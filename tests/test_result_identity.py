import unittest

from experiments.verify_results import result_identity


class ResultIdentityTests(unittest.TestCase):
    def test_unordered_rows_preserve_multiplicity(self):
        a = ['ok', [['value', 'INTEGER']], [[['int', 1]], [['int', 2]]]]
        b = ['ok', a[1], list(reversed(a[2]))]
        self.assertEqual(result_identity(a), result_identity(b))
        self.assertNotEqual(result_identity(a, ordered=True), result_identity(b, ordered=True))
        self.assertNotEqual(result_identity(a), result_identity(['ok', a[1], a[2] + [a[2][0]]]))

    def test_descriptor_value_and_type_are_significant(self):
        a = ['ok', [['value', 'INTEGER']], [[1]]]
        for b in (['ok', [['other', 'INTEGER']], [[1]]],
                  ['ok', a[1], [[2]]], ['ok', a[1], [[True]]], ['error', 'bad']):
            self.assertNotEqual(result_identity(a), result_identity(b))
