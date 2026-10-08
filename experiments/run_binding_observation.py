"""Result agreement need not imply relation-binding agreement.

A once-installed observation callback records SQLite's own compilation reads.
No catalog model or BindScope decision contributes to the observed read sets.
Empty database names from COUNT(*) callbacks are retained in raw reads but are
not guessed into a database identity. The covered queries also read a column.
"""
from __future__ import annotations
import sys,tempfile,csv,random
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bindscope.native_sqlite import Connection,literal,canonical,version
from experiments.common import ROOT,write_json,environment
TEXTS=(
 'SELECT value FROM x WHERE id=1',
 'SELECT * FROM x WHERE id=1',
 'SELECT SUM(value) FROM x WHERE id>=1',
 'SELECT COUNT(*) FROM x WHERE id>=1',
 'SELECT a.value,b.value FROM x a JOIN x b ON a.id=b.id WHERE a.id=1',
 'SELECT value FROM aux.x WHERE id=1')

def run(out=ROOT/'results'):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);rows=[]
    for same_data in (True,False):
        for seed in range(25):
            rng=random.Random(seed);v=rng.randrange(1,10000)
            for qi,q in enumerate(TEXTS):
                with tempfile.TemporaryDirectory(prefix='bindscope-observe-') as tmp:
                    main=str(Path(tmp)/'main.db');aux=str(Path(tmp)/'aux.db')
                    with Connection(main,observe_reads=True) as reader,Connection(main) as writer:
                        for c in (reader,writer):c.execute('ATTACH '+literal(aux)+' AS aux')
                        writer.execute('CREATE TABLE aux.x(id int,value int);INSERT INTO aux.x VALUES(1,%d),(2,%d)'%(v,v+1))
                        with reader.prepare(q) as retained:
                            retained.run();writer.execute('CREATE TABLE main.x(id int,value int);INSERT INTO main.x VALUES(1,%d),(2,%d)'%((v,v+1) if same_data else (v+100,v+101)))
                            reader.query('SELECT name FROM main.sqlite_schema LIMIT 1')
                            actual,internal=retained.run()
                            with reader.prepare(q) as fresh:
                                expected,_=fresh.run()
                                rows.append(dict(same_data=same_data,seed=seed,query=qi,result_mismatch=canonical(actual)!=canonical(expected),
                                    read_mismatch=retained.read_relations!=fresh.read_relations,internal_reprepares=internal,
                                    retained_reads=retained.reads,fresh_reads=fresh.reads,actual=actual,fresh=expected))
    write_json(out/'binding_observation.json',dict(engine_version=version(),environment=environment(),queries=TEXTS,records=rows,
        scope='Actual original-SQL engine read-set observation on private unrestricted DB; set of schema/table reads, not complete ordered incarnation binding',
        summary={k:sum(int(r[k]) for r in rows) for k in ('result_mismatch','read_mismatch')},cases=len(rows),
        result_equal_read_different=sum(not r['result_mismatch'] and r['read_mismatch'] for r in rows)))
    print(len(rows),'cases',sum(r['result_mismatch'] for r in rows),'result differences',sum(r['read_mismatch'] for r in rows),'read-set differences')
if __name__=='__main__':run()
