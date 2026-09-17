"""Regression tests for event-loop safety (RES-01/03/04):

- orphan_fs_state_async runs the blocking os.stat in a worker thread with a hard
  timeout, so a hung SMB/NFS mount can't freeze the poller/event loop; a stat that
  doesn't answer in time degrades to "error" (skip the decision), never "gone".
- tasks.spawn_background keeps a strong reference to a fire-and-forget coroutine so
  the loop can't GC-cancel it mid-run.
"""
import asyncio
import tempfile
import time
import unittest

from app.services import import_matcher, tasks


class OrphanFsStateAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_path_is_unknown(self):
        self.assertEqual(await import_matcher.orphan_fs_state_async(None), "unknown")

    async def test_existing_path_is_present(self):
        with tempfile.NamedTemporaryFile() as f:
            self.assertEqual(await import_matcher.orphan_fs_state_async(f.name), "present")

    async def test_missing_path_is_absent(self):
        self.assertEqual(await import_matcher.orphan_fs_state_async("/no/such/path.mkv"), "absent")

    async def test_hung_stat_times_out_to_error_without_blocking_the_loop(self):
        # Patch the pure sync fn (the seam orphan_fs_state_async runs in a thread),
        # NOT os.stat — os.stat is shared process-wide and would slow the whole suite.
        original = import_matcher.orphan_fs_state

        def _hang(path):
            time.sleep(2.0)  # simulate a stuck mount; runs in a worker thread
            return "present"

        import_matcher.orphan_fs_state = _hang
        try:
            started = time.monotonic()
            # The event loop must stay responsive: a concurrent coroutine keeps
            # ticking while the stat is stuck in its thread.
            ticks = 0

            async def _ticker():
                nonlocal ticks
                for _ in range(5):
                    await asyncio.sleep(0.02)
                    ticks += 1

            state, _ = await asyncio.gather(
                import_matcher.orphan_fs_state_async("/some/stuck/mount/file.mkv", timeout=0.2),
                _ticker(),
            )
            elapsed = time.monotonic() - started
        finally:
            import_matcher.orphan_fs_state = original

        self.assertEqual(state, "error")
        self.assertLess(elapsed, 1.0)   # returned on the 0.2s timeout, not the 2s stat
        self.assertEqual(ticks, 5)      # the loop kept running the whole time


class SpawnBackgroundTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_coroutine_and_retains_then_releases_reference(self):
        ran = []

        async def _work():
            await asyncio.sleep(0.01)
            ran.append(True)

        task = tasks.spawn_background(_work())
        self.assertIn(task, tasks._background)  # strong ref held while running
        await task
        await asyncio.sleep(0)  # let the done-callback fire
        self.assertEqual(ran, [True])
        self.assertNotIn(task, tasks._background)  # released after completion


if __name__ == "__main__":
    unittest.main()
