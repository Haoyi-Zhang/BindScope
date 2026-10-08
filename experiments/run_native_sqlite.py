"""Real SQLite prepared-object experiment, not a PostgreSQL surrogate.

Original SQL is prepared by sqlite3_prepare_v2 and retained across DDL. Every
application execution is paired with a distinct fresh prepared handle on the
SAME physical connection and in the SAME transaction (including temp objects).
Before either execution, all candidate catalogs are stepped to synchronize the
connection schema cache. Context writers are quiescent during each pair. These are serial history runs,
not a throughput experiment. Main and aux are shared by two physical sessions.
"""
from __future__ import annotations
import sys,random,tempfile,csv,gzip,time,collections,hashlib,json,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from experiments.common import ROOT,write_json,environment
from bindscope.native_sqlite import Connection,SQLiteError,canonical,version,literal
from bindscope.guard import Guard,Entry,Decision
from bindscope.model import World,Plan,MissingRelation
from bindscope.syntax import parse

POLICIES=('native','checkout','dependency','global_epoch','direct','prefix_strict','bindscope')
TEXTS=(
 'SELECT value FROM x WHERE id=$1',
 'SELECT * FROM x WHERE id=$1',
 'SELECT SUM(value) FROM x WHERE id>=$1',
 'SELECT COUNT(*) FROM y WHERE id>=$1',
 'SELECT a.value,b.value FROM x a JOIN y b ON a.id=b.id WHERE a.id=$1',
 'SELECT value FROM main.x WHERE id=$1',
 'SELECT value FROM aux.x WHERE id=$1',
 'SELECT a.value,b.value FROM x a JOIN x b ON a.id=b.id WHERE a.id=$1')
QUERIES=tuple(parse(x) for x in TEXTS)
PROFILES=('stable','shadow','shape','mixed')
SEEDS=(1201,1301,1409,1511,1601)

class Session:
    def __init__(self,sid,filename,aux,policy,observe_reads=False):
        self.sid=str(sid);self.conn=Connection(str(filename),observe_reads=observe_reads);self.last_reads=None
        self.conn.execute('ATTACH '+literal(str(aux))+' AS aux')
        self.policy=policy;self.guard=Guard(self.sid,policy if policy!='native' else 'native_model')
        self.handles={};self.explicit=0
    def execute(self,q,world):
        g=self.guard;k=q.text;path=('temp'+self.sid,'main','aux')
        if self.policy not in ('native','checkout'):g.synchronize_watermark(world.sequence)
        current=None;decision=Decision('reuse','native',False)
        try:
            if self.policy=='checkout':
                # Strong baseline: plain finalize/prepare/run, with NO guard
                # resolution, certificates, reverse indices or mirrored catalog.
                decision=Decision('prepare','checkout',False)
                if k in self.handles:self.handles.pop(k).close()
                self.explicit+=1;handle=self.conn.prepare(k);self.handles[k]=handle
                out,reparsed=handle.run((1,)*q.nparams);self.last_reads=handle.read_relations
                return out,decision,reparsed
            if self.policy=='native':
                if k not in self.handles:decision=Decision('prepare','cold',False)
            else:
                try:decision,current=g.decide(k,world,q,path)
                except MissingRelation:decision=Decision('prepare','missing_relation',True)
            if decision.action=='prepare':
                if k in self.handles:self.handles.pop(k).close()
                g.evict(k);self.explicit+=1
                handle=self.conn.prepare(k);self.handles[k]=handle
                if self.policy!='native':
                    if current is None:
                        try:current=g._resolve(world,q,path)
                        except MissingRelation:current=None
                    # sqlite3_prepare_v2 may accept a stale cached schema before
                    # sqlite3_step notices a committed external DDL. Do not turn
                    # the mirror's failure into a fabricated engine outcome.
                    # Only a successful modeled binding obtains a certificate;
                    # the actual handle still runs and supplies its own outcome.
                    if current is not None:
                        g._link(k,Entry(q,current,Plan.fresh(world,q,path,current),g.context_epoch,g.hard_epoch,native_path=path))
            elif current is not None:
                e=g.entries[k];g._unlink(k);e.certificate=current;e.dirty=False;e.reasons.clear();e.context_epoch=g.context_epoch;g._link(k,e)
            out,reparsed=self.handles[k].run((1,)*q.nparams)
            if self.policy!='native' and k not in g.entries:
                raise AssertionError('engine succeeded without a mirrored binding')
            self.last_reads=self.handles[k].read_relations
            return out,decision,reparsed
        except SQLiteError as exc:
            self.last_reads=None
            g.evict(k)
            if k in self.handles:self.handles.pop(k).close()
            return ('error',exc.code,str(exc)),decision,0
    def close(self):self.conn.close()

def create_sql(conn,schema,name,base,columns=('id','value')):
    conn.execute('CREATE TABLE '+schema+'.'+name+'('+','.join(c+' int' for c in columns)+')')
    values=','.join('('+','.join(str(v) for v in ([i,base+i]+[42]*(len(columns)-2)))+')' for i in range(1,9))
    conn.execute('INSERT INTO '+schema+'.'+name+' VALUES '+values)

def history(seed,profile,steps):
    rng=random.Random(seed)
    for step in range(steps):
        sid=rng.randrange(2);name=rng.choice(('x','y'));action='none'
        if profile=='stable':action='data' if step%17==0 else 'none'
        elif rng.random()<.35:
            action=rng.choice({'shadow':('temp','cancel'),'shape':('shape','main'),
                               'mixed':('temp','shape','main','cancel','unrelated','data','stats','unknown')}[profile])
        yield action,sid,name,rng.randrange(len(QUERIES)),rng.randrange(2)

def mutate(w,sessions,admin,action,sid,name):
    events=[];schema='temp'+str(sid);conn=sessions[sid].conn
    def toggle(logical,physical,c,base):
        slot=(logical,name)
        if slot in w.catalog:
            c.execute('DROP TABLE '+physical+'.'+name);events.append(w.drop(*slot))
        else:
            create_sql(c,physical,name,base);events.append(w.create(*slot,base=base))
    if action=='temp':toggle(schema,'temp',conn,900)
    elif action=='main':toggle('main','main',admin,100)
    elif action=='shape':
        slot=('main',name)
        if slot not in w.catalog:
            create_sql(admin,'main',name,100);events.append(w.create(*slot,base=100))
        else:
            r=w.catalog[slot]
            if len(r.columns)==2:
                admin.execute('ALTER TABLE main.'+name+' ADD COLUMN extra int DEFAULT 42');r.columns+=('extra',)
            else:
                admin.execute('ALTER TABLE main.'+name+' DROP COLUMN extra');r.columns=('id','value')
            r.version+=1;events.append(w.event('ddl',(slot,)))
    elif action=='cancel':
        if (schema,name) not in w.catalog:
            create_sql(conn,'temp',name,900);events.append(w.create(schema,name,base=900))
            conn.execute('DROP TABLE temp.'+name);events.append(w.drop(schema,name))
    elif action=='unrelated':
        other='unrelated';slot=('main',other)
        if slot in w.catalog:admin.execute('DROP TABLE main.'+other);events.append(w.drop(*slot))
        else:create_sql(admin,'main',other,700);events.append(w.create(*slot,base=700))
    elif action=='data':
        if ('main',name) in w.catalog:
            admin.execute('UPDATE main.'+name+' SET value=value+1 WHERE id=1');events.append(w.data('main',name))
    elif action=='stats':admin.execute('ANALYZE');events.append(w.event('stats'))
    elif action=='unknown':events.append(w.event('unknown'))
    for e in events:
        for s in sessions:s.guard.observe(e)
    return events

def run(outdir,steps=1000,seeds=SEEDS):
    outdir=Path(outdir);outdir.mkdir(parents=True,exist_ok=True)
    summary=[];mismatches=[]
    fields=['profile','seed','step','policy','session','action','query','decision','reason','explicit_prepare','internal_reprepare','mismatch','read_mismatch','actual_reads','fresh_reads','actual','fresh']
    start=time.perf_counter()
    with gzip.open(outdir/'native_sqlite_decisions.csv.gz','wt',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for profile in PROFILES:
            for seed in seeds:
                for policy in POLICIES:
                    with tempfile.TemporaryDirectory(prefix='bindscope-sqlite-') as directory:
                        main=Path(directory)/'main.db';aux=Path(directory)/'aux.db'
                        admin=Connection(str(main));admin.execute('PRAGMA journal_mode=WAL;ATTACH '+literal(str(aux))+' AS aux; PRAGMA aux.journal_mode=WAL')
                        w=World()
                        for schema,base in [('main',100),('aux',500)]:
                            for name in ('x','y'):
                                create_sql(admin,schema,name,base);w.create(schema,name,base=base)
                        sessions=[Session(i,main,aux,policy,observe_reads=True) for i in range(2)]
                        for s in sessions:s.guard.synchronize_watermark(w.sequence)
                        count=collections.Counter()
                        try:
                            for step,(action,sid,name,qi,borrower) in enumerate(history(seed,profile,steps)):
                                events=mutate(w,sessions,admin,action,sid,name)
                                s=sessions[borrower];q=QUERIES[qi];before=s.explicit
                                s.conn.execute('BEGIN')
                                try:
                                    # Control-plane barrier shared by ALL policies.
                                    # PRAGMA schema_version alone does not refresh
                                    # SQLite's parser schema cache (separate diagnostic).
                                    for catalog in ('temp','main','aux'):
                                        s.conn.query('SELECT name FROM '+catalog+'.sqlite_schema LIMIT 1')
                                    actual,decision,reparsed=s.execute(q,w)
                                    try:
                                        with s.conn.prepare(q.text) as fresh_handle:
                                            fresh,_=fresh_handle.run((1,)*q.nparams);fresh_reads=fresh_handle.read_relations
                                    except SQLiteError as exc:fresh=('error',exc.code,str(exc));fresh_reads=None
                                    bad=canonical(actual)!=canonical(fresh)
                                    read_bad=actual[0]==fresh[0]=='ok' and s.last_reads!=fresh_reads
                                finally:s.conn.execute('ROLLBACK')
                                count.update(executions=1,fresh_controls=1,mismatches=int(bad),read_mismatches=int(read_bad),explicit_prepares=s.explicit-before,
                                             internal_reprepares=reparsed,errors=int(actual[0]=='error'),events=len(events),catalog_sync_prepares=3)
                                writer.writerow(dict(profile=profile,seed=seed,step=step,policy=policy,session=borrower,action=action,
                                    query=qi,decision=decision.action,reason=decision.reason,explicit_prepare=s.explicit-before,
                                    internal_reprepare=reparsed,mismatch=int(bad),read_mismatch=int(read_bad),actual_reads=json.dumps(s.last_reads),fresh_reads=json.dumps(fresh_reads),actual=json.dumps(actual),fresh=json.dumps(fresh)))
                                if bad and len(mismatches)<20:mismatches.append(dict(profile=profile,seed=seed,step=step,policy=policy,actual=actual,fresh=fresh))
                        finally:
                            for s in sessions:s.close()
                            admin.close()
                        summary.append(dict(profile=profile,seed=seed,policy=policy,**count))
                    stream.flush()
                print(profile,seed,'completed',flush=True)
    with (outdir/'native_sqlite_summary.csv').open('w',newline='') as f:
        wr=csv.DictWriter(f,fieldnames=list(summary[0]));wr.writeheader();wr.writerows(summary)
    result=dict(scope='Actual libsqlite3 with once-installed observational authorizer; original query read sets, not complete object-incarnation tuples;  original SQL; common three-catalog synchronization barrier; same-session fresh handle in matched transaction; no PostgreSQL',
                engine_version=version(),environment=environment(),steps=steps,seeds=seeds,profiles=PROFILES,policies=POLICIES,
                queries=TEXTS,first_mismatches=mismatches,elapsed_seconds=time.perf_counter()-start)
    write_json(outdir/'native_sqlite.json',result)
    assert all(r['mismatches']==r['read_mismatches']==0 for r in summary if r['policy'] not in ('native','dependency')), 'guard/fresh mismatch: inspect raw records'

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--out',default=str(ROOT/'results'));ap.add_argument('--steps',type=int,default=1000)
    args=ap.parse_args();run(args.out,args.steps)
