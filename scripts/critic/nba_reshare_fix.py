"""Cycle 17 (shipped 2026-10-28; NBA_MINUTES_CRITIQUE_LOG.md) -- the test: re-share a limited Doubtful/Questionable player's minutes through the 240 step.
For each uncertain k: A (all dress), B_k (k sits), and for D/Q also L_k (k dresses, role x K).
m_i = A_i + sum_k (1-p_k)(B_k,i - A_i) + [D/Q] p_k (L_k,i - A_i);  own i: p_i x (L_i,i if D/Q else m_i)."""
import sys, numpy as np
from collections import defaultdict
sys.path.insert(0,'/home/asr/edge_search/scripts'); sys.path.insert(0,'/home/asr/edge_search')
import nba_minutes_lab as L
from edge import nba_minutes as NM
D=L.load(verbose=False); F=L.final_params(); st=L.report_status(D); rows=D["rows"]
SCALE={}
orig=L.game_roles
def patched(D_,g,R_,prm,mode):
    team,base,sr=orig(D_,g,R_,prm,mode)
    if SCALE:
        for j,pl in enumerate(team):
            if pl["id"] in SCALE: base[j]*=SCALE[pl["id"]]
    return team,base,sr
L.game_roles=patched
LIM=("Doubtful","Questionable")
def preds(season,mode,K,cut=15):
    R=L.roles(D,F); prev=L._prev_inactive(D); out=[]
    for g in D["games"]:
        if g["season"]!=season or not all(i in st for i in g["idx"]): continue
        truth={i:rows[i]["inactive"] for i in g["idx"]}
        cand=[i for i in g["idx"] if st[i]!="Out"]
        rank=lambda c:set(sorted(c,key=lambda i:(not rows[i]["starter"],-float(R["base"][i])))[:cut])
        keep=rank(cand)
        def cat(i):
            if mode=="confirmed" and rows[i]["starter"]: return None
            s=st[i]
            if s in LIM: return s
            if s in ("Probable","Available"): return "Probable"
            if prev[i] is True and rows[i]["team_game_no"]>1: return "stale"
            return None
        unc={i:(cat(i),float(R["base"][i])>=10) for i in keep if cat(i)}
        def proj(kept,scale=None):
            for i in g["idx"]: rows[i]["inactive"]=i not in kept
            SCALE.clear(); SCALE.update(scale or {})
            try: return L.project_game(D,g,R,F,mode)
            finally:
                SCALE.clear()
                for i in g["idx"]: rows[i]["inactive"]=truth[i]
        A=proj(keep)
        B={k:proj(rank([i for i in cand if i!=k])) for k in unc}
        Lk={k:proj(keep,{k:K[c[0]]}) for k,c in unc.items() if c[0] in LIM}
        members=set(keep)|{i for b in B.values() for i in b}|{i for i in g["idx"] if not truth[i]}
        for i in g["idx"]:
            if i not in members or (not truth[i] and rows[i]["actual"] is None): continue
            out.append(dict(a=A.get(i,0.0),
                terms=[(unc[k],B[k].get(i,0.0)-A.get(i,0.0),(Lk[k].get(i,0.0)-A.get(i,0.0)) if k in Lk else None) for k in unc if k!=i],
                own=unc.get(i), own_L=Lk[i].get(i,0.0) if i in Lk else None,
                dressed=not truth[i], y=float(rows[i]["actual"]) if not truth[i] else 0.0))
    return out
def score(P,p,K,reshare):
    m=[];own=[]
    for r in P:
        v=r["a"]
        for c,db,dl in r["terms"]:
            v+=(1-p[c])*db
            if reshare and dl is not None: v+=p[c]*dl
        if r["own"] and r["own"][0] in LIM:
            v=(r["own_L"]+sum((1-p[c])*db for c,db,_ in r["terms"])) if reshare else v*K[r["own"][0]]
        m.append(v); own.append(p[r["own"]] if r["own"] else 1.0)
    m=np.array(m);own=np.array(own);y=np.array([r["y"] for r in P]);dr=np.array([r["dressed"] for r in P])
    eb=own*m-y; ea=(m-y)[dr]
    return np.sqrt((ea**2).mean()),np.abs(ea).mean(),np.sqrt((eb**2).mean()),float(ea.mean())
for mode in ("confirmed","inactives"):
    p=L.report_fit(L.report_preds(D,F,"2024-25",mode,st))[0]
    best=None
    for f in (0.8,0.9,1.0,1.1,1.2):
        K={**NM.REPORT_K[mode],"Questionable":NM.REPORT_K[mode]["Questionable"]*f}
        b=score(preds("2024-25",mode,K),p,K,True)
        print(f"{mode} train K_Q x{f} ({K['Questionable']:.3f}): RMSE {b[0]:.4f} MAE {b[1]:.4f}",flush=True)
        if best is None or b[0]<best[0]: best=(b[0],f,K)
    _,f,K=best
    b=score(preds("2025-26",mode,K),p,K,True)
    print(f"{mode} TEST chosen x{f}: RMSE {b[0]:.4f} MAE {b[1]:.4f} board {b[2]:.4f} bias {b[3]:+.3f}",flush=True)
