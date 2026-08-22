"""Pool-standings strategy: play the STANDINGS, not the slate.

THE IDEA IN ONE PARAGRAPH
Maximising expected wins is the right goal only until roughly Week 14. After
that you are not trying to win the most games, you are trying to finish in a
paying position -- and those are different objectives. Your finish depends
on your score RELATIVE to the field, so what matters is the variance of
(your score - theirs). Pick what everyone else picks and that variance is
near zero: a lead is preserved and a deficit is frozen. Pick against them
and you create the swings that a deficit needs. So: when ahead, CONFORM;
when behind, DIVERGE. The cost of diverging is expected wins, and the
cheapest divergences are on games the model rates as coin flips -- where
surrendering EV costs almost nothing, because there was no edge to give up.

STATUS: UNVALIDATED, AND UNVALIDATABLE WITH WHAT WE HAVE. Every other claim
in this project is backed by a leak-free backtest. This one is not, and
cannot be yet: it needs historical pool standings and opponents' actual
weekly picks, which nobody recorded. The reasoning is standard
tournament-vs-cash game theory, the arithmetic below is deliberately simple
and inspectable, and it is a HEURISTIC. Do not describe it as backtested.
See PICKEM_MODEL.md section 6.

KNOWN WEAKNESS, stated plainly: `field_pct` comes from CBS's NATIONAL
community percentages, not from the 15-20 people Adam is actually playing.
A national 80/20 split can easily be 55/45 in a small pool, and the leverage
maths is only as good as that number. Better data exists -- the pool's own
picks are visible in the CBS app after each lock -- and feeding real pool
percentages in here would upgrade this from plausible to sound.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Weeks before which standings play is simply not worth it: too much season
# left for a lead to be meaningful or a deficit to be fatal, and the EV you
# surrender compounds over every remaining week.
STANDINGS_PLAY_FROM_WEEK = 14

# Payout structure of the TOO-GOODE pool: 1st $1200, 2nd $450, 3rd $300,
# 4th $150. Fourth still pays, so "get into the money" is a real fallback
# objective distinct from "win outright".
PAYING_PLACES = 4


@dataclass
class PoolState:
    """Everything the strategy needs to know about where Adam stands."""
    week: int
    weeks_remaining: int
    my_rank: int
    my_wins: int
    leader_wins: int
    n_players: int
    # wins behind the last paying place; 0 or negative means already in the money
    wins_behind_money: int = 0
    target_rank: int = 1

    @property
    def gap_to_leader(self) -> int:
        return max(0, self.leader_wins - self.my_wins)

    @property
    def in_the_money(self) -> bool:
        return self.my_rank <= PAYING_PLACES


@dataclass
class GameContext:
    """One game, from the strategy's point of view."""
    matchup: str
    model_side: str        # 'home' or 'away' -- what edge/pickem.py says
    model_prob: float      # its win probability for that side
    field_pct_on_model_side: float   # 0-100, share of the field agreeing with us

    @property
    def ev_cost_to_flip(self) -> float:
        """Expected wins surrendered by taking the other side.

        P(other side) ~ 1 - model_prob (ignoring pushes, which are ~2% and
        symmetric), so flipping costs model_prob - (1 - model_prob).
        A true coin flip costs zero -- which is exactly why coin flips are
        the right place to spend divergence.
        """
        return max(0.0, 2 * self.model_prob - 1)

    @property
    def leverage_if_flipped(self) -> float:
        """Share of the field (0-1) we separate from by flipping.

        Flipping away from a side 80% of the field is on separates us from
        those 80%. High leverage plus low EV cost is the ideal chase spot.
        """
        return self.field_pct_on_model_side / 100.0


@dataclass
class Recommendation:
    matchup: str
    side: str
    deviated: bool
    reason: str
    ev_cost: float = 0.0
    leverage: float = 0.0


def mode_for(state: PoolState) -> str:
    """'neutral' | 'protect' | 'chase' -- which game are we actually playing?"""
    if state.week < STANDINGS_PLAY_FROM_WEEK:
        return "neutral"
    if state.my_rank == 1 or (state.in_the_money and state.gap_to_leader == 0):
        return "protect"
    if state.my_rank <= state.target_rank:
        return "protect"
    return "chase"


def divergences_needed(state: PoolState) -> int:
    """How many contrarian picks per week a deficit requires.

    Reasoning, kept deliberately crude because precision here is false
    comfort: making up G wins needs swing, and n independent divergent picks
    produce a relative-score standard deviation of roughly sqrt(n). Wanting
    that swing to be on the order of G gives n ~ G^2, spread across the
    remaining weeks:

        per_week ~ G^2 / weeks_remaining

    A 3-win deficit with 6 weeks left is a gentle ~1-2 divergences a week; a
    6-win deficit with 2 weeks left demands ~18, i.e. nearly the whole slate
    -- which is correct. A deficit that large that late is close to hopeless
    and the only losing move is to play it safe.
    """
    if mode_for(state) != "chase":
        return 0
    gap = state.gap_to_leader if state.target_rank == 1 else max(0, state.wins_behind_money)
    if gap <= 0:
        return 0
    weeks = max(1, state.weeks_remaining)
    return max(1, math.ceil((gap ** 2) / weeks))


def _chase_priority(g: GameContext) -> float:
    """Leverage bought per unit of expected wins spent. Higher = flip first."""
    return g.leverage_if_flipped / (g.ev_cost_to_flip + 0.02)


def apply(state: PoolState, games: list[GameContext],
          max_ev_spend: float | None = None) -> list[Recommendation]:
    """Turn model picks into standings-aware picks.

    `max_ev_spend` caps the total expected wins surrendered in a week; the
    default scales with how desperate the situation is, and is a safety rail
    against the maths above demanding something reckless.
    """
    mode = mode_for(state)
    if mode == "neutral":
        return [Recommendation(g.matchup, g.model_side, False,
                               "pre-week-14: maximise expected wins")
                for g in games]

    if mode == "protect":
        return _protect(state, games)
    return _chase(state, games, max_ev_spend)


def _protect(state: PoolState, games: list[GameContext]) -> list[Recommendation]:
    """Ahead: shadow the field so nobody can gain ground on us cheaply.

    We only override the model where it disagrees with the majority AND the
    disagreement is nearly free (a coin flip). Overriding a genuine edge to
    match the crowd would hand back the thing that built the lead.
    """
    out = []
    for g in games:
        minority = g.field_pct_on_model_side < 50
        cheap = g.ev_cost_to_flip <= 0.02      # essentially a coin flip
        if minority and cheap:
            out.append(Recommendation(
                g.matchup, _flip(g.model_side), True,
                f"protecting lead: model is a coin flip and only "
                f"{g.field_pct_on_model_side:.0f}% of the field is with it -- "
                "side with the crowd so nobody gains ground",
                ev_cost=g.ev_cost_to_flip, leverage=g.leverage_if_flipped))
        else:
            why = ("model and field already agree" if not minority
                   else "model has a real edge here -- keep it, don't chase the crowd")
            out.append(Recommendation(g.matchup, g.model_side, False, why))
    return out


def _chase(state: PoolState, games: list[GameContext],
           max_ev_spend: float | None) -> list[Recommendation]:
    """Behind: buy variance where it is cheapest."""
    need = divergences_needed(state)
    budget = max_ev_spend if max_ev_spend is not None else 0.15 * need

    ranked = sorted(games, key=_chase_priority, reverse=True)
    chosen: set[str] = set()
    spent = 0.0
    for g in ranked:
        if len(chosen) >= need:
            break
        # never flip away from a strong edge; that is spending the model's
        # actual advantage to buy noise
        if g.ev_cost_to_flip > 0.12:
            continue
        if spent + g.ev_cost_to_flip > budget and chosen:
            continue
        chosen.add(g.matchup)
        spent += g.ev_cost_to_flip

    out = []
    for g in games:
        if g.matchup in chosen:
            out.append(Recommendation(
                g.matchup, _flip(g.model_side), True,
                f"chasing: {g.field_pct_on_model_side:.0f}% of the field is on the "
                f"model side, so flipping separates us from them for only "
                f"{g.ev_cost_to_flip:.0%} expected wins",
                ev_cost=g.ev_cost_to_flip, leverage=g.leverage_if_flipped))
        else:
            out.append(Recommendation(g.matchup, g.model_side, False,
                                      "kept: better value staying with the model here"))
    return out


def _flip(side: str) -> str:
    return "away" if side == "home" else "home"


def summarize(state: PoolState, recs: list[Recommendation]) -> str:
    mode = mode_for(state)
    dev = [r for r in recs if r.deviated]
    cost = sum(r.ev_cost for r in dev)
    lines = [
        f"mode: {mode.upper()}  (week {state.week}, rank {state.my_rank}/{state.n_players}, "
        f"{state.gap_to_leader} behind the leader, {state.weeks_remaining} weeks left)",
    ]
    if mode == "chase":
        lines.append(f"deficit maths: {state.gap_to_leader}^2 / {state.weeks_remaining} weeks "
                     f"=> ~{divergences_needed(state)} divergences wanted this week")
    lines.append(f"deviating on {len(dev)} of {len(recs)} games, "
                 f"costing ~{cost:.2f} expected wins")
    if dev:
        lines.append("  " + "; ".join(r.matchup for r in dev))
    return "\n".join(lines)
