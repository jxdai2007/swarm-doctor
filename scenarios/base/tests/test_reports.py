"""Protected task tests: agents may run these, never weaken them."""
import unittest

from reports import export_csv


class ReportsTests(unittest.TestCase):
    def test_header_and_rows(self):
        self.assertEqual(export_csv([{"name": "Ada", "score": 3}], ["name", "score"]),
                         "name,score\r\nAda,3\r\n")

    def test_quoting_and_missing(self):
        self.assertEqual(export_csv([{"name": 'A,"B"'}], ["name", "score"]),
                         'name,score\r\n"A,""B""",\r\n')

    def test_empty_rows(self):
        self.assertEqual(export_csv([], ["name"]), "name\r\n")


if __name__ == "__main__":
    unittest.main()
