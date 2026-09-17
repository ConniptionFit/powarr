"""Unit tests for LIB-02: explicit Sonarr episode-delete policy modes.

Sonarr has no native per-episode delete/unmonitor distinction — deleting
"one episode" via the old default path calls delete_series()/unmonitor_series()
on the WHOLE series. propagate_and_delete(delete_mode=...) gives the caller an
explicit, narrower choice instead."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.deletion_log import DeletionLog
from app.models.integration import Integration
from app.models.media import MediaItem
from app.services.deleter import propagate_and_delete


class _FakeSonarr:
    def __init__(self, episode_files=None):
        self.episode_files = episode_files or []
        self.deleted_file_ids = []
        self.deleted_series = []
        self.unmonitored_series = []
        self.season_monitored_calls = []

    async def get_episode_files(self, series_id):
        return self.episode_files

    async def delete_episode_file(self, episode_file_id):
        self.deleted_file_ids.append(episode_file_id)
        return True

    async def set_season_monitored(self, series_id, season_numbers, monitored):
        self.season_monitored_calls.append((series_id, season_numbers, monitored))
        return True

    async def unmonitor_series(self, series_id):
        self.unmonitored_series.append(series_id)
        return True

    async def delete_series(self, series_id, delete_files=True, add_import_exclusion=False):
        self.deleted_series.append(series_id)
        return True


def _episode(rating_key="ep1", **over):
    base = dict(plex_rating_key=rating_key, title="Episode", media_type="episode",
                parent_title="Some Show", sonarr_id=42, file_path="/tv/Some Show/S01E01.mkv",
                file_size=1024 ** 3)
    base.update(over)
    return MediaItem(**base)


class EpisodeDeleteModesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add(Integration(name="sonarr", url="http://sonarr:8989", api_key="k", enabled=True))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    async def test_episode_files_mode_deletes_only_the_file(self):
        item = _episode()
        self.db.add(item)
        self.db.commit()
        fake = _FakeSonarr(episode_files=[{"id": 99, "path": item.file_path, "seasonNumber": 1}])

        with patch("app.integrations.sonarr.SonarrIntegration", return_value=fake):
            action = await propagate_and_delete(item, self.db, delete_mode="episode_files")
        self.db.commit()

        self.assertEqual(action, "deleted_episode_file")
        self.assertEqual(fake.deleted_file_ids, [99])
        self.assertEqual(fake.unmonitored_series, [])
        self.assertEqual(fake.season_monitored_calls, [])
        self.assertEqual(fake.deleted_series, [])

    async def test_unmonitor_season_mode_deletes_file_and_unmonitors_season(self):
        item = _episode()
        self.db.add(item)
        self.db.commit()
        fake = _FakeSonarr(episode_files=[{"id": 99, "path": item.file_path, "seasonNumber": 3}])

        with patch("app.integrations.sonarr.SonarrIntegration", return_value=fake):
            action = await propagate_and_delete(item, self.db, delete_mode="unmonitor_season")
        self.db.commit()

        self.assertEqual(action, "unmonitored_season")
        self.assertEqual(fake.deleted_file_ids, [99])
        self.assertEqual(fake.season_monitored_calls, [(42, {3}, False)])
        self.assertEqual(fake.unmonitored_series, [])

    async def test_unmonitor_series_mode_deletes_file_and_unmonitors_whole_series(self):
        item = _episode()
        self.db.add(item)
        self.db.commit()
        fake = _FakeSonarr(episode_files=[{"id": 99, "path": item.file_path, "seasonNumber": 1}])

        with patch("app.integrations.sonarr.SonarrIntegration", return_value=fake):
            action = await propagate_and_delete(item, self.db, delete_mode="unmonitor_series")
        self.db.commit()

        self.assertEqual(action, "unmonitored")
        self.assertEqual(fake.deleted_file_ids, [99])
        self.assertEqual(fake.unmonitored_series, [42])
        self.assertEqual(fake.season_monitored_calls, [])

    async def test_remove_from_sonarr_mode_removes_whole_series_not_just_file(self):
        item = _episode()
        self.db.add(item)
        self.db.commit()
        fake = _FakeSonarr()

        with patch("app.integrations.sonarr.SonarrIntegration", return_value=fake):
            action = await propagate_and_delete(item, self.db, delete_mode="remove_from_sonarr")
        self.db.commit()

        self.assertEqual(action, "deleted_from_arr")
        self.assertEqual(fake.deleted_series, [42])
        # Does not bother resolving/deleting the individual episode file —
        # the whole series (and its files) is being removed anyway.
        self.assertEqual(fake.deleted_file_ids, [])

    async def test_no_matching_episode_file_still_applies_monitoring_change(self):
        """File already gone from Sonarr's index (e.g. manually removed) —
        the monitoring-level action should still apply, just no file delete call."""
        item = _episode(file_path="/tv/Some Show/S01E99-not-in-sonarr.mkv")
        self.db.add(item)
        self.db.commit()
        fake = _FakeSonarr(episode_files=[{"id": 1, "path": "/tv/Some Show/S01E01.mkv", "seasonNumber": 1}])

        with patch("app.integrations.sonarr.SonarrIntegration", return_value=fake):
            action = await propagate_and_delete(item, self.db, delete_mode="unmonitor_series")
        self.db.commit()

        self.assertEqual(action, "unmonitored")
        self.assertEqual(fake.deleted_file_ids, [])
        self.assertEqual(fake.unmonitored_series, [42])

    async def test_unmonitor_season_without_resolved_season_falls_back_to_file_delete_only(self):
        item = _episode(file_path="/tv/Some Show/S01E99-not-in-sonarr.mkv")
        self.db.add(item)
        self.db.commit()
        fake = _FakeSonarr(episode_files=[])

        with patch("app.integrations.sonarr.SonarrIntegration", return_value=fake):
            action = await propagate_and_delete(item, self.db, delete_mode="unmonitor_season")
        self.db.commit()

        self.assertEqual(action, "deleted_episode_file")
        self.assertEqual(fake.season_monitored_calls, [])

    async def test_unknown_delete_mode_falls_back_to_series_default(self):
        """No integration extra_config → default is unmonitor whole series
        (existing pre-LIB-02 behavior), proving non-episode-mode callers are unaffected."""
        item = _episode()
        self.db.add(item)
        self.db.commit()
        fake = _FakeSonarr()

        with patch("app.integrations.sonarr.SonarrIntegration", return_value=fake):
            action = await propagate_and_delete(item, self.db, delete_mode=None)
        self.db.commit()

        self.assertEqual(action, "unmonitored")
        self.assertEqual(fake.unmonitored_series, [42])

    async def test_writes_deletion_log_and_removes_media_item(self):
        item = _episode()
        self.db.add(item)
        self.db.commit()
        item_id = item.id
        fake = _FakeSonarr(episode_files=[{"id": 99, "path": item.file_path, "seasonNumber": 1}])

        with patch("app.integrations.sonarr.SonarrIntegration", return_value=fake):
            await propagate_and_delete(item, self.db, delete_mode="episode_files")
        self.db.commit()

        self.assertIsNone(self.db.query(MediaItem).filter_by(id=item_id).first())
        log = self.db.query(DeletionLog).filter_by(title="Episode").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.arr_action, "deleted_episode_file")


if __name__ == "__main__":
    unittest.main()
