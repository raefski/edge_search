"""Critic cycle 9: inactives state ignores who inherits a scratched starter's START.
Fit P(start) logistic on 2024-25 (inactives rows), role = p*ms + (1-p)*mb, gap refit on 2024-25,
score both seasons; paired SE by team-game on the teammate-of-scratch slice."""
import sys, numpy as np, pickle
from collections import defaultdict
sys.path.insert(0,'/home/asr/edge_search'); sys.path.insert(0,'/home/asr/edge_search/scripts')
import nba_minutes_lab as L
from edge import nba_minutes as NM
D=L.load(verbose=False); F=L.final_params(); R=L.roles(D,F); rows=D["rows"]
M,S=D["M"],D["S"]
def feats(g):
    idx=g["idx"]; out=[i for i in idx if rows[i]["inactive"]]
    lostg=defaultdict(float)
    for j in out:
        for gr in L._groups(rows[j]["pos"]): lostg[gr]+=float(R["srate"][j])
    act=[i for i in idx if not rows[i]["inactive"]]
    open_=max(0.0,5-sum(float(R["srate"][i]) for i in act))
    X={}
    for i in act:
        sr=float(R["srate"][i]); last=0.0 if np.isnan(M[i,-1]) else float(S[i,-1])
        vac=min(1.0,max([lostg.get(gr,0) for gr in L._groups(rows[i]["pos"])]+[0]))
        # rank of i by srate among actives sharing a group with a vacated start
        X[i]=[1,sr,last,vac*(1-sr),open_*(1-sr),float(R["base"][i])/36]
    return X
# fit logistic on 2024-25
Xs,ys=[],[]
for g in D["games"]:
    if g["season"]!="2024-25": continue
    for i,x in feats(g).items(): Xs.append(x); ys.append(1.0 if rows[i]["starter"] else 0.0)
X=np.array(Xs); y=np.array(ys); w=np.zeros(X.shape[1])
for _ in range(30):
    p=1/(1+np.exp(-X@w)); H=(X*(p*(1-p))[:,None]).T@X+1e-6*np.eye(len(w)); w+=np.linalg.solve(H,X.T@(y-p))
p=1/(1+np.exp(-X@w)); print("coef",w.round(3),"train logloss",float(-(y*np.log(p)+(1-y)*np.log(1-p)).mean()))
P0=1/(1+np.exp(-X[:,:2]@np.linalg.lstsq(X[:,:2],y,rcond=None)[0]))
PS={}
for g in D["games"]:
    if g["season"] in ("2024-25","2025-26"):
        for i,x in feats(g).items(): PS[i]=1/(1+np.exp(-np.dot(x,w)))
orig=L.game_roles
def patched(D_,g,R_,prm,mode):
    team,base,sr=orig(D_,g,R_,prm,mode)
    if mode=="inactives" and prm.get("pstart"):
        for k,pl in enumerate(team):
            i=pl["id"]
            if pl["inactive"] or i not in PS or (R_["ns"][i]+R_["nb"][i])<=0: continue
            r=rows[i]
            if r["real_pos_games"]<prm["pre_if"] or r["nba_games"]<5 or r["since_return"] is not None: continue
            p=PS[i]; sr0=float(R_["srate"][i])
            # replace the start-rate mix in base by p: base*(ms*p+mb*(1-p))/(ms*sr0+mb*(1-sr0))
            den=float(R_["ms"][i])*sr0+float(R_["mb"][i])*(1-sr0)
            if den>1: base[k]=base[k]*(float(R_["ms"][i])*p+float(R_["mb"][i])*(1-p))/den
            pl["starting"]=p; sr[k]=p
    return team,base,sr
L.game_roles=patched
def preds(prm):
    out={}
    for g in D["games"]:
        if g["season"] not in ("2024-25","2025-26"): continue
        pr=L.project_game(D,g,R,prm,"inactives")
        sc=any(rows[i]["inactive"] and R["base"][i]>=NM.REGULAR_MIN for i in g["idx"])
        for i in g["idx"]:
            r=rows[i]
            if r["inactive"] or r["actual"] is None: continue
            out[i]=(g["season"],id(g),sc,bool(r["starter"]) and R["srate"][i]<0.5,pr.get(i,0)-r["actual"])
    return out
A=preds(F)
P={**F,"pstart":True}; P["gap_coef"]={**F["gap_coef"],"inactives":L.fit_gap(D,P)["inactives"]}
B=preds(P)
pickle.dump((A,B),open('fix_preds.pkl','wb'))
for s in ("2024-25","2025-26"):
  for name,f in (("all",lambda v:True),("teammate of scratch",lambda v:v[2]),("promoted starter",lambda v:v[3])):
    ks=[k for k,v in A.items() if v[0]==s and f(v)]
    ea=np.array([A[k][4] for k in ks]); eb=np.array([B[k][4] for k in ks])
    gid=defaultdict(float)
    for k,a,b in zip(ks,ea,eb): gid[A[k][1]]+=b*b-a*a
    dd=np.array(list(gid.values())); se=dd.std()*np.sqrt(len(dd))/len(ks)
    print(f"{s} {name:20s} n={len(ks)} games={len(dd)} RMSE {np.sqrt((ea**2).mean()):.3f}->{np.sqrt((eb**2).mean()):.3f} "
          f"MSE d {(eb**2-ea**2).mean():+.3f} (se {se:.3f}) MAE {np.abs(ea).mean():.3f}->{np.abs(eb).mean():.3f} bias {ea.mean():+.2f}->{eb.mean():+.2f}")
