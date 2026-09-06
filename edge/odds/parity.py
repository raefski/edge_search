"""Measure the paid path against the free one, in the units the models use.

WHY THIS IS A COMPONENT AND NOT A ONE-OFF SCRIPT
The requirement for this migration is "wean off the Odds API without
compromising prediction accuracy". That is a claim about model output, and the
only honest way to hold it is to measure it -- on real slates, repeatedly,
in the units the model actually consumes.

WHAT IS ACTUALLY AT RISK, in order
1. COVERAGE, not price. A pitcher whose props were not scraped has no
   projection at all and vanishes from the pool. That is a far larger error
   than being two cents off on his strikeout price, and it is invisible in any
   average taken over the players that DID match. Coverage is therefore
   reported first and separately.
2. BOOK COUNT. Pick'em's consensus came from ~10 books and now comes from 3.
   edge/pickem_free.py's own docstring is honest about this: most of the gain
   from averaging arrives by the third or fourth book. Three is on the flat
   part of that curve, which is an argument, not a measurement -- this is the
   measurement.
3. PRICE LEVEL. Bias (systematic offset) and dispersion (noise) are separated,
   because they have different consequences: pick'em reads a line MOVE, so a
   constant offset cancels and noise does not.

READ THE COVERAGE NUMBER FIRST. A tighter MAE on a smaller matched set is
worse, not better, and this repo has been caught by exactly that shape before
(HANDOFF.md section 8: "when a number moves the RIGHT way after a change,
check it too").
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class Divergence:
    """One quantity compared across the two sources."""
    unit: str
    n_paid: int = 0
    n_free: int = 0
    n_matched: int = 0
    diffs: list[float] = field(default_factory=list)   # free - paid

    @property
    def coverage(self) -> float:
        """Share of the paid source's items the free one also produced.

        The headline number. Reported before any error statistic, because an
        error statistic over the matched subset says nothing about what was
        dropped.
        """
        return self.n_matched / self.n_paid if self.n_paid else 0.0

    @property
    def bias(self) -> float | None:
        """Mean signed difference. Cancels out of any model reading a MOVE."""
        return statistics.fmean(self.diffs) if self.diffs else None

    @property
    def mae(self) -> float | None:
        return statistics.fmean(map(abs, self.diffs)) if self.diffs else None

    @property
    def p90(self) -> float | None:
        if not self.diffs:
            return None
        s = sorted(map(abs, self.diffs))
        return s[min(len(s) - 1, int(0.9 * len(s)))]

    @property
    def worst(self) -> float | None:
        return max(map(abs, self.diffs), default=None)

    def as_dict(self) -> dict:
        return {"unit": self.unit, "n_paid": self.n_paid, "n_free": self.n_free,
                "n_matched": self.n_matched, "coverage": round(self.coverage, 3),
                "bias": None if self.bias is None else round(self.bias, 3),
                "mae": None if self.mae is None else round(self.mae, 3),
                "p90": None if self.p90 is None else round(self.p90, 3),
                "worst": None if self.worst is None else round(self.worst, 3)}


def compare_keyed(paid: dict[str, float], free: dict[str, float], unit: str) -> Divergence:
    """Compare two {key: value} readings of the same quantity."""
    d = Divergence(unit=unit, n_paid=len(paid), n_free=len(free))
    for k, pv in paid.items():
        fv = free.get(k)
        if fv is None or pv is None:
            continue
        d.n_matched += 1
        d.diffs.append(fv - pv)
    return d


# --- pick'em ----------------------------------------------------------------

def compare_pickem(paid_games, free_games) -> dict[str, Divergence]:
    """Diverence in home spread and game total, per fixture.

    Takes two lists of edge.pickem_live.LiveGame. Keyed on (away, home)
    abbreviations, which both paths derive from the same TEAM_NAME_TO_ABBR map,
    so a key miss is a genuinely missing fixture rather than a naming artefact.
    """
    def keyed(games, attr):
        return {f"{g.away_abbr}@{g.home_abbr}": getattr(g, attr)
                for g in games if getattr(g, attr) is not None}

    out = {
        "home_spread": compare_keyed(keyed(paid_games, "live_line"),
                                     keyed(free_games, "live_line"), "points"),
        "total": compare_keyed(keyed(paid_games, "total"),
                               keyed(free_games, "total"), "points"),
    }
    # Book count is the mechanism behind any dispersion above, so report it
    # rather than leaving the reader to infer it.
    out["n_books"] = compare_keyed(keyed(paid_games, "n_books"),
                                   keyed(free_games, "n_books"), "books")
    return out


# --- DFS --------------------------------------------------------------------

def compare_dfs_pitchers(paid_client, free_client, sport: str, markets: list[str],
                         book: str = "draftkings") -> dict[str, Divergence]:
    """Divergence in projected DK fantasy points per pitcher, end to end.

    Runs edge.dfs.project_pitcher over both sources so the comparison is in the
    unit the optimiser actually consumes, not in raw prices. A price difference
    that does not move a projection does not matter; one that moves it by two
    points reorders the pool.
    """
    from edge import dfs

    def project_all(client) -> tuple[dict[str, float], dict[str, float]]:
        projections: dict[str, float] = {}
        k_means: dict[str, float] = {}
        try:
            events = client.get_events(sport)
        except Exception:
            return projections, k_means
        for ev in events:
            try:
                payload = client.get_event_odds(sport, ev["id"], markets, "us")
            except Exception:
                continue
            bk = next((b for b in payload.get("bookmakers", [])
                       if b["key"] == book), None)
            if not bk:
                continue
            names = {o["description"] for m in bk["markets"]
                     for o in m["outcomes"] if o.get("description")}
            for name in names:
                res = dfs.project_pitcher(dfs.player_markets(bk, name))
                if res["proj"] is not None:
                    projections[dfs.norm(name)] = res["proj"]
                    if res.get("k_mean") is not None:
                        k_means[dfs.norm(name)] = res["k_mean"]
        return projections, k_means

    paid_proj, paid_k = project_all(paid_client)
    free_proj, free_k = project_all(free_client)
    return {
        "pitcher_projection": compare_keyed(paid_proj, free_proj, "DK pts"),
        "pitcher_k_mean": compare_keyed(paid_k, free_k, "strikeouts"),
    }


# --- reporting --------------------------------------------------------------

def report(results: dict[str, Divergence], title: str = "") -> str:
    lines = []
    if title:
        lines += [title, "=" * len(title)]
    lines.append(f"{'quantity':<20} {'cover':>7} {'paid':>6} {'free':>6} "
                 f"{'bias':>8} {'MAE':>8} {'p90':>8} {'worst':>8}  unit")
    for name, d in results.items():
        def f(v):
            return "   --   " if v is None else f"{v:8.3f}"
        lines.append(
            f"{name:<20} {d.coverage:>6.1%} {d.n_paid:>6} {d.n_free:>6} "
            f"{f(d.bias)} {f(d.mae)} {f(d.p90)} {f(d.worst)}  {d.unit}")
    lines.append("")
    lines.append("Coverage is the number that matters first: a better MAE over "
                 "fewer matched items is a worse result, not a better one.")
    return "\n".join(lines)
