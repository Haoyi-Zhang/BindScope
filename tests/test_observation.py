"""Real-engine instrumentation/lifecycle regression tests (private databases)."""
import tempfile, unittest
from pathlib import Path
from bindscope.native_sqlite import Connection, SQLiteError, literal
from bindscope.model import World
from bindscope.syntax import parse
from experiments.run_native_sqlite import Session

class ReadObservationTests(unittest.TestCase):
    def setUp(self):
        self.c=Connection(observe_reads=True)
        self.c.execute('CREATE TABLE x(id int,value int);INSERT INTO x VALUES(1,10)')
    def tearDown(self):self.c.close()
    def test_compiled_read_set(self):
        with self.c.prepare('SELECT value FROM x WHERE id=$1') as s:
            self.assertEqual(s.read_relations,(('main','x'),))
            s.run((1,));self.assertEqual(s.read_relations,(('main','x'),))
    def test_reprepare_updates_read_set(self):
        with self.c.prepare('SELECT value FROM x') as s:
            s.run();self.c.execute('CREATE TEMP TABLE x(id int,value int);INSERT INTO temp.x VALUES(1,20)')
            _,n=s.run();self.assertGreater(n,0)
            self.assertEqual(s.read_relations,(('temp','x'),))
    def test_empty_database_read_not_invented(self):
        with self.c.prepare('SELECT COUNT(*) FROM x') as s:
            s.run()
            self.assertTrue(s.reads)
            self.assertEqual(s.read_relations,tuple(sorted(set((d,t) for d,t,c in s.reads if d))))
    def test_authorizer_not_replaced_on_run(self):
        callback=self.c._auth_callback
        with self.c.prepare('SELECT * FROM x') as s:
            s.run();s.run()
        self.assertIs(callback,self.c._auth_callback)
    def test_prepare_tail_lifetime_under_callback_allocations(self):
        # Authorizer callback allocates Python strings during the C call.
        # pzTail remains inside the original byte buffer until inspected.
        for i in range(200):
            with self.c.prepare('SELECT value FROM x WHERE id=1;     ') as s:
                self.assertEqual(s.run()[0][2],(((1,10),),))
    def test_empty_result_read_set(self):
        with self.c.prepare('SELECT value FROM x WHERE id=999') as s:
            self.assertEqual(s.run()[0][2],());self.assertEqual(s.read_relations,(('main','x'),))
    def test_default_observer_is_disabled(self):
        with Connection() as c:
            self.assertIsNone(c._auth_callback)
    def test_checkout_never_resolves_model(self):
        with tempfile.TemporaryDirectory() as d:
            main=Path(d)/'main.db';aux=Path(d)/'aux.db'
            with Connection(str(main)) as c:c.execute('CREATE TABLE x(id int,value int);INSERT INTO x VALUES(1,10)')
            s=Session(0,main,aux,'checkout');w=World()
            try:
                s.guard._resolve=lambda *a:(_ for _ in ()).throw(AssertionError('baseline accessed model'))
                q=parse('SELECT value FROM x WHERE id=$1')
                for _ in range(2):self.assertEqual(s.execute(q,w)[0][2],(((1,10),),))
                self.assertEqual(s.explicit,2);self.assertFalse(s.guard.entries)
            finally:s.close()

if __name__=='__main__':unittest.main()
