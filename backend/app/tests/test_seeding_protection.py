"""Unit tests for LIB-05: protect actively-seeding torrents from deletion
suggestions."""
import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.app_setting import AppSetting
from app.models.integration import Integration
from app.models.media import MediaItem
from app.services.plex_sync import _is_seeding_path, refresh_seeding_protection


class IsSeedingPathTests(unittest.TestCase):
    def test_exact_match_single_file_torrent(self):
        self.assertTrue(_is_seeding_path("/downloads/Movie.mkv", {"/downloads/Movie.mkv"}))

    def test_file_inside_torrent_directory(self):
        self.assertTrue(_is_seeding_path(
            "/downloads/Show.S01/Show.S01E01.mkv", {"/downloads/Show.S01"}))

    def test_trailing_slash_on_torrent_path_still_matches(self):
        self.assertTrue(_is_seeding_path(
            "/downloads/Show.S01/Show.S01E01.mkv", {"/downloads/Show.S01/"}))

    def test_unrelated_path_does_not_match(self):
        self.assertFalse(_is_seeding_path("/library/Movie.mkv", {"/downloads/Other"}))

    def test_prefix_collision_without_separator_does_not_match(self):
        # "/downloads/Show.S01Extra" must not match torrent "/downloads/Show.S01"
        self.assertFalse(_is_seeding_path(
            "/downloads/Show.S01Extra/file.mkv", {"/downloads/Show.S01"}))

    def test_no_seeding_paths(self):
        self.assertFalse(_is_seeding_path("/downloads/Movie.mkv", set()))


class _FakeClient:
    def __init__(self, paths):
        self._paths = paths

    async def get_seeding_paths(self):
        return self._paths


class _FakeUnreachableClient:
    async def get_seeding_paths(self):
        return None


def _item(rating_key, file_path, **over):
    base = dict(plex_rating_key=rating_key, title=rating_key, media_type="movie", file_path=file_path)
    base.update(over)
    return MediaItem(**base)


class RefreshSeedingProtectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def _set_cleanup(self, **kwargs):
        cfg = {"protect_seeding_torrents": True}
        cfg.update(kwargs)
        self.db.add(AppSetting(key="cleanup", value=json.dumps(cfg)))
        self.db.commit()

    def _add_qbittorrent(self, enabled=True):
        self.db.add(Integration(name="qbittorrent", url="http://qbt:8080", enabled=enabled))
        self.db.commit()

    async def test_noop_when_setting_off(self):
        self.db.add(AppSetting(key="cleanup", value=json.dumps({"protect_seeding_torrents": False})))
        self.db.commit()
        self._add_qbittorrent()
        count = await refresh_seeding_protection(self.db)
        self.assertEqual(count, 0)

    async def test_noop_when_no_download_client_enabled(self):
        self._set_cleanup()
        count = await refresh_seeding_protection(self.db)
        self.assertEqual(count, 0)

    async def test_protects_matching_items(self):
        self._set_cleanup()
        self._add_qbittorrent()
        self.db.add(_item("1", "/downloads/Movie.A/Movie.A.mkv"))
        self.db.add(_item("2", "/library/Movie.B.mkv"))
        self.db.commit()

        with patch("app.api.v1.integrations._get_client",
                   return_value=_FakeClient({"/downloads/Movie.A"})):
            count = await refresh_seeding_protection(self.db)

        self.assertEqual(count, 1)
        a = self.db.query(MediaItem).filter_by(plex_rating_key="1").one()
        b = self.db.query(MediaItem).filter_by(plex_rating_key="2").one()
        self.assertTrue(a.seeding_protected)
        self.assertFalse(bool(b.seeding_protected))

    async def test_resets_stale_protection_no_longer_seeding(self):
        self._set_cleanup()
        self._add_qbittorrent()
        self.db.add(_item("1", "/downloads/Movie.A/Movie.A.mkv", seeding_protected=True))
        self.db.commit()

        with patch("app.api.v1.integrations._get_client", return_value=_FakeClient(set())):
            count = await refresh_seeding_protection(self.db)

        self.assertEqual(count, 0)
        a = self.db.query(MediaItem).filter_by(plex_rating_key="1").one()
        self.assertFalse(bool(a.seeding_protected))

    async def test_unreachable_client_aborts_and_preserves_prior_flags(self):
        self._set_cleanup()
        self._add_qbittorrent()
        self.db.add(_item("1", "/downloads/Movie.A/Movie.A.mkv", seeding_protected=True))
        self.db.commit()

        with patch("app.api.v1.integrations._get_client", return_value=_FakeUnreachableClient()):
            count = await refresh_seeding_protection(self.db)

        self.assertEqual(count, 0)
        a = self.db.query(MediaItem).filter_by(plex_rating_key="1").one()
        self.assertTrue(a.seeding_protected)  # untouched, not cleared


if __name__ == "__main__":
    unittest.main()
