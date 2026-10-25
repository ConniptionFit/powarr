import unittest
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.playlists import router
from app.database import get_db
from app.models.media import MediaItem
from app.models.smart_playlist import SmartPlaylist, SmartPlaylistTrack
from app.tests.test_lib07_to_11 import _ApiCase


class M3uTests(_ApiCase):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        db = self.db
        app.dependency_overrides[get_db] = lambda: db
        self.pc = TestClient(app)

    def test_export(self):
        pl = SmartPlaylist(genre_tag="rock", title="Road Trip: Rock!")
        self.db.add(pl)
        self.db.flush()
        self.db.add(MediaItem(plex_rating_key="10", title="Song", media_type="track",
                              file_path="/music/a/song.flac"))
        now = datetime.utcnow()
        self.db.add(SmartPlaylistTrack(playlist_id=pl.id, plex_key="10", artist_name="Band",
                                       track_title="Song", added_at=now))
        self.db.add(SmartPlaylistTrack(playlist_id=pl.id, plex_key="gone", artist_name="Ghost",
                                       track_title="Missing", added_at=now + timedelta(seconds=1)))
        self.db.commit()
        r = self.pc.get(f"/api/v1/smart-playlists/{pl.id}/export.m3u")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Road-Trip--Rock", r.headers["content-disposition"])
        lines = r.text.splitlines()
        self.assertEqual(lines[0], "#EXTM3U")
        self.assertIn("#EXTINF:-1,Band - Song", lines)
        self.assertEqual(lines[lines.index("#EXTINF:-1,Band - Song") + 1], "/music/a/song.flac")
        self.assertEqual(lines[-1], "#EXTINF:-1,Ghost - Missing")  # no path -> no file line

    def test_404(self):
        self.assertEqual(self.pc.get("/api/v1/smart-playlists/999/export.m3u").status_code, 404)
