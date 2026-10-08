"""Constructive counterexamples to weakened protocol assumptions; not DB bugs."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from experiments.common import ROOT,write_json
from bindscope.model import World,Relation,oracle_binding,identity
from bindscope.guard import Guard
from bindscope.syntax import parse

def setup(star=False):
    w=World();w.create('b','x',base=10);q=parse('SELECT '+('*' if star else 'value')+' FROM x WHERE id=$1');p=('a','b')
    g=Guard('0');g.execute('q',w,q,p)
    return w,q,p,g

def run():
    rows=[]
    def record(name,w,q,p,out):
        fresh=oracle_binding(w,q,p);bad=identity(out)!=identity(fresh)
        assert bad,name
        rows.append(dict(weakening=name,actual=out,fresh=fresh,mismatch=bad))
    w,q,p,g=setup();g.slot_index[('a','x')].clear();ev=w.create('a','x',base=99);g.observe(ev);out,_=g.execute('q',w,q,p)
    record('omit_negative_slot_watch',w,q,p,out)
    w,q,p,g=setup();g.schema_index['a'].clear();w.create('a','x',base=99);g.last_sequence=w.sequence;g.observe(w.event('schema',schema='a'));out,_=g.execute('q',w,q,p)
    record('omit_absent_schema_watch',w,q,p,out)
    w,q,p,g=setup(star=True);w.create('c','x',columns=('id','value','extra'));g.last_sequence=w.sequence;g.path_keys.clear();np=('c','b');g.observe(w.event('path',session='0'));out,_=g.execute('q',w,q,np)
    record('omit_path_watch',w,q,np,out)
    w,q,p,g=setup();g.synchronize_watermark=lambda watermark:None
    w.create('a','x',base=99);out,_=g.execute('q',w,q,p)
    record('lose_final_event_no_watermark',w,q,p,out)
    w,q,p,g=setup();decision,_=g.decide('q',w,q,p);assert decision.action=='reuse'
    g.observe(w.create('a','x',base=99));out=g.entries['q'].plan.execute(w,q,p)
    record('writer_between_validation_and_execution',w,q,p,out)
    w,q,p,g=setup();w.create('tmp1','x',base=77);other_path=('tmp1','a','b')
    # Applying a server-object handle from another physical session is invalid.
    # This model also shows why equal SQL text is not a sufficient sharing key.
    out=('ok',g.entries['q'].plan.resolution.bindings,g.entries['q'].plan.resolution.descriptor)
    record('cross_session_object_reuse',w,q,other_path,out)
    w,q,p,g=setup();w.create('a','x',base=99);g.observe(w.event('data'));out,d=g.execute('q',w,q,p)
    assert identity(out)==identity(oracle_binding(w,q,p)) and d.reason=='barrier'
    write_json(ROOT/'results/faults.json',{'scope':'constructed model protocol-weakening counterexamples','negative_controls':rows,'later_sequence_gap_recovers':True})
    print(len(rows),'expected negative-control counterexamples; recovery passed')
if __name__=='__main__':run()
