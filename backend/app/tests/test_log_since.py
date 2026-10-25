import logging
import unittest

from app import log_buffer


class LogSinceTests(unittest.TestCase):
    def setUp(self):
        log_buffer._BUFFER.clear()
        log_buffer._TOTAL = 0
        self.h = log_buffer.RingBufferHandler()
        self.log = logging.getLogger("t.logsince")
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        self.log.addHandler(self.h)

    def tearDown(self):
        self.log.removeHandler(self.h)

    def test_incremental(self):
        self.log.info("one"); self.log.info("two")
        lines, nxt, reset = log_buffer.get_logs_since(-1)
        self.assertEqual((len(lines), nxt, reset), (2, 2, True))
        self.log.info("three")
        lines, nxt2, reset = log_buffer.get_logs_since(nxt)
        self.assertTrue(lines[0].endswith("three"))
        self.assertEqual((len(lines), nxt2, reset), (1, 3, False))
        self.assertEqual(log_buffer.get_logs_since(nxt2)[0], [])

    def test_cursor_from_before_restart_resets(self):
        self.log.info("a")
        lines, nxt, reset = log_buffer.get_logs_since(999)
        self.assertTrue(reset)
        self.assertEqual(len(lines), 1)

    def test_rolled_off_lines_are_capped_to_buffer(self):
        for i in range(1005):
            self.log.info("x%d", i)
        lines, nxt, reset = log_buffer.get_logs_since(0)
        self.assertEqual((len(lines), nxt, reset), (1000, 1005, False))
