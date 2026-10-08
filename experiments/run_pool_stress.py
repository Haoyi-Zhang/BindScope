"""Actual thread/queue/SQLite exercise. Not PostgreSQL or SQL throughput evidence."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from experiments.common import ROOT,write_json,environment
from bindscope.pool import Pool
import sqlite3,threading,time,statistics,random,csv
class Session:
    def __init__(self,name):
        self.name=name;self.conn=sqlite3.connect(':memory:',check_same_thread=False)
        self.conn.execute('CREATE TABLE x(id INTEGER, value INTEGER)')
        self.conn.executemany('INSERT INTO x VALUES(?,?)',[(i,i*3) for i in range(1,2049)])
    def close(self):self.conn.close()

def percentile(xs,p):
    ys=sorted(xs);return ys[round((len(ys)-1)*p)]

def run():
    rows=[];raw=[]
    for rep in range(5):
        cases=[(1,1),(4,2),(8,2)];random.Random(701+rep).shuffle(cases)
        for threads,size in cases:
            pool=Pool(Session,size);startbarrier=threading.Barrier(threads+1);errors=[];returns=[]
            def worker():
                try:
                    startbarrier.wait()
                    for i in range(250):
                        with pool.lease() as s:
                            with pool.gate.read():
                                value=s.conn.execute('SELECT SUM(value) FROM x WHERE id >= ?',((i%2048)+1,)).fetchone()[0]
                                k=i%2048+1;expected=3*(2048*2049-(k-1)*k)//2
                                if value!=expected:raise AssertionError((value,expected))
                    returns.append(1)
                except BaseException as e:errors.append(repr(e))
            workers=[threading.Thread(target=worker) for _ in range(threads)]
            for t in workers:t.start()
            start=time.perf_counter_ns();startbarrier.wait()
            for t in workers:t.join(timeout=60)
            elapsed=time.perf_counter_ns()-start
            if any(t.is_alive() for t in workers):raise RuntimeError('stress timeout')
            assert not errors,errors
            assert len(returns)==threads
            waits=pool.wait_ns.copy();pool.close()
            rows.append(dict(repeat=rep,borrowers=threads,sessions=size,leases=len(waits),seconds=elapsed/1e9,leases_per_second=len(waits)/(elapsed/1e9),median_wait_us=statistics.median(waits)/1000,p95_wait_us=percentile(waits,.95)/1000,max_wait_us=max(waits)/1000,waits_over_100us=sum(x>100000 for x in waits),errors=len(errors)))
            raw.extend(dict(repeat=rep,borrowers=threads,sessions=size,lease=i,wait_ns=x) for i,x in enumerate(waits))
    for filename,data in [('pool_stress.csv',rows),('pool_waits.csv',raw)]:
        with (ROOT/'results'/filename).open('w',newline='') as f:
            wr=csv.DictWriter(f,fieldnames=data[0]);wr.writeheader();wr.writerows(data)
    write_json(ROOT/'results/pool_stress.json',dict(scope='Real Python pool adapter with SQLite; NO PostgreSQL claims',environment=environment(),replicates=5,leases=sum(r['leases'] for r in rows),correct=True))
    print('pool stress completed',sum(r['leases'] for r in rows),'leases')
if __name__=='__main__':run()
