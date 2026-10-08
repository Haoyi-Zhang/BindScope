"""Minimal actual-engine catalog-coherence diagnostic. No binding model used.

A writer creates main.x after a reader has prepared against aux.x. Each arm
starts with new private files. We compare retained and fresh handles, with no
schema synchronization, a version-only read, or an actual schema-table step.
"""
from __future__ import annotations
import sys,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bindscope.native_sqlite import Connection,literal,version
from experiments.common import ROOT,write_json,environment
SQL='SELECT value FROM x WHERE id=1'
def diagnostic():
    records=[]
    for mode in ('none','version_only','schema_step'):
        with tempfile.TemporaryDirectory(prefix='bindscope-catalog-') as directory:
            main=str(Path(directory)/'main.db');aux=str(Path(directory)/'aux.db')
            with Connection(main) as reader,Connection(main) as writer:
                for c in (reader,writer):c.execute('ATTACH '+literal(aux)+' AS aux')
                writer.execute('CREATE TABLE aux.x(id int,value int);INSERT INTO aux.x VALUES(1,50)')
                with reader.prepare(SQL) as retained:
                    before,n=retained.run()
                    writer.execute('CREATE TABLE main.x(id int,value int);INSERT INTO main.x VALUES(1,10)')
                    sync=None
                    if mode=='version_only':sync=reader.query('PRAGMA main.schema_version')
                    elif mode=='schema_step':sync=reader.query('SELECT name FROM main.sqlite_schema LIMIT 1')
                    old,internal=retained.run();fresh=reader.query(SQL)
                    records.append(dict(mode=mode,before=before,synchronization=sync,retained=old,fresh=fresh,internal_reprepares=internal))
    return records
if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--out',default=str(ROOT/'results'));a=ap.parse_args()
    rows=diagnostic();write_json(Path(a.out)/'catalog_sync.json',dict(engine_version=version(),environment=environment(),scope='actual SQLite diagnostic; no PostgreSQL claim; native difference is a contract observation, not a claimed DBMS bug',query=SQL,records=rows));print(json.dumps(rows,indent=2))
