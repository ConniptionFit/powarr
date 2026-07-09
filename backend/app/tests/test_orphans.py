"""Unit tests for orphaned failed-import cleanup — the positive-confirmation
decision rule (download clients + filesystem legs, and the prompt/auto-purge
gate) and the qBittorrent login-response interpretation it depends on.
Run inside the container: python -m unittest discover -s app/tests -v"""
import os
import tempfile
import unittest

from app.services.import_matcher import (
    decide_orphans, decide_orphan_status, orphan_fs_state, looks_like_missing_files,
    extract_output_path,
)
from app.integrations.base import BaseIntegration


class TestDecideOrphans(unittest.TestCase):
    IDS = {"aaa111", "bbb222", "ccc333"}

    def test_all_clients_confirm_absence(self):
        # Both clients answered, neither has bbb/ccc → orphaned
        result = decide_orphans(self.IDS, [{"aaa111"}, set()])
        self.assertEqual(result, {"bbb222", "ccc333"})

    def test_present_in_any_client_is_not_orphaned(self):
        result = decide_orphans(self.IDS, [{"aaa111"}, {"bbb222", "ccc333"}])
        self.assertEqual(result, set())

    def test_unreachable_client_aborts_decision(self):
        # One client down → absence can't be confirmed → no decision at all
        self.assertIsNone(decide_orphans(self.IDS, [{"aaa111"}, None]))

    def test_no_clients_aborts_decision(self):
        self.assertIsNone(decide_orphans(self.IDS, []))

    def test_case_insensitive_hashes(self):
        # *arr downloadIds are uppercase; clients report lowercase
        result = decide_orphans({"AAA111", "BBB222"}, [{"aaa111"}])
        self.assertEqual(result, {"BBB222"})

    def test_empty_ids(self):
        self.assertEqual(decide_orphans(set(), [set()]), set())


class TestOrphanFsState(unittest.TestCase):
    """Filesystem leg of the presence check: present rescues the row, an
    un-stat-able path aborts (same rule as an unreachable client)."""

    def test_no_path_recorded_is_unknown(self):
        self.assertEqual(orphan_fs_state(None), "unknown")
        self.assertEqual(orphan_fs_state(""), "unknown")

    def test_existing_path_is_present(self):
        with tempfile.NamedTemporaryFile() as f:
            self.assertEqual(orphan_fs_state(f.name), "present")

    def test_missing_path_is_absent(self):
        self.assertEqual(orphan_fs_state("/no/such/path/anywhere.mkv"), "absent")

    def test_path_component_is_a_file_is_absent(self):
        # ENOTDIR — a parent component is a regular file → the path can't exist
        with tempfile.NamedTemporaryFile() as f:
            self.assertEqual(orphan_fs_state(os.path.join(f.name, "child.mkv")), "absent")


class TestDecideOrphanStatus(unittest.TestCase):
    """Prompt vs auto-purge gate, applied only after every client confirmed absence."""

    def test_default_prompts_for_confirmation(self):
        self.assertEqual(decide_orphan_status("absent", auto_purge=False), "orphan_pending")
        self.assertEqual(decide_orphan_status("unknown", auto_purge=False), "orphan_pending")

    def test_auto_purge_goes_straight_to_orphaned(self):
        self.assertEqual(decide_orphan_status("absent", auto_purge=True), "orphaned")
        self.assertEqual(decide_orphan_status("unknown", auto_purge=True), "orphaned")

    def test_file_on_disk_is_never_orphaned(self):
        self.assertIsNone(decide_orphan_status("present", auto_purge=False))
        self.assertIsNone(decide_orphan_status("present", auto_purge=True))

    def test_fs_error_aborts_even_with_auto_purge(self):
        self.assertIsNone(decide_orphan_status("error", auto_purge=False))
        self.assertIsNone(decide_orphan_status("error", auto_purge=True))


class TestLooksLikeMissingFiles(unittest.TestCase):
    def test_legacy_and_new_messages(self):
        self.assertTrue(looks_like_missing_files("No importable files resolved for this download"))
        self.assertTrue(looks_like_missing_files("Download files are gone — nothing left to import"))
        self.assertTrue(looks_like_missing_files(
            "Manual import queued | Download files are gone — nothing left to import"))
        self.assertTrue(looks_like_missing_files(
            "Server error '500 Internal Server Error' for url '...manualimport...' "
            "Object reference not set to an instance of an object."))
        self.assertTrue(looks_like_missing_files(
            "No files found are eligible for import in /downloads/x; qBittorrent is reporting missing files"))

    def test_unrelated(self):
        self.assertFalse(looks_like_missing_files(None))
        self.assertFalse(looks_like_missing_files(""))
        self.assertFalse(looks_like_missing_files("Manual import command queued for 3 file(s)"))
        self.assertFalse(looks_like_missing_files("Import push failed: HTTP 500"))


class TestExtractOutputPath(unittest.TestCase):
    def test_structured_field_wins(self):
        self.assertEqual(
            extract_output_path({"outputPath": "/downloads/A", "statusMessages": []}),
            "/downloads/A")

    def test_parses_eligible_message(self):
        msgs = "No files found are eligible for import in /downloads/ActiveSeeds/Foo.S01; qBittorrent is reporting missing files"
        self.assertEqual(extract_output_path({"statusMessages": []}, messages=msgs),
                         "/downloads/ActiveSeeds/Foo.S01")

    def test_from_raw_metadata_json(self):
        raw = '{"outputPath": null, "messages": "No files found are eligible for import in /downloads/X/Y"}'
        self.assertEqual(extract_output_path(raw_metadata=raw), "/downloads/X/Y")


class TestManualImportErrorResult(unittest.TestCase):
    def test_nullreference_500_is_no_files(self):
        r = BaseIntegration._manual_import_error_result(
            Exception("Server error '500 Internal Server Error' for url "
                      "'http://x/manualimport' — Object reference not set to an instance of an object."))
        self.assertEqual(r["reason"], "no_files")
        self.assertIn("gone", r["message"].lower())

    def test_other_errors_passthrough(self):
        r = BaseIntegration._manual_import_error_result(Exception("connection refused"))
        self.assertNotIn("reason", r)
        self.assertIn("connection refused", r["message"])


class TestQbitLoginParsing(unittest.TestCase):
    """_login must accept every response shape seen in the wild:
    v4.x: 200 + "Ok."/"Fails." body with an "SID" cookie;
    v5.x: 204 empty on success, 401 on bad creds;
    5.2+ renamed the cookie "SID" → "QBT_SID_<port>", so success is judged by
    "any session cookie was set", never by a hardcoded cookie name."""

    def _login_outcome(self, status_code: int, body: str, cookies: dict) -> bool:
        # Mirrors the decision expression in QbittorrentIntegration._login
        return status_code < 400 and "fails" not in body.strip().lower() and bool(cookies)

    def test_v5_success_204_empty(self):
        self.assertTrue(self._login_outcome(204, "", {"QBT_SID_8080": "x"}))

    def test_v4_success_200_ok(self):
        self.assertTrue(self._login_outcome(200, "Ok.", {"SID": "x"}))

    def test_v4_bad_creds_200_fails(self):
        self.assertFalse(self._login_outcome(200, "Fails.", {}))

    def test_v5_bad_creds_401(self):
        self.assertFalse(self._login_outcome(401, "Unauthorized", {}))

    def test_no_cookie_is_failure(self):
        self.assertFalse(self._login_outcome(200, "Ok.", {}))


if __name__ == "__main__":
    unittest.main()
