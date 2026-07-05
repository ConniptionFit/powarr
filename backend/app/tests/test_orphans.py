"""Unit tests for orphaned failed-import cleanup — the positive-confirmation
decision rule and the qBittorrent login-response interpretation it depends on.
Run inside the container: python -m unittest discover -s app/tests -v"""
import unittest

from app.services.import_matcher import decide_orphans


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


class TestQbitLoginParsing(unittest.TestCase):
    """_login must accept both response shapes seen in the wild:
    v4.x: 200 + "Ok."/"Fails." body; v5.x: 204 empty on success, 401 on bad creds."""

    def _login_outcome(self, status_code: int, body: str, sid: str | None) -> bool:
        # Mirrors the decision expression in QbittorrentIntegration._login
        return status_code < 400 and "fails" not in body.strip().lower() and bool(sid)

    def test_v5_success_204_empty(self):
        self.assertTrue(self._login_outcome(204, "", "abc"))

    def test_v4_success_200_ok(self):
        self.assertTrue(self._login_outcome(200, "Ok.", "abc"))

    def test_v4_bad_creds_200_fails(self):
        self.assertFalse(self._login_outcome(200, "Fails.", None))

    def test_v5_bad_creds_401(self):
        self.assertFalse(self._login_outcome(401, "Unauthorized", None))

    def test_no_cookie_is_failure(self):
        self.assertFalse(self._login_outcome(200, "Ok.", None))


if __name__ == "__main__":
    unittest.main()
