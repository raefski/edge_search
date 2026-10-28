# Cycle 18: run dump.py, then score.py (production-faithful), from the repo root.
"""Production-faithful score of cycle 18: zero-sum over every kept row with v > 0 (dressed
or not -- unknown pre-lock), score dressed rows. c refit on 2024-25 dressed rows."""
import sys, pickle, collections, numpy as np
sys.path.insert(0, '/home/asr/edge_search/scripts'); sys.path.insert(0, '/home/asr/edge_search')
exec(open('scripts/critic/nba_recency_fix/fit.py').read().split("def arrays")[0].replace("res = pickle.load(open(sys.argv[1], 'rb'))", "res = pickle.load(open('data/cache/nba_minutes_lab/cycle18_preds.pkl','rb'))"))
from edge import nba_minutes as NM
def run(mode, s, c):
    o = res[(mode, s)]; g = collections.defaultdict(list)
    for t in o: g[(rows[t[0]]['gid'], rows[t[0]]['team'])].append(t)
    v0, v1, y = [], [], []
    for ts in g.values():
        corr = {}
        for i, v, yy, own, srate, base, dr, p in ts:
            if v <= 0: continue
            l3 = last3(i)
            st = rows[i]['starter'] if mode == 'confirmed' else srate >= 0.5
            corr[i] = 0.0 if l3 is None else c[st] * (l3 - v)
        mu = np.mean(list(corr.values())) if corr else 0.0
        for i, v, yy, own, srate, base, dr, p in ts:
            if not dr: continue
            v0.append(v); y.append(yy); v1.append(max(0.0, v + corr[i] - mu) if i in corr else v)
    v0, v1, y = map(np.array, (v0, v1, y)); f = lambda m: f"{np.sqrt(((m-y)**2).mean()):.4f}/{np.abs(m-y).mean():.4f}/{(m-y).mean():+.3f}"
    return f"{s} n{len(y)} {f(v0)} -> {f(v1)}"
for mode in ('confirmed', 'inactives'):
    c = dict(zip((True, False), NM.RECENT_C[mode]))
    print(mode, c, ' | '.join(run(mode, s, c) for s in ('2024-25', '2025-26')), flush=True)
