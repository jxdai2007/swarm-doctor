"""Editable task tests; protected assertions live in test_reports.py."""
import unittest
from reports.export import export_csv


class ExportEdges(unittest.TestCase):
    def test_generator_and_quoting(self):
        rows = ({"name": value} for value in ['a,b', 'a"b', None])
        self.assertEqual(export_csv(rows, iter(["name"])), 'name\r\n"a,b"\r\n"a""b"\r\n""\r\n')

    def test_duplicate_columns(self):
        with self.assertRaises(ValueError):
            export_csv([], ["name", "name"])
