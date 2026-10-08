"""Complete depth-three model histories over the explicitly finite domain.

All eight event choices at each depth, four execute/defer schedules, seven
nonempty initial presence sets, two paths and two SELECT forms. No sampling,
no general SQL theorem, and no PostgreSQL execution. Exact generations and
result descriptors (not just relation identity) are checked for safe policies.
"""
from __future__ import annotations
import sys,itertools,copy,csv,collections,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from experiments.common import ROOT,write_json,environment
from bindscope.model import World,oracle_binding,identity
from bindscope.guard import Guard,POLICIES,SAFE_POLICIES
from bindscope.syntax import parse
OPS=('toggle_a','toggle_b','toggle_c','alter_b','shape_b','path','unknown','lost_tail_a')
QUERIES=(parse('SELECT value FROM x WHERE id=$1'),parse('SELECT * FROM x WHERE id=$1'))
PATHS=(('a','b','c'),('c','b','a'))
def transition(w,g,path,op):
    if op.startswith('toggle_') or op=='lost_tail_a':
        s=op[-1];e=w.drop(s,'x') if (s,'x') in w.catalog else w.create(s,'x',base=100*ord(s))
    elif op in ('alter_b','shape_b'):
        e=w.alter('b','x',add_column=op=='shape_b') if ('b','x') in w.catalog else w.create('b','x',base=200)
    elif op=='path':path=PATHS[path==PATHS[0]];e=w.event('path',session='0')
    else:e=w.event('unknown')
    if op!='lost_tail_a':g.observe(e)
    return path

def run(outdir):
    outdir=Path(outdir);outdir.mkdir(parents=True,exist_ok=True);rows=[];first={};start=time.perf_counter()
    for policy in POLICIES:
        counts=collections.Counter(histories=0,checks=0,mismatches=0,exact_mismatches=0,prepares=0,native_delegations=0,errors=0)
        for mask,path0,q in itertools.product(range(1,8),PATHS,QUERIES):
            initial=World()
            for i,s in enumerate(('a','b','c')):
                if mask&(1<<i):initial.create(s,'x',base=i*100)
            base=Guard('0',policy);base.execute('q',initial,q,path0)
            for ops in itertools.product(OPS,repeat=3):
                for first_use,second_use in itertools.product((False,True),repeat=2):
                    w=copy.deepcopy(initial);g=copy.deepcopy(base);path=path0;counts['histories']+=1
                    for step,op in enumerate(ops):
                        path=transition(w,g,path,op)
                        if step<2 and not (first_use,second_use)[step]:continue
                        expected=oracle_binding(w,q,path);actual,d=g.execute('q',w,q,path)
                        bad=identity(actual)!=identity(expected);exact=actual!=expected
                        counts.update(checks=1,mismatches=int(bad),exact_mismatches=int(exact),prepares=int(d.action.startswith('prepare')),native_delegations=int(d.reason=='native_reanalysis'),errors=int(actual[0]=='error'))
                        g.assert_index_consistent()
                        if bad and policy not in first:first[policy]=dict(mask=mask,path=path0,query=q.text,operations=ops,schedule=[first_use,second_use,True],step=step,actual=actual,expected=expected)
                        if policy in SAFE_POLICIES:assert not exact,(policy,ops,step,actual,expected)
        rows.append(dict(policy=policy,**counts));print(policy,dict(counts),flush=True)
        write_json(outdir/'histories-progress.json',{'completed':rows})
    with (outdir/'histories_summary.csv').open('w',newline='') as f:
        wr=csv.DictWriter(f,fieldnames=rows[0]);wr.writeheader();wr.writerows(rows)
    write_json(outdir/'histories.json',dict(scope=__doc__,depth=3,operations=OPS,initial_masks=list(range(1,8)),paths=PATHS,queries=[q.text for q in QUERIES],use_schedules=[[a,b,True] for a,b in itertools.product((False,True),repeat=2)],results=rows,first_counterexamples=first,elapsed_seconds=time.perf_counter()-start,environment=environment()))
if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--out',default=str(ROOT/'results'));a=ap.parse_args();run(a.out)
