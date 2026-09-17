"""Weekly digest: per-section toggles, new artists/playlists sections, and the
excluded_libraries consistency fix in the cleanup section."""
import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.app_setting import AppSetting
from app.models.artist_add_log import ArtistAddLog
from app.models.failed_import import FailedImport
from app.models.media import MediaItem
from app.models.smart_playlist import SmartPlaylist
from app.services.digest import build_digest_message


class DigestTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def _set_notifications(self, **overrides):
        base = {
            "digest_include_imports": True, "digest_include_artists": True,
            "digest_include_playlists": True, "digest_include_cleanup": True,
        }
        base.update(overrides)
        self.db.add(AppSetting(key="notifications", value=json.dumps(base)))
        self.db.commit()

    def test_all_sections_on_by_default(self):
        msg = build_digest_message(self.db)
        self.assertIn("Failed imports open", msg)
        self.assertIn("artists added", msg)
        self.assertIn("playlists created", msg)
        self.assertIn("Deletion candidates", msg)

    def test_toggling_a_section_off_removes_it(self):
        self._set_notifications(digest_include_imports=False)
        msg = build_digest_message(self.db)
        self.assertNotIn("Failed imports", msg)
        self.assertIn("artists added", msg)

    def test_all_sections_off_gives_a_clear_message(self):
        self._set_notifications(digest_include_imports=False, digest_include_artists=False,
                                 digest_include_playlists=False, digest_include_cleanup=False)
        msg = build_digest_message(self.db)
        self.assertIn("No digest sections enabled", msg)

    def test_artists_added_last_7d_counted_and_named(self):
        recent = datetime.utcnow() - timedelta(days=2)
        stale = datetime.utcnow() - timedelta(days=10)
        self.db.add_all([
            ArtistAddLog(artist_name="Fresh Artist", source="discovery", added_at=recent),
            ArtistAddLog(artist_name="Old Artist", source="related", added_at=stale),
        ])
        self.db.commit()
        msg = build_digest_message(self.db)
        self.assertIn("Last 7d artists added: 1", msg)
        self.assertIn("Fresh Artist", msg)
        self.assertNotIn("Old Artist", msg)

    def test_playlists_created_last_7d_counted_and_named(self):
        recent = datetime.utcnow() - timedelta(days=1)
        stale = datetime.utcnow() - timedelta(days=30)
        self.db.add_all([
            SmartPlaylist(genre_tag="rock", title="Rock Hits", plex_playlist_id="1",
                          plex_created_at=recent),
            SmartPlaylist(genre_tag="jazz", title="Jazz Classics", plex_playlist_id="2",
                          plex_created_at=stale),
            SmartPlaylist(genre_tag="pop", title="Draft Only", plex_playlist_id=None,
                          plex_created_at=None),
        ])
        self.db.commit()
        msg = build_digest_message(self.db)
        self.assertIn("Last 7d playlists created: 1", msg)
        self.assertIn("Rock Hits", msg)
        self.assertNotIn("Jazz Classics", msg)
        self.assertNotIn("Draft Only", msg)

    def test_cleanup_section_respects_excluded_libraries(self):
        self.db.add(MediaItem(plex_rating_key="rk1", title="Item", media_type="movie",
                              library_section="Movies", file_size=100, score=90.0, ignored=False))
        self.db.add(AppSetting(key="cleanup", value=json.dumps({"excluded_libraries": ["Movies"]})))
        self.db.commit()
        msg = build_digest_message(self.db)
        self.assertIn("Deletion candidates above threshold: 0", msg)


if __name__ == "__main__":
    unittest.main()
