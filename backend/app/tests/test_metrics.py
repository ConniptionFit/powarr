import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.deletion_log import DeletionLog
from app.models.failed_import import FailedImport
from app.models.media import MediaItem
from app.services.metrics import render_metrics


class TestMetrics(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_empty_database_renders_zero_totals(self):
        out = render_metrics(self.db)
        self.assertIn("powarr_media_items 0", out)
        self.assertIn("powarr_media_bytes 0", out)
        self.assertTrue(out.endswith("\n"))

    def test_counts_and_labels(self):
        self.db.add_all([
            MediaItem(plex_rating_key="1", title="A", media_type="movie", file_size=100),
            MediaItem(plex_rating_key="2", title="B", media_type="movie", file_size=50),
            MediaItem(plex_rating_key="3", title='Q"uote', media_type="show", file_size=5),
            FailedImport(source_app="sonarr", raw_title="x", status="suggested"),
            DeletionLog(title="gone", media_type="movie", file_size=70),
        ])
        self.db.commit()
        out = render_metrics(self.db)
        self.assertIn("powarr_media_items 3", out)
        self.assertIn("powarr_media_bytes 155", out)
        self.assertIn('powarr_media_items_by_type{media_type="movie"} 2', out)
        self.assertIn('powarr_failed_imports{status="suggested"} 1', out)
        self.assertIn("powarr_deleted_bytes 70", out)


if __name__ == "__main__":
    unittest.main()
