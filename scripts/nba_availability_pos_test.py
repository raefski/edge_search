"""Cycle 1 (availability): positional transfer of a scratch's minutes on top of rank_gap.
After rank_gap, each active player whose position overlaps a scratched regular's gets
k * (scratched minutes in his groups) * w_i / sum(w over overlapping actives), w_i = his
slope (rank-gap cell; bench ranks have larger slopes); the team is then closed back to 240
by taking the same amount from non-overlapping players in proportion to their slopes."""
import sys; sys.path.insert(0, "/home/asr/edge_search"); sys.path.insert(0, "/home/asr/edge_search/scripts")
import nba_minutes_lab as L
from edge import nba_minutes as NM
orig = L._rank_gap
K = {"k": 0.0}
def patched(team, base, coef):
    out = orig(team, base, coef)
    k = K["k"]
    if not k: return out
    role = {pl["id"]: b for pl, b in zip(team, base)}
    lost = {}
    for pl in team:
        if pl["inactive"] and role[pl["id"]] >= NM.REGULAR_MIN:
            for g in L._groups(pl["position"]): lost[g] = lost.get(g, 0) + role[pl["id"]]
    if not lost: return out
    act = [pl for pl in team if not pl["inactive"]]
    ov = {pl["id"]: sum(v for g, v in lost.items() if g in L._groups(pl["position"])) for pl in act}
    # receivers: overlapping players outside the top 5 roles (the bench), weight = role
    top = set(sorted(out, key=lambda i: -role[i])[:5])
    rec = {i: ov[i] for i in ov if ov[i] > 0 and i not in top}
    if not rec: return out
    give = {i: k * rec[i] * max(role[i], 1.0) / sum(max(role[j], 1.0) for j in rec if ov[j] == ov[i]) for i in rec}
    tot = sum(give.values())
    donors = {i: out[i] for i in out if i not in rec and out[i] > 0}
    ds = sum(donors.values()) or 1
    o = dict(out)
    for i, g in give.items(): o[i] = min(48.0, o[i] + g)
    for i, m in donors.items(): o[i] = max(0.0, o[i] - tot * m / ds)
    return o
L._rank_gap = patched
D = L.load(verbose=False)
prm = L.final_params()
for mode in ("confirmed", "inactives"):
    for k in (0.0, 0.05, 0.1, 0.2, 0.3):
        K["k"] = k
        r = L.evaluate(D, prm, mode, seasons=("2024-25", "2025-26", "2026-27"))
        f = lambda s, sl: (r[s][sl]["rmse"], r[s][sl]["bias"], r[s][sl]["n"]) if sl in r.get(s, {}) else None
        print(mode, k, {s: (round(f(s, "all")[0], 4), [round(x, 3) for x in f(s, "teammate of a scratch")]) for s in r}, flush=True)
