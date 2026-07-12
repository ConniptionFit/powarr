"""GET /media/stats's candidates_above_threshold must match what GET /media
(list_media) would actually return -- they were found to disagree in
production: excluded_libraries was applied to the list query but not the
stats count, so the dashboard showed a nonzero candidate count while the
Deletion Suggestions page (which calls list_media) rendered nothing whenever
every library happened to be excluded."""
import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.app_setting import AppSetting
from app.models.media import MediaItem
from app.api.v1.media import get_stats, list_media


class ExcludedLibrariesStatsConsistencyTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def _add_item(self, library_section, score=80.0):
        item = MediaItem(
            plex_rating_key=f"rk-{library_section}-{score}",
            title="Some Item", media_type="movie",
            library_section=library_section, file_size=1000,
            score=score, ignored=False,
        )
        self.db.add(item)
        self.db.commit()

    def _set_excluded_libraries(self, libraries):
        self.db.add(AppSetting(key="cleanup", value=json.dumps({"excluded_libraries": libraries})))
        self.db.commit()

    def test_all_libraries_excluded_yields_zero_candidates_in_stats_too(self):
        self._add_item("Movies")
        self._set_excluded_libraries(["Movies"])
        stats = get_stats(db=self.db)
        listed = list_media(db=self.db, media_type=None, min_score=None, ignored=False,
                            include_protected=False, pending=False, sort_by="score",
                            order="desc", limit=200, offset=0)
        self.assertEqual(stats.candidates_above_threshold, 0)
        self.assertEqual(len(listed), 0)

    def test_partial_exclusion_only_counts_non_excluded_libraries(self):
        self._add_item("Movies")
        self._add_item("TV Shows")
        self._set_excluded_libraries(["Movies"])
        stats = get_stats(db=self.db)
        listed = list_media(db=self.db, media_type=None, min_score=None, ignored=False,
                            include_protected=False, pending=False, sort_by="score",
                            order="desc", limit=200, offset=0)
        self.assertEqual(stats.candidates_above_threshold, 1)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].library_section, "TV Shows")

    def test_no_exclusions_counts_everything_above_threshold(self):
        self._add_item("Movies")
        self._add_item("TV Shows")
        stats = get_stats(db=self.db)
        listed = list_media(db=self.db, media_type=None, min_score=None, ignored=False,
                            include_protected=False, pending=False, sort_by="score",
                            order="desc", limit=200, offset=0)
        self.assertEqual(stats.candidates_above_threshold, 2)
        self.assertEqual(len(listed), 2)


if __name__ == "__main__":
    unittest.main()
