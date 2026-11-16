"""2017 attachment 7 counters. Additive only; legacy scores stay with their producers."""
import numpy as np

REVISION = 'guifan-2017-v1'
THRESHOLDS = {1: [.1,1,3,5,10,20,30,50], 3: [.1,1,3,10,20,30,50,70],
              6: [.1,1,5,10,25,50,100], 12: [.1,1,5,10,25,50,100],
              24: [.1,10,25,50,100,250]}
BOUNDS = {3: [.1,3,10,20,50,70], 24: [.1,10,25,50,100,250]}
LABELS = ['小雨','中雨','大雨','暴雨','大暴雨','特大暴雨']


def metadata(variable=None, hours=None):
    if variable == 't':
        return dict(has_acc=True)
    if hours:
        return dict(has_trace=True, **(dict(grades=[dict(id=i+1,label=label,
            range=f'[{lo:g}, {BOUNDS[hours][i+1]:g}) mm' if i<5 else f'≥{lo:g} mm')
            for i,(label,lo) in enumerate(zip(LABELS,BOUNDS[hours]))]) if hours in BOUNDS else {}))
    return {}


def accuracy(fc, ob):
    e = np.abs(np.asarray(fc)-np.asarray(ob))
    return dict(N1=int((e<=1+1e-6).sum()), N2=int((e<=2+1e-6).sum()))


def rain_rows(base, fc, ob, hours, trace=None, thresholds=None):
    fc,ob = np.asarray(fc),np.asarray(ob)
    tiny = (ob>0)&(ob<.1) if trace is None else np.asarray(trace,dtype=bool)&(ob<.1)
    grade_counts={}
    if hours in BOUNDS:
        gf,go=np.searchsorted(BOUNDS[hours],fc,side='right'),np.searchsorted(BOUNDS[hours],ob,side='right')
        grade_counts=dict(GO=int(((gf>go)&(go>=1)).sum()),GU=int(((go>gf)&(gf>=1)).sum()))
    result=[]
    for t in thresholds if thresholds is not None else THRESHOLDS[hours]:
        f,o=fc>=t,ob>=t
        result.append(dict(base,n=len(fc),threshold=t,H=int((f&o).sum()),
                           M=int((~f&o).sum()),F=int((f&~o).sum()),
                           **(dict(T=int((f&tiny).sum()),**grade_counts) if t==.1 else {})))
    if hours in BOUNDS:
        f,o=np.searchsorted(BOUNDS[hours],fc,side='right'),np.searchsorted(BOUNDS[hours],ob,side='right')
        for k in range(1,7):
            result.append(dict(base,n=len(fc),threshold=None,grade=k,
                H=int(((f==k)&(o==k)).sum()),F=int(((f==k)&(f>o)).sum()),M=int(((o==k)&(f<o)).sum())))
    return result


# Appended after the legacy moment/threshold block in station SQLite products.
COLUMNS = ('N1','N2','T','GO','GU') + tuple(f'g{k}{c}' for k in range(1,7) for c in ('h','m','f'))


def sql_expressions():
    bounds=BOUNDS[24]
    f='('+'+'.join(f'(fc>={b})' for b in bounds)+')'
    o='('+'+'.join(f'(ob>={b})' for b in bounds)+')'
    conditions=["var='t' AND ABS(fc-ob)<=1.000001", "var='t' AND ABS(fc-ob)<=2.000001",
                "var IN ('rain6','rain12','rain24') AND fc>=0.1 AND ob<0.1 AND trace=1"]
    conditions += [f"var='rain24' AND {f}>{o} AND {o}>=1",f"var='rain24' AND {o}>{f} AND {f}>=1"]
    conditions += [f"var='rain24' AND {condition}" for k in range(1,7)
                   for condition in (f'{f}={k} AND {o}={k}',f'{o}={k} AND {f}<{o}',f'{f}={k} AND {f}>{o}')]
    return [f'SUM(CASE WHEN {c} THEN 1 ELSE 0 END)' for c in conditions]


def extras(variable, values, threshold=None):
    d=dict(zip(COLUMNS,map(lambda x:int(x or 0),values)))
    if variable=='t':return {k:d[k] for k in ('N1','N2')}
    if variable.startswith('rain') and threshold==.1:return dict(T=d['T'],**({k:d[k] for k in ('GO','GU')} if variable=='rain24' else {}))
    return {}


def grade_rows(base, values):
    d=dict(zip(COLUMNS,map(lambda x:int(x or 0),values)))
    return [dict(base,threshold=None,grade=k,H=d[f'g{k}h'],M=d[f'g{k}m'],F=d[f'g{k}f']) for k in range(1,7)]


def derived(row):
    """Decorate station products without touching existing score values."""
    n=row.get('n',0)
    if 'N1' in row:
        row.update(ACC1=row['N1']/n if n else None, ACC2=row['N2']/n if n else None)
    if 'T' in row:
        row['PC']=(n-row['M']-row['F']+row['T'])/n if n else None
    if row.get('grade'):
        h,m,f=row['H'],row['M'],row['F']
        div=lambda a,b:a/b if b else None
        row.update(TS=div(h,h+m+f),POD=div(h,h+m),FAR=div(f,h+f),MR=div(m,h+m),BIAS=div(h+f,h+m))
        chance=(h+m)*(h+f)/n if n else None
        row['ETS']=div(h-chance,h+m+f-chance) if chance is not None else None
    return row
