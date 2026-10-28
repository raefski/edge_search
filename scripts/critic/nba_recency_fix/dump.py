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
            out.append(dict(i=i,a=A.get(i,0.0),
                terms=[(unc[k],B[k].get(i,0.0)-A.get(i,0.0),(Lk[k].get(i,0.0)-A.get(i,0.0)) if k in Lk else None) for k in unc if k!=i],
                own=unc.get(i), own_L=Lk[i].get(i,0.0) if i in Lk else None,
                dressed=not truth[i], y=float(rows[i]["actual"]) if not truth[i] else 0.0))
    return out
import pickle
LIM=("Doubtful","Questionable")
res={}; R0=L.roles(D,F)
for mode in ("confirmed","inactives"):
    p=L.report_fit(L.report_preds(D,F,"2024-25",mode,st))[0]
    K=NM.REPORT_K[mode]
    for s in ("2024-25","2025-26"):
        P=preds(s,mode,K); o=[]
        for r in P:
            v=r["a"]
            for c,db,dl in r["terms"]:
                v+=(1-p[c])*db
                if dl is not None: v+=p[c]*dl
            if r["own"] and r["own"][0] in LIM: v=r["own_L"]+sum((1-p[c])*db for c,db,_ in r["terms"])
            o.append((r["i"],v,r["y"],r["own"],float(R0["srate"][r["i"]]),float(R0["base"][r["i"]]),r["dressed"],p[r["own"]] if r["own"] else 1.0))
        res[(mode,s)]=o
        e=np.array([a[1]-a[2] for a in o if a[6]]); print(mode,s,len(e),np.sqrt((e**2).mean()),np.abs(e).mean(),e.mean(),flush=True)
pickle.dump(res,open("data/cache/nba_minutes_lab/cycle18_preds.pkl","wb"))
