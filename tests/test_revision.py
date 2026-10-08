import unittest,copy
from bindscope.guard import Guard,SAFE_POLICIES
from bindscope.model import World,identity,oracle_binding
from bindscope.syntax import parse
from bindscope.native_sqlite import Connection,SQLiteError,version

Q=parse('SELECT value FROM x WHERE id=$1')
class NativeDelegationTests(unittest.TestCase):
    def setup_world(self,policy='native_aware',query=Q):
        w=World();w.create('b','x');w.create('c','x',base=99)
        g=Guard('0',policy);path=('a','b','c');g.execute('q',w,query,path)
        return w,g,path
    def test_native_path_delegated(self):
        w,g,p=self.setup_world();g.observe(w.event('path',session='0'));p=('c','b','a')
        out,d=g.execute('q',w,Q,p)
        self.assertEqual(d.reason,'native_reanalysis');self.assertEqual(g.prepare_attempts,1)
        self.assertEqual(identity(out),identity(oracle_binding(w,Q,p)))
    def test_native_positive_ddl_delegated(self):
        w,g,p=self.setup_world();g.observe(w.alter('b','x'));out,d=g.execute('q',w,Q,p)
        self.assertEqual(d.reason,'native_reanalysis')
    def test_shadow_needs_prepare(self):
        w,g,p=self.setup_world();g.observe(w.create('a','x'));out,d=g.execute('q',w,Q,p)
        self.assertEqual(d.action,'prepare')
    def test_changed_shape_not_delegated(self):
        q=parse('SELECT * FROM x');w,g,p=self.setup_world(query=q)
        g.observe(w.alter('b','x',add_column=True));out,d=g.execute('q',w,q,p)
        self.assertEqual(d.action,'prepare');self.assertEqual(out[0],'ok')
    def test_dropped_winner_delegated(self):
        w,g,p=self.setup_world();g.observe(w.drop('b','x'));out,d=g.execute('q',w,Q,p)
        self.assertEqual(d.reason,'native_reanalysis');self.assertEqual(identity(out),identity(oracle_binding(w,Q,p)))
    def test_recreation_delegated_without_aliasing_identity(self):
        w,g,p=self.setup_world();old=w.catalog[('b','x')].oid
        g.observe(w.drop('b','x'));g.observe(w.create('b','x'))
        out,d=g.execute('q',w,Q,p);self.assertNotEqual(old,out[1][0][0]);self.assertEqual(d.reason,'native_reanalysis')
    def test_same_winner_new_prefix_refresh(self):
        w,g,p=self.setup_world();g.observe(w.event('path',session='0'));p=('c','b','a')
        g.execute('q',w,Q,p);g.assert_index_consistent()
        g.observe(w.event('path',session='0'));p=('a','c','b');g.execute('q',w,Q,p)
        g.observe(w.create('a','x'));out,d=g.execute('q',w,Q,p)
        self.assertEqual(d.action,'prepare');self.assertEqual(identity(out),identity(oracle_binding(w,Q,p)))
    def test_no_engine_outcome_oracle_used_by_decide(self):
        w,g,p=self.setup_world();g.observe(w.alter('b','x'))
        g.entries['q'].plan.native_outcome=lambda *args: (_ for _ in ()).throw(AssertionError('oracle access'))
        d,_=g.decide('q',w,Q,p);self.assertEqual(d.reason,'native_reanalysis')
    def test_missing_tail_watermark_recovers_all_safe_policies(self):
        for policy in SAFE_POLICIES:
            with self.subTest(policy=policy):
                w,g,p=self.setup_world(policy);w.create('a','x')
                out,d=g.execute('q',w,Q,p)
                self.assertEqual(identity(out),identity(oracle_binding(w,Q,p)))
    def test_watermark_regression_rejected(self):
        w,g,p=self.setup_world()
        with self.assertRaises(ValueError):g.synchronize_watermark(w.sequence-1)
    def test_negative_watermark_rejected(self):
        with self.assertRaises(ValueError):Guard('0').synchronize_watermark(-1)
    def test_delegation_hard_barrier_still_prepares(self):
        w,g,p=self.setup_world();g.observe(w.event('unknown'))
        _,d=g.execute('q',w,Q,p);self.assertEqual(d.action,'prepare')

class ActualSQLiteTests(unittest.TestCase):
    def setUp(self):
        self.c=Connection();self.c.execute('CREATE TABLE x(id int,value int);INSERT INTO x VALUES(1,10),(2,20)')
    def tearDown(self):self.c.close()
    def test_actual_version(self):self.assertTrue(version().startswith('3.'))
    def test_actual_parameter(self):
        with self.c.prepare('SELECT value FROM x WHERE id=$1') as s:
            out,n=s.run((1,));self.assertEqual(out[2],(((1,10),),));self.assertEqual(n,0)
    def test_temp_shadow_native_and_fresh_agree(self):
        with self.c.prepare('SELECT * FROM x') as s:
            s.run();self.c.execute('CREATE TEMP TABLE x(id int,value int);INSERT INTO temp.x VALUES(1,99)')
            old,n=s.run();fresh=self.c.query('SELECT * FROM x')
            self.assertEqual(old,fresh);self.assertGreater(n,0)
    def test_shape_changes_native_descriptor(self):
        with self.c.prepare('SELECT * FROM x') as s:
            old,_=s.run();self.c.execute('ALTER TABLE x ADD COLUMN extra int DEFAULT 42')
            new,n=s.run();self.assertNotEqual(old[1],new[1]);self.assertEqual(new,self.c.query('SELECT * FROM x'))
    def test_errors_are_real(self):
        with self.assertRaises(SQLiteError):self.c.prepare('SELECT * FROM absent')
    def test_parameter_count(self):
        with self.c.prepare('SELECT value FROM x WHERE id=$1') as s:
            with self.assertRaises(ValueError):s.run(())
    def test_parameter_type(self):
        with self.c.prepare('SELECT value FROM x WHERE id=$1') as s:
            with self.assertRaises(TypeError):s.run(('x',))
    def test_multi_statement_rejected(self):
        with self.assertRaises(ValueError):self.c.prepare('SELECT * FROM x; SELECT * FROM x')
    def test_finalized_handle_rejected(self):
        s=self.c.prepare('SELECT * FROM x');s.close()
        with self.assertRaises(RuntimeError):s.run()
    def test_closed_connection_rejected(self):
        self.c.close()
        with self.assertRaises(RuntimeError):self.c.execute('SELECT 1')
    def test_connection_close_finalizes_children(self):
        s=self.c.prepare('SELECT * FROM x');self.c.close();self.assertTrue(s.closed)
    def test_descriptor_of_empty_result(self):
        out=self.c.query('SELECT id,value FROM x WHERE id=77');self.assertEqual(len(out[1]),2);self.assertFalse(out[2])
    def test_qualified_ignores_temp(self):
        self.c.execute('CREATE TEMP TABLE x(id int,value int);INSERT INTO temp.x VALUES(1,99)')
        out=self.c.query('SELECT value FROM main.x WHERE id=1');self.assertEqual(out[2],(((1,10),),))

if __name__=='__main__':unittest.main()
