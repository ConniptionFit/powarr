"""Unit tests for LIB-01: deletion dry-run / impact preview."""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.integration import Integration
from app.models.media import MediaItem
from app.schemas.settings import CleanupSettings
from app.services.deletion_preview import build_deletion_preview


def _item(**over):
    base = dict(plex_rating_key=over.pop("plex_rating_key", None) or f"k{id(over)}",
                title="Title", media_type="movie", file_size=1024 ** 3)
    base.update(over)
    return MediaItem(**base)


class BuildDeletionPreviewTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.cleanup = CleanupSettings()

    def tearDown(self):
        self.db.close()

    def test_missing_ids_counted_and_do_not_crash(self):
        preview = build_deletion_preview(self.db, [999], self.cleanup)
        self.assertEqual(preview.total_items, 0)
        self.assertEqual(preview.missing_count, 1)
        self.assertEqual(preview.total_size_bytes, 0)

    def test_totals_and_soft_delete_echo(self):
        a = _item(plex_rating_key="a", title="A", file_size=2 * 1024 ** 3)
        b = _item(plex_rating_key="b", title="B", file_size=3 * 1024 ** 3)
        self.db.add_all([a, b])
        self.db.commit()
        cleanup = CleanupSettings(soft_delete_days=7)
        preview = build_deletion_preview(self.db, [a.id, b.id], cleanup)
        self.assertEqual(preview.total_items, 2)
        self.assertEqual(preview.total_size_bytes, 5 * 1024 ** 3)
        self.assertEqual(preview.soft_delete_days, 7)
        self.assertTrue(preview.would_pend)

    def test_immediate_delete_when_soft_delete_off(self):
        a = _item(plex_rating_key="a")
        self.db.add(a)
        self.db.commit()
        preview = build_deletion_preview(self.db, [a.id], CleanupSettings(soft_delete_days=0))
        self.assertFalse(preview.would_pend)

    def test_protected_count(self):
        a = _item(plex_rating_key="a", protected=True)
        b = _item(plex_rating_key="b", watch_protected=True)
        c = _item(plex_rating_key="c", seeding_protected=True)
        d = _item(plex_rating_key="d", progress_protected=True)
        e = _item(plex_rating_key="e")
        self.db.add_all([a, b, c, d, e])
        self.db.commit()
        preview = build_deletion_preview(self.db, [a.id, b.id, c.id, d.id, e.id], self.cleanup)
        self.assertEqual(preview.protected_count, 4)
        self.assertTrue(preview.items[3].progress_protected)

    def test_no_arr_action_without_integration(self):
        a = _item(plex_rating_key="a", media_type="movie", radarr_id=5)
        self.db.add(a)
        self.db.commit()
        preview = build_deletion_preview(self.db, [a.id], self.cleanup)
        self.assertEqual(preview.items[0].arr_app, "radarr")
        self.assertEqual(preview.items[0].arr_action, "none")
        self.assertIsNone(preview.items[0].cascade_warning)

    def test_unmonitor_action_default_extra_config(self):
        self.db.add(Integration(name="radarr", enabled=True, url="http://r"))
        a = _item(plex_rating_key="a", media_type="movie", radarr_id=5)
        self.db.add(a)
        self.db.commit()
        preview = build_deletion_preview(self.db, [a.id], self.cleanup)
        self.assertEqual(preview.items[0].arr_action, "unmonitor")

    def test_delete_from_arr_action_when_configured(self):
        import json
        self.db.add(Integration(name="radarr", enabled=True, url="http://r",
                                 extra_config=json.dumps({"delete_from_arr_list": True})))
        a = _item(plex_rating_key="a", media_type="movie", radarr_id=5)
        self.db.add(a)
        self.db.commit()
        preview = build_deletion_preview(self.db, [a.id], self.cleanup)
        self.assertEqual(preview.items[0].arr_action, "delete_from_arr")

    def test_series_cascade_warning_when_sibling_episodes_excluded(self):
        """Deleting one Sonarr-linked episode unmonitors/deletes the WHOLE
        series — the preview must flag this when other episodes of that same
        series exist outside the selection."""
        self.db.add(Integration(name="sonarr", enabled=True, url="http://s"))
        ep1 = _item(plex_rating_key="e1", media_type="episode", sonarr_id=42,
                    parent_title="Show", title="S01E01")
        ep2 = _item(plex_rating_key="e2", media_type="episode", sonarr_id=42,
                    parent_title="Show", title="S01E02")
        self.db.add_all([ep1, ep2])
        self.db.commit()

        # Only ep1 is being deleted — ep2 is a sibling left behind.
        preview = build_deletion_preview(self.db, [ep1.id], self.cleanup)
        self.assertIsNotNone(preview.items[0].cascade_warning)
        self.assertIn("series", preview.items[0].cascade_warning)
        self.assertIn("Sonarr", preview.items[0].cascade_warning)
        self.assertIn("1 other item", preview.items[0].cascade_warning)

    def test_no_cascade_warning_when_all_siblings_included(self):
        self.db.add(Integration(name="sonarr", enabled=True, url="http://s"))
        ep1 = _item(plex_rating_key="e1", media_type="episode", sonarr_id=42)
        ep2 = _item(plex_rating_key="e2", media_type="episode", sonarr_id=42)
        self.db.add_all([ep1, ep2])
        self.db.commit()

        preview = build_deletion_preview(self.db, [ep1.id, ep2.id], self.cleanup)
        for item in preview.items:
            self.assertIsNone(item.cascade_warning)

    def test_no_cascade_warning_when_arr_action_is_none(self):
        # No sonarr integration row → arr_action stays "none" → no warning even
        # though a sibling exists, since nothing will actually be touched.
        ep1 = _item(plex_rating_key="e1", media_type="episode", sonarr_id=42)
        ep2 = _item(plex_rating_key="e2", media_type="episode", sonarr_id=42)
        self.db.add_all([ep1, ep2])
        self.db.commit()

        preview = build_deletion_preview(self.db, [ep1.id], self.cleanup)
        self.assertIsNone(preview.items[0].cascade_warning)


class DeleteModePreviewTests(unittest.TestCase):
    """LIB-02 — the preview reflects whichever explicit episode delete_mode
    was requested instead of always describing the extra_config default."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.cleanup = CleanupSettings()
        self.db.add(Integration(name="sonarr", enabled=True, url="http://s"))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_episode_files_mode_has_no_cascade_warning(self):
        ep1 = _item(plex_rating_key="e1", media_type="episode", sonarr_id=42)
        ep2 = _item(plex_rating_key="e2", media_type="episode", sonarr_id=42)
        self.db.add_all([ep1, ep2])
        self.db.commit()

        preview = build_deletion_preview(self.db, [ep1.id], self.cleanup, delete_mode="episode_files")
        self.assertEqual(preview.items[0].arr_action, "delete_episode_file")
        self.assertIsNone(preview.items[0].cascade_warning)

    def test_unmonitor_season_mode_warns_with_season_caveat(self):
        ep1 = _item(plex_rating_key="e1", media_type="episode", sonarr_id=42)
        ep2 = _item(plex_rating_key="e2", media_type="episode", sonarr_id=42)
        self.db.add_all([ep1, ep2])
        self.db.commit()

        preview = build_deletion_preview(self.db, [ep1.id], self.cleanup, delete_mode="unmonitor_season")
        self.assertEqual(preview.items[0].arr_action, "unmonitor_season")
        self.assertIn("season", preview.items[0].cascade_warning)
        self.assertIn("1 other item", preview.items[0].cascade_warning)

    def test_unmonitor_series_mode_warns_about_whole_series(self):
        ep1 = _item(plex_rating_key="e1", media_type="episode", sonarr_id=42)
        ep2 = _item(plex_rating_key="e2", media_type="episode", sonarr_id=42)
        self.db.add_all([ep1, ep2])
        self.db.commit()

        preview = build_deletion_preview(self.db, [ep1.id], self.cleanup, delete_mode="unmonitor_series")
        self.assertEqual(preview.items[0].arr_action, "unmonitored")
        self.assertIn("entire series", preview.items[0].cascade_warning)

    def test_remove_from_sonarr_mode_warns_about_removal(self):
        ep1 = _item(plex_rating_key="e1", media_type="episode", sonarr_id=42)
        ep2 = _item(plex_rating_key="e2", media_type="episode", sonarr_id=42)
        self.db.add_all([ep1, ep2])
        self.db.commit()

        preview = build_deletion_preview(self.db, [ep1.id], self.cleanup, delete_mode="remove_from_sonarr")
        self.assertEqual(preview.items[0].arr_action, "deleted_from_arr")
        self.assertIn("remove the entire series", preview.items[0].cascade_warning)

    def test_no_warning_when_no_siblings_left_out(self):
        ep1 = _item(plex_rating_key="e1", media_type="episode", sonarr_id=42)
        self.db.add(ep1)
        self.db.commit()

        preview = build_deletion_preview(self.db, [ep1.id], self.cleanup, delete_mode="unmonitor_series")
        self.assertIsNone(preview.items[0].cascade_warning)

    def test_movie_ignores_delete_mode(self):
        """delete_mode is Sonarr-episode-specific — a movie's preview is
        unaffected even if a delete_mode string is passed."""
        movie = _item(plex_rating_key="m1", media_type="movie", radarr_id=5)
        self.db.add(movie)
        self.db.commit()

        preview = build_deletion_preview(self.db, [movie.id], self.cleanup, delete_mode="unmonitor_series")
        self.assertEqual(preview.items[0].arr_action, "none")  # no radarr integration configured


if __name__ == "__main__":
    unittest.main()
