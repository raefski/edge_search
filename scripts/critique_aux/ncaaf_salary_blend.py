import sys; sys.path[:0]=["/home/asr/edge_search/scripts","/home/asr/edge_search"]
import numpy as np, ncaaf_lineup_lab as L
from edge.ncaaf import base_position
B,F,T=L.boards(),L.fields(),L.dk_points()
data=L.player_rows(B,T)  # date,pos,proj,salk,actual
W=(0.0,0.25,0.5,0.75,1.0)
def ppk(train):
    out={}
    for pos in ("QB","RB","WR"):
        rs=[r for r in train if r[1]==pos and r[2]>=3]  # players with real roles
        out[pos]=sum(r[4] for r in rs)/sum(r[3] for r in rs) if rs else None
    return out
def blend(ps,k,w):
    o=[]
    for p in ps:
        c=k.get(base_position(p["dk_pos"]))
        pr=p["proj"] if c is None else (1-w)*p["proj"]+w*c*p["salary"]/1000*(1.0 if p["proj"]>=3 else p["proj"]/3)
        o.append(dict(p,proj=round(pr,2)))
    return o
base=L.rows(L.theory.Z_CASH,("cash",),B,F,T); bmap={r["contest"]:r for r in base}
dates=sorted({r["date"] for r in base})
alld=sorted({d[0] for d in data})
res={}
for w in W:
  for d in dates:
    for mode in ("fwd","loso"):
      tr=[r for r in data if (r[0]<d if mode=="fwd" else r[0]!=d)]
      k=ppk(tr)
      Bd={g:blend(ps,k,w) for g,ps in B.items() if ps[0]["date"]==d}
      res[(w,d,mode)]=L.rows(L.theory.Z_CASH,("cash",),Bd,F,T)
def pr(rs,lab): print(L.summary(rs,lab,bmap))
print(L.HEAD); pr(base,"shipped")
for w in W:
  for mode in ("fwd","loso"):
    pr(sum((res[(w,d,mode)] for d in dates),[]),f"w={w} {mode}")
# w chosen: fwd=on earlier slates' lineup outcome; loso = on other slates
for mode in ("fwd","loso"):
  held=[]
  for d in dates:
    prev=[x for x in dates if (x<d if mode=="fwd" else x!=d)]
    def sc(w):
      rs=sum((res[(w,x,mode)] for x in prev),[])
      return (np.mean([r["pctile"] for r in rs]) if rs else 0, w==0)
    wb=max(W,key=sc); held+=res[(wb,d,mode)]; print(mode,d,wb)
  pr(held,f"w chosen {mode}")
for w in (0.5,):
  for r in sum((res[(w,d,"fwd")] for d in dates),[]): print(r["date"],r["contest"],round(r["points"],1),round(r["pctile"],2),r["cashed"],round(bmap[r["contest"]]["pctile"],2))
