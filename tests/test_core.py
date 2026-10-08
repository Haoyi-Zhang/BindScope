from __future__ import annotations
import unittest,copy,threading,time
from bindscope.syntax import parse,UnsupportedSQL
from bindscope.model import *
from bindscope.guard import Guard,POLICIES
from bindscope.pool import Pool,RWGate
from bindscope.sqlite_oracle import SQLiteBridge

Q=parse('SELECT value FROM x WHERE id = $1')
class CoreTests(unittest.TestCase):
    def setUp(self):
        self.w=World();self.w.create('b','x',base=10);self.path=('tmp0','a','b','c')
    def guard(self,p='bindscope'):
        g=Guard('0',p);g.last_sequence=self.w.sequence;g.execute('q',self.w,Q,self.path);return g
    def test_public_pattern_shapes(self):
        from experiments.common import queries
        for q in queries():self.assertGreater(len(q.refs),0)
    def test_fail_closed_parser(self):
        for s in ['SELECT random() FROM x','DELETE FROM x','SELECT value FROM x; DROP TABLE x','WITH q AS (SELECT * FROM x) SELECT * FROM q',"SELECT value FROM x WHERE value = '1'",'SELECT value FROM x LIMIT 1','SELECT value FROM x --comment','SELECT value FROM x WHERE id = $2','SELECT value::text FROM x','SELECT value FROM x FOR UPDATE','SELECT x.value FROM x x JOIN x x ON x.id=x.id']:
            with self.subTest(sql=s),self.assertRaises(UnsupportedSQL):parse(s)
    def test_shadow_counterexample(self):
        guards={p:self.guard(p) for p in POLICIES};ev=self.w.create('a','x',base=99)
        for p,g in guards.items():
            g.observe(ev);out,_=g.execute('q',self.w,Q,self.path)
            self.assertEqual(identity(out)==identity(oracle_binding(self.w,Q,self.path)),p not in ('native_model','dependency'))
    def test_direct_baseline_does_not_build_negative_witnesses(self):
        g=self.guard('direct')
        self.assertFalse(g.entries['q'].certificate.slots)
        self.assertFalse(g.slot_index)
        self.assertFalse(g.path_keys)
    def test_negative_prefix(self):
        r=resolve(self.w,Q,self.path)
        self.assertEqual(r.slots,frozenset({('tmp0','x'),('a','x'),('b','x')}))
    def test_unrelated_suffix(self):
        g=self.guard();g.observe(self.w.create('c','x'));_,d=g.execute('q',self.w,Q,self.path)
        self.assertEqual(d.reason,'clean_witness')
    def test_cancel_salvage(self):
        g=self.guard();g.observe(self.w.create('a','x'));g.observe(self.w.drop('a','x'))
        _,d=g.execute('q',self.w,Q,self.path);self.assertEqual((d.action,d.reason),('reuse','validated_equal'))
    def test_cancellation_does_not_clear_early(self):
        g=self.guard();g.observe(self.w.create('a','x'));self.assertTrue(g.entries['q'].dirty)
        out,_=g.execute('q',self.w,Q,self.path);self.assertEqual(identity(out),identity(oracle_binding(self.w,Q,self.path)))
        g.observe(self.w.drop('a','x'));out,_=g.execute('q',self.w,Q,self.path)
        self.assertEqual(identity(out),identity(oracle_binding(self.w,Q,self.path)))
    def test_path_equal_bindings_refresh(self):
        g=self.guard();g.observe(self.w.event('path',session='0'));np=('b','a')
        _,d=g.execute('q',self.w,Q,np);self.assertEqual(d.reason,'validated_equal');g.assert_index_consistent()
        self.assertEqual(g.entries['q'].certificate.slots,frozenset({('b','x')}))
    def test_path_changed_winner(self):
        self.w.create('a','x');g=self.guard();g.observe(self.w.event('path',session='0'))
        _,d=g.execute('q',self.w,Q,('b','a'));self.assertEqual(d.action,'prepare')
    def test_qualified_ignores_path(self):
        q=parse('SELECT value FROM b.x WHERE id = $1');g=Guard('0');g.execute('q',self.w,q,self.path)
        g.observe(self.w.event('path',session='0'));_,d=g.execute('q',self.w,q,('c',));self.assertEqual(d.reason,'clean_witness')
    def test_missing_not_cached(self):
        q=parse('SELECT value FROM missing WHERE id = $1');g=Guard('0')
        for _ in range(3):
            out,d=g.execute('q',self.w,q,self.path);self.assertEqual(out,('error','42P01'));self.assertFalse(g.entries)
        self.assertEqual(g.prepare_attempts,3)
    def test_gap_barrier(self):
        g=self.guard();self.w.create('a','x') # omitted event
        g.observe(self.w.event('data'));out,d=g.execute('q',self.w,Q,self.path)
        self.assertEqual(d.reason,'barrier');self.assertEqual(identity(out),identity(oracle_binding(self.w,Q,self.path)))
    def test_initial_watermark_exposes_first_missed_event(self):
        g=Guard('0');g.execute('q',self.w,Q,self.path)
        self.w.create('a','x')
        g.observe(self.w.event('data'))
        out,d=g.execute('q',self.w,Q,self.path)
        self.assertEqual(d.reason,'barrier')
        self.assertEqual(identity(out),identity(oracle_binding(self.w,Q,self.path)))
    def test_explicit_order_is_not_canonicalized_away(self):
        self.w.catalog[('b','x')].rows=((1,3),(2,1),(3,2))
        q=parse('SELECT value FROM x ORDER BY id')
        b=SQLiteBridge();b.load(self.w)
        self.assertEqual(b.execute(q,(1,))[1],((3,),(1,),(2,)))
        b.close()
    def test_lost_final_event_recovers_from_watermark(self):
        g=self.guard();self.w.create('a','x');out,_=g.execute('q',self.w,Q,self.path)
        self.assertEqual(identity(out),identity(oracle_binding(self.w,Q,self.path)))
    def test_unknown_and_security_are_barriers(self):
        for kind in ('unknown','security','reset','gap'):
            g=self.guard();g.observe(self.w.event(kind));_,d=g.execute('q',self.w,Q,self.path);self.assertEqual(d.reason,'barrier')
    def test_other_session_path_no_spurious_gap(self):
        g=self.guard();g.observe(self.w.event('path',session='1'));g.observe(self.w.event('data'))
        _,d=g.execute('q',self.w,Q,self.path);self.assertEqual(d.reason,'clean_witness')
    def test_temp_scoping(self):
        g=self.guard();g.observe(self.w.create('tmp1','x'));_,d=g.execute('q',self.w,Q,self.path);self.assertEqual(d.action,'reuse')
        g.observe(self.w.create('tmp0','x'));_,d=g.execute('q',self.w,Q,self.path);self.assertEqual(d.action,'prepare')
    def test_ddl_shape_native_error(self):
        q=parse('SELECT * FROM x WHERE id = $1');gs={p:Guard('0',p) for p in POLICIES}
        for g in gs.values():g.execute('q',self.w,q,self.path)
        ev=self.w.alter('b','x',add_column=True)
        for p,g in gs.items():
            g.observe(ev);out,_=g.execute('q',self.w,q,self.path)
            self.assertEqual(out[0]=='error',p=='native_model')
    def test_data_not_cached(self):
        g=self.guard();old=g.entries['q'].certificate;g.observe(self.w.data('b','x'));_,d=g.execute('q',self.w,Q,self.path)
        self.assertEqual(d.action,'reuse');self.assertEqual(old,g.entries['q'].certificate)
        b=SQLiteBridge();b.load(self.w);rows=b.execute(Q,old.oids,(1,));b.close();self.assertEqual(rows[1],((12,),))
    def test_self_join_deduplicates_watch(self):
        q=parse('SELECT l.value, r.value FROM x l JOIN x r ON l.id=r.id WHERE l.id=$1');r=resolve(self.w,q,self.path)
        self.assertEqual(len(r.bindings),2);self.assertEqual(len(r.slots),3)
    def test_index_cleanup(self):
        g=self.guard();g.evict('q');self.assertFalse(g.slot_index);self.assertFalse(g.schema_index);self.assertFalse(g.path_keys)
    def test_cache_alias_rejected(self):
        g=self.guard()
        with self.assertRaises(ValueError):g.execute('q',self.w,parse('SELECT * FROM x WHERE id=$1'),self.path)
    def test_schema_event_watches_absence(self):
        g=self.guard();self.w.catalog[('a','x')]=Relation(99)
        g.observe(self.w.event('schema',schema='a'));_,d=g.execute('q',self.w,Q,self.path);self.assertEqual(d.action,'prepare')
    def test_aba_identity(self):
        g=self.guard();g.observe(self.w.drop('b','x'));g.observe(self.w.create('b','x',base=10));_,d=g.execute('q',self.w,Q,self.path)
        self.assertEqual(d.action,'prepare')
    def test_prepare_failure_evicts(self):
        g=self.guard();g.observe(self.w.drop('b','x'));out,_=g.execute('q',self.w,Q,self.path)
        self.assertEqual(out[0],'error');self.assertFalse(g.entries)

class PoolTests(unittest.TestCase):
    class S:
        def __init__(self,sid):self.sid=sid;self.guard=Guard(sid)
    def test_exception_returns_lease(self):
        p=Pool(self.S,1)
        with self.assertRaises(ValueError):
            with p.lease():raise ValueError('test')
        with p.lease() as s:self.assertEqual(s.sid,'0')
        p.close()
    def test_active_close_rejected(self):
        p=Pool(self.S,1)
        with p.lease():
            with self.assertRaises(RuntimeError):p.close()
        p.close()
        with self.assertRaises(RuntimeError):
            with p.lease():pass
    def test_writer_blocks_reader(self):
        g=RWGate();entered=threading.Event();release=threading.Event();read=threading.Event()
        def writer():
            with g.write():entered.set();release.wait(2)
        def reader():
            with g.read():read.set()
        a=threading.Thread(target=writer);a.start();self.assertTrue(entered.wait(2))
        b=threading.Thread(target=reader);b.start();self.assertFalse(read.wait(.02));release.set();a.join();b.join();self.assertTrue(read.is_set())
    def test_sessions_never_shared_concurrently(self):
        p=Pool(self.S,2);errors=[]
        def work():
            try:
                for i in range(50):
                    with p.lease() as s:
                        with p.gate.read():time.sleep(.0001)
            except Exception as e:errors.append(e)
        ts=[threading.Thread(target=work) for _ in range(6)]
        for t in ts:t.start()
        for t in ts:t.join()
        self.assertFalse(errors);self.assertEqual(p.leases,300);p.close()

if __name__=='__main__':unittest.main()
