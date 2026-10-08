"""Bounded checks over every stated catalog state, query, and legal event."""
from __future__ import annotations
from common import *
import itertools,copy,csv
from collections import Counter
from bindscope.model import World,Relation,resolve,oracle_binding,identity,MissingRelation
from bindscope.guard import Guard,POLICIES,SAFE_POLICIES

def run(outdir):
    outdir=Path(outdir); outdir.mkdir(parents=True,exist_ok=True)
    counts=Counter(); mismatches=Counter(); reuse_checks=Counter(); examples={}
    cases=[(('a','b'),('x','y')), (('tmp','a','b'),('x',))]
    with (outdir/'exhaustive_by_family.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['family','catalogs','initial_resolutions','transitions','policy','mismatches','reuse_checks'])
        writer.writeheader()
        for schemas,names in cases:
            family=f'{len(schemas)}schemas_{len(names)}names'; before=counts.copy(); bm=mismatches.copy(); br=reuse_checks.copy()
            slots=list(itertools.product(schemas,names))
            q=[parse(f'SELECT value FROM {n} WHERE id = $1') for n in names]
            q += [parse(f'SELECT * FROM {s}.{n} WHERE id = $1') for s,n in slots]
            if len(names)>1:q += [parse('SELECT l.value, r.value FROM x l JOIN y r ON l.id = r.id WHERE l.id = $1')]
            paths=[p for k in range(1,len(schemas)+1) for p in itertools.permutations(schemas,k)]
            for state in itertools.product((0,1,2),repeat=len(slots)):
                counts['catalogs']+=1; w=World(next_oid=100)
                for j,(slot,status) in enumerate(zip(slots,state)):
                    if status:w.catalog[slot]=Relation(j+1,version=status-1,rows=((1,j+10),))
                for path,query in itertools.product(paths,q):
                    try:res=resolve(w,query,path)
                    except MissingRelation:continue
                    counts['initial_resolutions']+=1
                    expected=oracle_binding(w,query,path)
                    assert expected[1]==res.bindings and expected[2]==res.descriptor
                    events=[]
                    for slot in slots:
                        if slot in w.catalog:
                            events += [('drop',slot),('alter',slot),('column',slot),('data',slot),('stats',slot)]
                        else:events.append(('create',slot))
                    events += [('path',p) for p in paths if p!=path]
                    for kind,arg in events:
                        nw=copy.deepcopy(w); np=path
                        if kind=='drop':event=nw.drop(*arg)
                        elif kind=='alter':event=nw.alter(*arg)
                        elif kind=='column':event=nw.alter(*arg,add_column=True)
                        elif kind=='create':event=nw.create(*arg,base=999)
                        elif kind=='data':event=nw.data(*arg)
                        elif kind=='stats':event=nw.analyze(*arg)
                        else:np=arg;event=nw.event('path',session='0')
                        wanted=identity(oracle_binding(nw,query,np)); counts['transitions']+=1
                        for p in POLICIES:
                            g=Guard('0',p);g.execute('q',w,query,path);g.observe(event)
                            actual,decision=g.execute('q',nw,query,np)
                            if decision.action=='reuse':reuse_checks[p]+=1
                            bad=identity(actual)!=wanted;mismatches[p]+=bad
                            if bad and p not in examples:
                                examples[p]={'family':family,'catalog_state':state,'path':path,'query':query.text,'event':[kind,arg], 'actual':actual,'fresh':wanted}
                            g.assert_index_consistent()
                            if p in SAFE_POLICIES:
                                assert not bad,(p,kind,query.text,actual,wanted)
            for p in POLICIES:
                writer.writerow(dict(family=family,**{k:counts[k]-before[k] for k in ('catalogs','initial_resolutions','transitions')},policy=p,mismatches=mismatches[p]-bm[p],reuse_checks=reuse_checks[p]-br[p]))
    summary={'scope':'finite abstract model; no PostgreSQL execution', 'counts':dict(counts),'policies':{p:{'mismatches':mismatches[p],'reuse_checks':reuse_checks[p]} for p in POLICIES},'counterexamples':examples}
    write_json(outdir/'exhaustive.json',summary);print(json.dumps(summary,indent=2))

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--out',default=str(ROOT/'results'));args=ap.parse_args();run(args.out)
