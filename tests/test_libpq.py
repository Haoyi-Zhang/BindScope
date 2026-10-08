import unittest
from bindscope.libpq import identifier,literal,Connection,PGError
class LibpqBoundaryTests(unittest.TestCase):
    def test_identifier_quoting(self):
        self.assertEqual(identifier('x"y'),'"x""y"')
        with self.assertRaises(ValueError):identifier('x\x00')
    def test_literal_quoting(self):
        self.assertEqual(literal("x'y"),"'x''y'")
        with self.assertRaises(ValueError):literal('\x00')
    def test_nonexistent_socket_fails_not_synthetic_success(self):
        with self.assertRaises(PGError):
            Connection('host=/nonexistent-bindscope-test-socket connect_timeout=2 dbname=postgres')
