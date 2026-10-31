#!/usr/bin/env python3
"""NBA lineup-construction backtest vs real contests (written 2026-10-30, overnight review).

    python3 scripts/nba_construction_backtest.py [out_dir]

Rebuilds each NBA contest's lineups from the logged board for its DK draft group under
alternative constructions (cash: 10th/30th/50th pct, team caps 2-4, min-minutes floors;
GPP: 90th/95th/99th pct, ownership fades, game stacks, team caps) and scores them on that
contest's own player fpts (contests.db). Cash: share clearing the cash line + margin; GPP:
finishing percentile + ROI. Also compares top-1% GPP lineups with the field.
Result 10/20-10/29 (10 cash slates / 12 GPP slates / 32 contests): nothing beat shipped
(CASH_PCT 30, GPP_PCT 90, own_weight 0, no caps); shipped cash missed the line 7 of 10 by
25-78 pts -- projection error, not construction. Re-run at ~25 slates on the at-lock boards
in dfs_proj_history_nba.csv (cap 4 cash, g99, 4+ game stack were the candidates).
"""
import sys, sqlite3, json, collections
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from edge import dfs_nba_theory as theory, dfs_opt_nba as opt, dfs_opt_climb as climb
from edge.names import norm
R = str(__import__('pathlib').Path(__file__).resolve().parents[1]) + '/'
OUT = sys.argv[1] if len(sys.argv) > 1 else '.'
db = sqlite3.connect(R + 'data/contests.db')
log = pd.read_csv(R + 'data/dfs_proj_log_nba.csv')
contests = pd.read_sql("select * from contest where sport='NBA' and slate_date>='2026-10-01' and final=1", db)

def team_cap(k): return lambda rows: max(collections.Counter(r['team'] for r in rows).values()) <= k
def game_stack(k): return lambda rows: max(collections.Counter(r['game'] for r in rows).values()) >= k
CASH = {'c30_shipped': dict(pct=30), 'c50_median': dict(pct=50), 'c10_floor': dict(pct=10),
        'c30_cap2': dict(pct=30, ev=team_cap(2)), 'c30_cap3': dict(pct=30, ev=team_cap(3)),
        'c30_cap4': dict(pct=30, ev=team_cap(4)), 'c30_min20': dict(pct=30, minmin=20),
        'c30_min24': dict(pct=30, minmin=24), 'c50_cap3': dict(pct=50, ev=team_cap(3))}
GPP = {'g90_shipped': dict(pct=90), 'g95': dict(pct=95), 'g99': dict(pct=99),
       'g90_own3': dict(pct=90, ow=3), 'g90_own6': dict(pct=90, ow=6), 'g90_own12': dict(pct=90, ow=12),
       'g90_game4': dict(pct=90, ev=game_stack(4)), 'g90_game5': dict(pct=90, ev=game_stack(5)),
       'g90_cap3': dict(pct=90, ev=team_cap(3)), 'g95_own6': dict(pct=95, ow=6), 'c30_in_gpp': dict(pct=30)}

def board(gid):
    b = log[log.gid == gid]
    pool = []
    for r in b.to_dict('records'):
        r['game'] = '@'.join(sorted([str(r['team']), str(r['opp'])]))
        r['starter'] = 1 if r.get('starter') == 1 else 0
        r['key'] = norm(r['player']); r['own'] = float(r['own'] or 0)
        pool.append(r)
    return pool

def build(pool, sim, spec, seed=0):
    excl = {p['player'] for p in pool if spec.get('minmin') and (p['minutes'] or 0) < spec['minmin']}
    for p in pool: p['name'] = p['player']
    opt.prepare(pool)
    key = 'ceil' if spec['pct'] >= 50 else 'floor'
    idx = climb.search(pool, sim, opt.NBA, spec['pct'], key=key, iters=40, seed=seed,
                       own_weight=spec.get('ow', 0.0), exclude=excl, extra_valid=spec.get('ev'))
    return idx

def feats(rows, own_of, sal_of, game_of, team_of):
    sal = [sal_of.get(k, 0) for k in rows]
    return dict(sal_left=50000 - sum(sal), n_cheap=sum(s < 4500 for s in sal),
                own_sum=sum(own_of.get(k, 0) for k in rows),
                max_team=max(collections.Counter(team_of.get(k) for k in rows).values()),
                max_game=max(collections.Counter(game_of.get(k) for k in rows).values()))

res, wins = [], []
for gid, cs in contests.groupby('draft_group'):
    pool = board(gid)
    if len(pool) < 20: print('no board', gid); continue
    sim = theory.simulate(pool, n_sims=3000, seed=0); theory.summarise(sim, pool)
    lineups = {}
    for name, spec in {**CASH, **GPP}.items():
        idx = build(pool, sim, spec)
        if idx: lineups[name] = [pool[i]['key'] for i in idx]
    lg = pd.read_csv(R + f"data/dfs_lineups_nba_{pool[0]['date']}.csv")
    lg = lg[lg.gid == gid]
    for m in ('cash', 'gpp'):
        if len(lg[lg['mode'] == m]) == 8: lineups[f'logged_{m}'] = [norm(x) for x in lg[lg['mode'] == m].player]
    sal_of = {p['key']: p['salary'] for p in pool}; game_of = {p['key']: p['game'] for p in pool}
    team_of = {p['key']: p['team'] for p in pool}
    for c in cs.to_dict('records'):
        cid = c['contest_id']
        act = dict(db.execute('select player_norm,fpts from player_result where contest_id=?', (cid,)).fetchall())
        own = dict(db.execute('select player_norm,pct_drafted from player_result where contest_id=?', (cid,)).fetchall())
        pts = np.array(sorted([r[0] for r in db.execute('select points from entry where contest_id=?', (cid,))]))
        pay = db.execute('select rank_lo,rank_hi,amount from payout where contest_id=?', (cid,)).fetchall()
        line = float(db.execute('select min(points) from entry where contest_id=? and rank<=?', (cid, c['places_paid'])).fetchone()[0])
        for name, keys in lineups.items():
            s = sum(act.get(k, 0.0) for k in keys)
            rank = 1 + int((pts > s).sum())
            pv = sum(a for lo, hi, a in pay if lo <= rank <= hi)
            res.append(dict(gid=gid, date=c['slate_date'], cid=cid, type=c['type'], field=len(pts), fee=c['entry_fee'],
                            cons=name, score=s, line=line, margin=s - line, cashed=s >= line,
                            pctile=(pts < s).mean(), payout=pv, missing=sum(k not in act for k in keys),
                            **feats(keys, own, sal_of, game_of, team_of)))
        # winners: top 1% (min 1) entries of GPP
        if c['type'] == 'gpp':
            top = db.execute('select entry_id from entry where contest_id=? order by points desc limit ?', (cid, max(1, len(pts)//100))).fetchall()
            allp = collections.defaultdict(list)
            for eid, k in db.execute('select entry_id,player_norm from entry_player where contest_id=?', (cid,)): allp[eid].append(k)
            for (eid,) in top:
                if len(allp[eid]) == 8: wins.append(dict(gid=gid, cid=cid, **feats(allp[eid], own, sal_of, game_of, team_of)))
            for eid, ks in allp.items():
                if len(ks) == 8: wins.append(dict(gid=gid, cid=cid, field_all=1, **feats(ks, own, sal_of, game_of, team_of)))
pd.DataFrame(res).to_csv(OUT + '/results.csv', index=False)
pd.DataFrame(wins).to_csv(OUT + '/winners.csv', index=False)
print('done', len(res))
