# Cycle 18 recency fit (2024-25) + variants. Run from repo root: python3 scripts/critic/nba_recency_fix/fit.py data/cache/nba_minutes_lab/cycle18_preds.pkl
"""Cycle 18: recency term c[state, starter] x (last-3 played this season - m), zero-sum per team-game.
Fit on 2024-25 only; starter flag in 'inactives' must be pre-lock (srate >= 0.5)."""
import sys, pickle, bisect, collections, numpy as np
sys.path.insert(0, '/home/asr/edge_search/scripts'); sys.path.insert(0, '/home/asr/edge_search')
import nba_minutes_lab as L
D = L.load(verbose=False); rows = D['rows']
res = pickle.load(open(sys.argv[1], 'rb'))
hist = collections.defaultdict(list)
for i, r in enumerate(rows):
    if not r['inactive'] and r['actual'] is not None :
        hist[r['pid']].append((r['date'], r['actual'], r['season']))
for v in hist.values(): v.sort()

def last3(i):
    r = rows[i]; h = hist[r['pid']]; k = bisect.bisect_left(h, (r['date'],))
    prev = [p for p in h[max(0, k - 3):k] if p[2] == r['season']]
    return np.mean([p[1] for p in prev]) if prev else None

def arrays(mode, s, starter_src, skip_unc, cap):
    o = res[(mode, s)]
    x, y, st, tg, v0 = [], [], [], [], []
    for i, v, yy, own, srate, base in o:
        l3 = last3(i)
        xi = 0.0 if l3 is None else l3 - v
        if skip_unc and own: xi = 0.0
        if cap: xi = max(-cap, min(cap, xi))
        sf = rows[i]['starter'] if (starter_src == 'actual' or mode == 'confirmed') else srate >= 0.5
        x.append(xi); y.append(yy); st.append(bool(sf)); tg.append((rows[i]['gid'], rows[i]['team'])); v0.append(v)
    return [np.array(a) if k != 3 else a for k, a in enumerate((x, y, st, tg, v0))]

def apply(c, x, st, tg, v0):
    corr = np.where(st, c[True], c[False]) * x
    g = collections.defaultdict(list)
    for j, k in enumerate(tg): g[k].append(j)
    for js in g.values(): corr[js] -= corr[js].mean()
    return np.maximum(0.0, v0 + corr)

def score(m, y): e = m - y; return f"{np.sqrt((e**2).mean()):.4f}/{np.abs(e).mean():.4f}/{e.mean():+.3f}"

for name, ss, sk, cap in (('critic (actual starter)', 'actual', False, 0), ('pre-lock starter', 'prelock', False, 0),
                          ('+skip uncertain', 'prelock', True, 0), ('+skip unc, cap 12', 'prelock', True, 12)):
    for mode in ('confirmed', 'inactives'):
        x, y, st, tg, v0 = arrays(mode, '2024-25', ss, sk, cap); r = y - v0
        c = {b: float((x[st == b] @ r[st == b]) / (x[st == b] @ x[st == b])) for b in (True, False)}
        out = []
        for s in ('2024-25', '2025-26'):
            x, y, st, tg, v0 = arrays(mode, s, ss, sk, cap)
            out.append(f"{s} {score(v0, y)} -> {score(apply(c, x, st, tg, v0), y)}")
        print(f"{name:26s} {mode:9s} c_start {c[True]:.3f} c_bench {c[False]:.3f} | " + " | ".join(out), flush=True)
print('--- MAE-grid fit on 2024-25 (zeros kept, pre-lock starter, no skip)')
for mode in ('confirmed','inactives'):
    x,y,st,tg,v0=arrays(mode,'2024-25','prelock',False,0)
    best=min(((a,b) for a in np.arange(0,0.31,0.02) for b in np.arange(0,0.31,0.02)),key=lambda c:np.abs(apply({True:c[0],False:c[1]},x,st,tg,v0)-y).mean())
    c={True:best[0],False:best[1]}
    print(mode,c,[f"{s} {score(arrays(mode,s,'prelock',False,0)[4],arrays(mode,s,'prelock',False,0)[1])} -> {score(apply(c,*[arrays(mode,s,'prelock',False,0)[k] for k in (0,2,3,4)]),arrays(mode,s,'prelock',False,0)[1])}" for s in ('2024-25','2025-26')],flush=True)
