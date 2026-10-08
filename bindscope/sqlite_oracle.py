"""Real SQLite execution of OID-bound integer SELECTs, not PG validation."""
from __future__ import annotations
import sqlite3
from .model import World
from .syntax import Query

class SQLiteBridge:
    def __init__(self):
        self.connection=sqlite3.connect(':memory:',check_same_thread=False)
    def load(self,world:World):
        c=self.connection
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            c.execute(f'DROP TABLE "{name}"')
        for r in world.catalog.values():
            cols=', '.join(f'"{name}" INTEGER NOT NULL' for name in r.columns)
            c.execute(f'CREATE TABLE "r_{r.oid}" ({cols})')
            placeholders=','.join('?' for _ in r.columns)
            c.executemany(f'INSERT INTO "r_{r.oid}" VALUES ({placeholders})',r.rows)
        c.commit()
    def execute(self,query:Query,oids:tuple[int,...],params=()):
        cur=self.connection.execute(query.bound_sql(oids),params)
        rows=cur.fetchall()
        return tuple(d[0] for d in cur.description),tuple(rows if query.order else sorted(rows))
    def close(self):self.connection.close()
