"""Real libpq/pool pilot; unavailable is a failure to establish engine evidence.

Default is a non-mutating connection probe. --run creates unique private schemas
and executes controlled DDL; use a dedicated disposable LOCAL test database.
This harness is authored but was not live-tested in the delivered environment.
"""
from __future__ import annotations
import sys,os,shutil,ctypes,ctypes.util,time,traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from experiments.common import ROOT,write_json,environment
from bindscope.libpq import Connection,PGError,identifier,literal
from bindscope.pool import Pool
from bindscope.guard import Guard,Entry
from bindscope.model import World,Relation,Plan,resolve,Event,MissingRelation
from bindscope.syntax import parse

class PostgresSession:
    """Exclusive per-physical-connection prepared objects; no cross-session IDs."""
    def __init__(self,sid,dsn,policy):
        self.connection=Connection(dsn);self.guard=Guard(sid,policy)
        self.prepared_names={};self.counter=0
    def close(self):self.connection.close()
    def run(self,query,world,path):
        g=self.guard;key=query.text
        g.synchronize_watermark(world.sequence)
        try:
            decision,current=g.decide(key,world,query,path)
            if decision.action=='prepare':
                old=self.prepared_names.pop(key,None)
                if old is not None:self.connection.execute('DEALLOCATE '+identifier(old))
                g.evict(key);g.prepare_attempts+=1
                if current is None:current=g._resolve(world,query,path)
                self.counter+=1;name='bs_'+str(self.counter)
                self.connection.prepare(name,query.text,(23,)*query.nparams)
                self.prepared_names[key]=name
                g._link(key,Entry(query,current,Plan.fresh(world,query,path,current),g.context_epoch,g.hard_epoch,native_path=path))
            elif current is not None:
                e=g.entries[key];g._unlink(key);e.certificate=current;e.dirty=False;e.reasons.clear()
                e.context_epoch=g.context_epoch;g._link(key,e)
            # This is real PQexecPrepared, not Plan.execute or a synthetic result.
            result=self.connection.prepared(self.prepared_names[key],(1,)*query.nparams)
            g.entries[key].native_path=path
            return result,decision
        except (PGError,MissingRelation):
            g.evict(key)
            # Keep server name so the next real prepare can deallocate it.
            raise

def capture_catalog(admin,schemas,versions,seq):
    """Authoritative catalog reads for these controlled, integer-only pilot tables."""
    w=World(sequence=seq)
    for schema in schemas:
        rows=admin.execute('SELECT c.oid::text,c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='+literal(schema)+" AND c.relkind='r'")[2]
        for oid,name in rows:
            cols=admin.execute('SELECT attname,atttypid::text FROM pg_catalog.pg_attribute WHERE attrelid='+str(int(oid))+ ' AND attnum>0 AND NOT attisdropped ORDER BY attnum')[2]
            if any(t!='23' for _,t in cols):raise RuntimeError('pilot fragment requires int4 columns')
            signature=tuple(cols);prev=versions.get(int(oid))
            version=0 if prev is None else prev[1]+int(signature!=prev[0])
            versions[int(oid)]=(signature,version)
            w.catalog[(schema,name)]=Relation(int(oid),version=version,columns=tuple(c for c,_ in cols))
    return w

def run_pilot(dsn):
    tag='bindscope_'+str(os.getpid())+'_'+str(int(time.time()));schemas=[tag+'_a',tag+'_b',tag+'_c']
    a,b,c=schemas;admin=Connection(dsn);pools={};results=[];versions={};sequence=0
    path=(a,b,c)
    queries=[parse('SELECT value FROM x WHERE id=$1'),parse('SELECT * FROM x WHERE id=$1')]
    def event(kind,slots=()):
        nonlocal sequence
        sequence+=1;e=Event(sequence,kind,tuple(slots))
        for pool in pools.values():
            with pool.gate.write():pool.broadcast(e)
    def phase(label):
        world=capture_catalog(admin,schemas,versions,sequence)
        # Catalog and data writers are quiescent for this entire matched batch.
        admin.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        snap=admin.execute('SELECT pg_export_snapshot()')[2][0][0]
        try:
            for policy,pool in pools.items():
                for i in range(4):
                    query=queries[i%2]
                    with pool.lease() as session, pool.gate.read():
                        conn=session.connection
                        conn.execute('SET search_path TO '+', '.join(identifier(x) for x in ('pg_catalog',)+path))
                        conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
                        conn.execute('SET TRANSACTION SNAPSHOT '+literal(snap))
                        try:
                            with Connection(dsn) as fresh:
                                fresh.execute('SET search_path TO '+', '.join(identifier(x) for x in ('pg_catalog',)+path))
                                fresh.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
                                fresh.execute('SET TRANSACTION SNAPSHOT '+literal(snap))
                                fresh.prepare('fresh',query.text,(23,)*query.nparams)
                                wanted=fresh.prepared('fresh',(1,)*query.nparams)
                                try:
                                    actual,decision=session.run(query,world,path)
                                    record={'phase':label,'policy':policy,'physical_session':session.guard.session,'backend_pid':conn.backend_pid,'query':query.text,'action':decision.action,'reason':decision.reason,'actual':actual,'fresh':wanted,'mismatch':actual!=wanted}
                                except PGError as exc:
                                    record={'phase':label,'policy':policy,'query':query.text,'error':str(exc),'sqlstate':exc.sqlstate,'fresh':wanted,'mismatch':True}
                                results.append(record);fresh.execute('ROLLBACK')
                        finally:conn.execute('ROLLBACK')
        finally:admin.execute('ROLLBACK')
    try:
        for schema in schemas:admin.execute('CREATE SCHEMA '+identifier(schema))
        for schema,value in ((b,10),(c,30)):
            admin.execute('CREATE TABLE '+identifier(schema)+'.x(id int,value int)')
            admin.execute('INSERT INTO '+identifier(schema)+'.x VALUES(1,'+str(value)+')')
        for policy in ('native_model','checkout','dependency','bindscope'):
            pools[policy]=Pool(lambda sid,p=policy:PostgresSession(sid,dsn,p),2)
        phase('baseline')
        admin.execute('CREATE TABLE '+identifier(a)+'.x(id int,value int)');admin.execute('INSERT INTO '+identifier(a)+'.x VALUES(1,20)');event('ddl',((a,'x'),));phase('earlier_shadow')
        admin.execute('DROP TABLE '+identifier(a)+'.x');event('ddl',((a,'x'),));phase('shadow_removed')
        admin.execute('ALTER TABLE '+identifier(b)+'.x ADD COLUMN extra int DEFAULT 42');event('ddl',((b,'x'),));phase('result_shape_change')
        path=(c,b,a);event('path');phase('path_switch')
        return dict(status='completed',scope='actual PostgreSQL controlled persistent-schema pilot; no temp/role/performance validation',server_version=admin.server_version,records=results)
    finally:
        for pool in pools.values():pool.close()
        for schema in reversed(schemas):
            try:admin.execute('DROP SCHEMA IF EXISTS '+identifier(schema)+' CASCADE')
            except PGError:pass
        admin.close()

def main():
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--run',action='store_true');ap.add_argument('--output',default=str(ROOT/'results/postgres_pilot.json'));args=ap.parse_args()
    result={'environment':environment(),'executables':{x:shutil.which(x) for x in ('postgres','initdb','pg_ctl','psql')},'libpq':ctypes.util.find_library('pq'),'engine_evidence':False,'requested_run':args.run}
    dsn=os.environ.get('BINDSCOPE_PG_DSN','host=127.0.0.1 port=5432 dbname=postgres connect_timeout=2')
    try:
        with Connection(dsn) as conn:result.update(probe='connected',server_version=conn.server_version)
        if args.run:result.update(run_pilot(dsn));result['engine_evidence']=True
        else:result['status']='probe_only'
    except (PGError,OSError) as exc:result.update(status='unavailable',error=str(exc))
    except Exception as exc:
        result.update(status='failed',error=repr(exc),traceback=traceback.format_exc())
    write_json(args.output,result);print(result.get('status'),result.get('error',''))
    return 0 if result.get('status') in ('completed','probe_only') else 2
if __name__=='__main__':sys.exit(main())
