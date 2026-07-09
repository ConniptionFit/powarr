"""Unit tests for the scheduled LLM backlog scanning window logic.
Run inside the container: python -m unittest discover -s app/tests -v"""
import unittest

from app.services.scheduler import in_quiet_hours


class TestInQuietHours(unittest.TestCase):
    def test_simple_window_inside(self):
        self.assertTrue(in_quiet_hours(2, 0, 6))

    def test_simple_window_at_start(self):
        self.assertTrue(in_quiet_hours(0, 0, 6))

    def test_simple_window_exclusive_end(self):
        self.assertFalse(in_quiet_hours(6, 0, 6))

    def test_simple_window_outside(self):
        self.assertFalse(in_quiet_hours(12, 0, 6))

    def test_wrapping_window_inside_before_midnight(self):
        self.assertTrue(in_quiet_hours(23, 22, 6))

    def test_wrapping_window_inside_after_midnight(self):
        self.assertTrue(in_quiet_hours(3, 22, 6))

    def test_wrapping_window_outside(self):
        self.assertFalse(in_quiet_hours(12, 22, 6))

    def test_wrapping_window_at_start(self):
        self.assertTrue(in_quiet_hours(22, 22, 6))

    def test_wrapping_window_exclusive_end(self):
        self.assertFalse(in_quiet_hours(6, 22, 6))

    def test_equal_start_end_is_single_hour(self):
        self.assertTrue(in_quiet_hours(4, 4, 4))
        self.assertFalse(in_quiet_hours(5, 4, 4))
        self.assertFalse(in_quiet_hours(0, 4, 4))


if __name__ == "__main__":
    unittest.main()
