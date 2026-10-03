"""Unrelated maintenance suite: nondeterministic wall-clock assertion."""
import time
import unittest


class ClockMaintenanceTest(unittest.TestCase):
    def test_even_wall_clock_tick(self):
        self.assertEqual(int(time.time()) % 2, 0, "legacy flaky clock check")


if __name__ == "__main__":
    unittest.main()
