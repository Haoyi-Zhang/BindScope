"""Original-SQL retained-handle timing under physical-session contention.

All contexts are stable during each measurement. Catalog synchronization and
fresh-control construction are outside the measured interval. This isolates the
steady-state ownership/guard/driver path; it does NOT measure online DDL,
PostgreSQL, or a production service. Raw lease waits and repeat order are saved.
"""
from __future__ import annotations
import sys, csv, gzip, random, tempfile, time, threading, statistics
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from experiments.common import ROOT,write_json,environment
from experiments.run_native_sqlite import Session
from bindscope.native_sqlite import Connection,canonical,literal,version
from bindscope.model import World
from bindscope.syntax import parse
from bindscope.pool import Pool
POLICIES=('native','checkout','direct','bindscope')
QUERIES={'point':parse('SELECT value FROM x WHERE id=$1'),
         'aggregate':parse('SELECT SUM(value) FROM x WHERE id>=$1')}
for size in (4,12):
    QUERIES['join'+str(size)]=parse('SELECT a0.value FROM x a0'+''.join(' JOIN x a%d ON a0.id=a%d.id'%(i,i) for i in range(1,size))+' WHERE a0.id=$1')

def measure(policy,workload,borrowers,size,repeat,leases,waitwriter,order):
    with tempfile.TemporaryDirectory(prefix='bindscope-pool-') as directory:
        main=Path(directory)/'main.db';aux=Path(directory)/'aux.db';q=QUERIES[workload]
        with Connection(str(main)) as admin:
            admin.execute('CREATE TABLE x(id INTEGER PRIMARY KEY,value int);'+
              'INSERT INTO x VALUES '+','.join('(%d,%d)'%(i,100+i) for i in range(1,2049)))
        w=World();w.create('main','x',base=100)
        pool=Pool(lambda sid:Session(sid,main,aux,policy),size)
        expected=None
        try:
            for s in pool.sessions:
                for catalog in ('temp','main','aux'):
                    s.conn.query('SELECT name FROM '+catalog+'.sqlite_schema LIMIT 1')
                now=canonical(s.conn.query(q.text,(1,)))
                if expected is None:expected=now
                assert now==expected
                for _ in range(50):assert canonical(s.execute(q,w)[0])==expected
            before=[s.explicit for s in pool.sessions]
            barrier=threading.Barrier(borrowers+1);errors=[];counts=[0]*borrowers
            def worker(index):
                try:
                    barrier.wait(timeout=20)
                    for _ in range(leases):
                        with pool.lease(timeout=20) as session:
                            with pool.gate.read():
                                actual,decision,n=session.execute(q,w)
                                if canonical(actual)!=expected:raise AssertionError('result differs from fresh preflight')
                                counts[index]+=n
                except BaseException as exc:errors.append(repr(exc))
            threads=[threading.Thread(target=worker,args=(i,)) for i in range(borrowers)]
            for t in threads:t.start()
            start=time.perf_counter_ns();barrier.wait(timeout=20)
            for t in threads:t.join(timeout=40)
            elapsed=(time.perf_counter_ns()-start)/1e9
            if any(t.is_alive() for t in threads):raise RuntimeError('pool benchmark timeout')
            if errors:raise RuntimeError(';'.join(errors))
            assert pool.leases==borrowers*leases
            waits=sorted(pool.wait_ns);n=len(waits)
            record=dict(order=order,repeat=repeat,workload=workload,policy=policy,borrowers=borrowers,sessions=size,
                        executions=n,seconds=elapsed,throughput=n/elapsed,wait_median_us=statistics.median(waits)/1000,
                        wait_p95_us=waits[max(0,int(.95*n)-1)]/1000,wait_p99_us=waits[max(0,int(.99*n)-1)]/1000,
                        explicit_prepares=sum(s.explicit-b for s,b in zip(pool.sessions,before)),
                        internal_reprepares=sum(counts),mismatches=0)
            for i,ns in enumerate(pool.wait_ns):waitwriter.writerow(dict(order=order,lease=i,wait_ns=ns))
            return record
        finally:pool.close()

def run(out=ROOT/'results',repeats=5,leases=250):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    rng=random.Random(48271);cases=[(p,w,b,s) for p in POLICIES for w in QUERIES for b,s in ((1,1),(4,2),(8,2))]
    rows=[]
    with gzip.open(out/'native_pool_waits.csv.gz','wt',newline='') as f:
        wr=csv.DictWriter(f,fieldnames=['order','lease','wait_ns']);wr.writeheader()
        for rep in range(repeats):
            order=cases.copy();rng.shuffle(order)
            for p,w,b,s in order:
                result=measure(p,w,b,s,rep,leases,wr,len(rows));rows.append(result)
                write_json(out/'native_pool-progress.json',{'measurements':rows})
                print(rep,p,w,b,s,result['throughput'],flush=True)
    with (out/'native_pool.csv').open('w',newline='') as f:
        wr=csv.DictWriter(f,fieldnames=list(rows[0]));wr.writeheader();wr.writerows(rows)
    write_json(out/'native_pool.json',dict(scope='Actual libsqlite3 original-SQL steady-state pool; NO context mutation in timed interval; catalog synchronization, fresh controls and 50 warmup executions/session outside timer',
        engine_version=version(),environment=environment(),repeats=repeats,leases_per_borrower=leases,order_seed=48271,
        table_rows=2048,queries={k:q.text for k,q in QUERIES.items()},measurements=len(rows),total_executions=sum(r['executions'] for r in rows),
        note='Python threads/ctypes/queue and shared CPU scheduling are part of measured cost. Five repeats describe this environment, not population confidence bounds.'))
if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--out',default=str(ROOT/'results'));ap.add_argument('--repeats',type=int,default=5);ap.add_argument('--leases',type=int,default=250);a=ap.parse_args()
    run(a.out,a.repeats,a.leases)
