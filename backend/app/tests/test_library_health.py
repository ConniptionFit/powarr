"""Unit tests for LIB-06: library health dashboard aggregation."""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.artist_thumbnail import ArtistThumbnail
from app.models.failed_import import FailedImport
from app.models.malformed_import_flag import MalformedImportFlag
from app.models.media import MediaItem
from app.services.library_health import compute_library_health


def _item(**over):
    base = dict(title="Title", media_type="movie", file_size=1024 ** 3, ignored=False)
    base.update(over)
    return MediaItem(**base)


class LibraryHealthTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def test_empty_library(self):
        health = compute_library_health(self.db)
        self.assertEqual(health["by_type"], [])
        self.assertEqual(health["arr_link_coverage"], [])
        self.assertEqual(health["duplicate_groups"], 0)
        self.assertEqual(health["open_imports_total"], 0)
        self.assertEqual(health["pending_soft_deletes"], 0)

    def test_by_type_counts_and_sizes(self):
        self.db.add_all([
            _item(plex_rating_key="a", media_type="movie", file_size=10),
            _item(plex_rating_key="b", media_type="movie", file_size=20),
            _item(plex_rating_key="c", media_type="show", file_size=0),
        ])
        self.db.commit()
        health = compute_library_health(self.db)
        by_type = {t["media_type"]: t for t in health["by_type"]}
        self.assertEqual(by_type["movie"]["count"], 2)
        self.assertEqual(by_type["movie"]["total_size_bytes"], 30)
        self.assertEqual(by_type["show"]["count"], 1)

    def test_arr_link_coverage_only_linkable_types(self):
        self.db.add_all([
            _item(plex_rating_key="a", media_type="movie", radarr_id=5),
            _item(plex_rating_key="b", media_type="movie"),  # unlinked
            _item(plex_rating_key="c", media_type="show"),   # not a linkable type
        ])
        self.db.commit()
        health = compute_library_health(self.db)
        cov = {c["media_type"]: c for c in health["arr_link_coverage"]}
        self.assertEqual(cov["movie"]["linked"], 1)
        self.assertEqual(cov["movie"]["total"], 2)
        # show/artist/album container rows never appear — they'd read as
        # false negatives (their linkage lives on child rows).
        self.assertNotIn("show", cov)

    def test_pending_soft_deletes_excluded_everywhere(self):
        from datetime import datetime
        self.db.add_all([
            _item(plex_rating_key="a", media_type="movie", radarr_id=1),
            _item(plex_rating_key="b", media_type="movie", radarr_id=2,
                  pending_delete_at=datetime.utcnow()),
        ])
        self.db.commit()
        health = compute_library_health(self.db)
        self.assertEqual(health["pending_soft_deletes"], 1)
        by_type = {t["media_type"]: t for t in health["by_type"]}
        self.assertEqual(by_type["movie"]["count"], 1)
        cov = {c["media_type"]: c for c in health["arr_link_coverage"]}
        self.assertEqual(cov["movie"]["total"], 1)

    def test_duplicates_reuse_lib03_grouper(self):
        self.db.add_all([
            _item(plex_rating_key="a", title="Dune", year=2021, file_size=8),
            _item(plex_rating_key="b", title="Dune", year=2021, file_size=3),
        ])
        self.db.commit()
        health = compute_library_health(self.db)
        self.assertEqual(health["duplicate_groups"], 1)
        self.assertEqual(health["duplicate_reclaimable_bytes"], 3)

    def test_thumbnail_coverage_counts_null_as_confirmed_miss(self):
        self.db.add_all([
            ArtistThumbnail(name_key="aesop rock", artist_name="Aesop Rock",
                            image_url="https://x/img.jpg", source="lidarr"),
            ArtistThumbnail(name_key="obscure act", artist_name="Obscure Act",
                            image_url=None),
        ])
        self.db.commit()
        health = compute_library_health(self.db)
        self.assertEqual(health["artist_thumbnails_total"], 2)
        self.assertEqual(health["artist_thumbnails_with_image"], 1)

    def test_open_import_backlog_and_malformed_flags(self):
        self.db.add_all([
            FailedImport(source_app="sonarr", raw_title="a", status="suggested"),
            FailedImport(source_app="lidarr", raw_title="b", status="resolve_failed"),
            FailedImport(source_app="radarr", raw_title="c", status="accepted"),  # closed
            MalformedImportFlag(source_app="sonarr", download_id="d1",
                                source_title="pack", dismissed=False),
            MalformedImportFlag(source_app="sonarr", download_id="d2",
                                source_title="pack2", dismissed=True),
        ])
        self.db.commit()
        health = compute_library_health(self.db)
        self.assertEqual(health["open_imports_total"], 2)
        self.assertEqual(health["open_imports_by_status"]["suggested"], 1)
        self.assertEqual(health["open_imports_by_status"]["orphan_pending"], 0)
        self.assertEqual(health["malformed_flags_open"], 1)

    def test_protection_breakdown(self):
        self.db.add_all([
            _item(plex_rating_key="a", protected=True),
            _item(plex_rating_key="b", watch_protected=True),
            _item(plex_rating_key="c", seeding_protected=True, progress_protected=True),
            _item(plex_rating_key="d", ignored=True),
        ])
        self.db.commit()
        health = compute_library_health(self.db)
        self.assertEqual(health["protections"]["seerr_requested"], 1)
        self.assertEqual(health["protections"]["recently_watched"], 1)
        self.assertEqual(health["protections"]["seeding"], 1)
        self.assertEqual(health["protections"]["in_progress"], 1)
        self.assertEqual(health["ignored_items"], 1)


if __name__ == "__main__":
    unittest.main()
