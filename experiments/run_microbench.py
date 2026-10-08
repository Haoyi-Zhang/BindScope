"""Control-plane measurements ONLY: no SQL prepare/execution or DB speedup."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from experiments.common import ROOT,write_json,environment
from bindscope.model import World,Event
from bindscope.syntax import parse
from bindscope.guard import Guard
import time,random,csv,gc,statistics

def setup(n,length,deps,policy):
    w=World();path=tuple('s'+str(i) for i in range(length))
    for j in range(deps):w.create(path[-1],'r'+str(j))
    joins=''.join(f' JOIN r{j} a{j} ON a0.id = a{j}.id' for j in range(1,deps))
    g=Guard('0',policy);qs=[]
    for i in range(n):
        q=parse('SELECT a0.value FROM r0 a0'+joins+f' WHERE a0.id = {i}')
        qs.append(q);g.execute(str(i),w,q,path)
    return w,path,g,qs

def run():
    rows=[];repeats=7;iters=20000;rng=random.Random(94017)
    cases=[(n,l,d,p) for n in (64,1024,4096) for l,d in ((2,1),(8,1),(8,4)) for p in ('bindscope','direct')]
    for rep in range(repeats):
        order=cases.copy();rng.shuffle(order)
        for n,length,deps,p in order:
            w,path,g,qs=setup(n,length,deps,p);keys=[str(i%n) for i in range(iters)]
            for key in keys[:1000]:g.decide(key,w,qs[int(key)],path)
            gc.collect();gc.disable()
            try:
                start=time.perf_counter_ns()
                for key in keys:g.decide(key,w,qs[int(key)],path)
                elapsed=time.perf_counter_ns()-start
            finally:gc.enable()
            rows.append(dict(repeat=rep,mode='clean_decide',policy=p,cache=n,path=length,deps=deps,iterations=iters,ns_per_op=elapsed/iters))
            write_json(ROOT/'results/microbench-progress.json',{'measurements':rows})
            print(rep,n,length,deps,p,flush=True)
    # Event fanout: distinct statements all watch r0. No SQL or native execution.
    for n in (64,1024,4096):
        w,path,g,qs=setup(n,8,1,'bindscope')
        for mode,slot in [('irrelevant_event',('s0','other')),('all_watchers_event',('s0','r0'))]:
            for rep in range(repeats):
                startseq=(g.last_sequence or 0);count=200
                start=time.perf_counter_ns()
                for i in range(count):g.observe(Event(startseq+i+1,'ddl',(slot,)))
                elapsed=time.perf_counter_ns()-start
                rows.append(dict(repeat=rep,mode=mode,policy='bindscope',cache=n,path=8,deps=1,iterations=count,ns_per_op=elapsed/count))
        g.assert_index_consistent()
    with (ROOT/'results/microbench.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
    write_json(ROOT/'results/microbench.json',dict(scope='Python guard control-plane, NOT PostgreSQL latency or throughput',environment=environment(),repeats=repeats,clean_iterations=iters,case_order_seed=94017,garbage_collection='disabled only in clean timed loop',event_note='Repeated notifications without executing dirty statements; measures marking fanout, not revalidation',measurements=len(rows)))
    print('microbench completed',len(rows),'measurements',flush=True)
if __name__=='__main__':run()
