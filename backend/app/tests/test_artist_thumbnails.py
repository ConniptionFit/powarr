"""AD-21 — library-artist thumbnail cache (services/artist_thumbnails.py) and
its overlay on the Related Artists typeahead.

- refresh_library_thumbnails: upserts from Lidarr remote posters with Deezer
  fallback for Plex-only artists; deletes rows once an artist leaves the
  library; aborts (cleanup included) when Lidarr is unreachable; never deletes
  when the library set is empty; confirmed misses aren't re-searched until the
  retry TTL passes.
- thumbnails_for: hits only, normalized-name keyed.
- search_artist_names: local thumbnails overlay Last.fm's (blank/placeholder)
  images; the known Last.fm placeholder star is treated as no image.
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ArtistThumbnail, MediaItem
from app.services.artist_discovery import _lastfm_image, search_artist_names
from app.services.artist_thumbnails import (
    _lidarr_remote_poster, refresh_library_thumbnails, thumbnails_for,
)


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _lidarr_artist(name, mbid="mbid-1", poster="https://fanart.example/poster.jpg"):
    images = [{"coverType": "poster", "remoteUrl": poster}] if poster else []
    return {"artistName": name, "foreignArtistId": mbid, "images": images}


def _index(*artists):
    from app.services.artist_discovery import _norm_artist
    by_name = {_norm_artist(a["artistName"]): a for a in artists}
    return ({}, by_name)


def _track(db, artist, key):
    db.add(MediaItem(plex_rating_key=key, title=f"{artist} song",
                     media_type="track", parent_title=artist))
    db.commit()


class RefreshTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = _db()

    async def _refresh(self, index, deezer_url=None):
        with patch("app.services.artist_thumbnails._DEEZER_PAUSE_S", 0), \
             patch("app.services.artist_discovery._lidarr_artist_index",
                   new=AsyncMock(return_value=index)), \
             patch("app.integrations.deezer.search_artist_image",
                   new=AsyncMock(return_value=deezer_url)) as deezer_mock:
            result = await refresh_library_thumbnails(self.db)
        return result, deezer_mock

    async def test_lidarr_artist_gets_remote_poster_row(self):
        result, deezer_mock = await self._refresh(_index(_lidarr_artist("Some Artist")))
        self.assertTrue(result["ok"])
        self.assertEqual(result["added"], 1)
        row = self.db.query(ArtistThumbnail).one()
        self.assertEqual(row.name_key, "some artist")
        self.assertEqual(row.image_url, "https://fanart.example/poster.jpg")
        self.assertEqual(row.source, "lidarr")
        self.assertEqual(row.musicbrainz_id, "mbid-1")
        deezer_mock.assert_not_awaited()  # Lidarr had the image — no fallback call

    async def test_plex_only_artist_falls_back_to_deezer(self):
        _track(self.db, "Plex Only", "t1")
        result, deezer_mock = await self._refresh(
            _index(), deezer_url="https://cdn.deezer.example/pic.jpg")
        self.assertEqual(result["added"], 1)
        row = self.db.query(ArtistThumbnail).one()
        self.assertEqual(row.image_url, "https://cdn.deezer.example/pic.jpg")
        self.assertEqual(row.source, "deezer")
        deezer_mock.assert_awaited_once_with("Plex Only")

    async def test_departed_artist_row_is_deleted(self):
        self.db.add(ArtistThumbnail(name_key="gone artist", artist_name="Gone Artist",
                                    image_url="https://x/img.jpg", source="lidarr"))
        self.db.commit()
        result, _ = await self._refresh(_index(_lidarr_artist("Still Here")))
        self.assertEqual(result["removed"], 1)
        keys = [r.name_key for r in self.db.query(ArtistThumbnail).all()]
        self.assertEqual(keys, ["still here"])

    async def test_lidarr_unreachable_aborts_without_cleanup(self):
        self.db.add(ArtistThumbnail(name_key="kept", artist_name="Kept",
                                    image_url="https://x/img.jpg", source="lidarr"))
        self.db.commit()
        with patch("app.services.artist_discovery._lidarr_artist_index",
                   new=AsyncMock(side_effect=RuntimeError("boom"))):
            result = await refresh_library_thumbnails(self.db)
        self.assertFalse(result["ok"])
        self.assertEqual(self.db.query(ArtistThumbnail).count(), 1)

    async def test_empty_library_never_deletes(self):
        self.db.add(ArtistThumbnail(name_key="kept", artist_name="Kept",
                                    image_url="https://x/img.jpg", source="lidarr"))
        self.db.commit()
        result, _ = await self._refresh(None)  # Lidarr not configured, no Plex music
        self.assertTrue(result["ok"])
        self.assertEqual(self.db.query(ArtistThumbnail).count(), 1)

    async def test_recent_miss_not_retried_old_miss_retried(self):
        _track(self.db, "Obscure", "t1")
        self.db.add(ArtistThumbnail(name_key="obscure", artist_name="Obscure",
                                    image_url=None, source=None,
                                    fetched_at=datetime.utcnow() - timedelta(days=1)))
        self.db.commit()
        _, deezer_mock = await self._refresh(_index(), deezer_url="https://x/found.jpg")
        deezer_mock.assert_not_awaited()

        row = self.db.query(ArtistThumbnail).one()
        row.fetched_at = datetime.utcnow() - timedelta(days=8)
        self.db.commit()
        _, deezer_mock = await self._refresh(_index(), deezer_url="https://x/found.jpg")
        deezer_mock.assert_awaited_once()
        self.assertEqual(self.db.query(ArtistThumbnail).one().image_url, "https://x/found.jpg")

    async def test_changed_lidarr_poster_updates_existing_row(self):
        self.db.add(ArtistThumbnail(name_key="some artist", artist_name="Some Artist",
                                    image_url="https://old/poster.jpg", source="lidarr"))
        self.db.commit()
        result, _ = await self._refresh(
            _index(_lidarr_artist("Some Artist", poster="https://new/poster.jpg")))
        self.assertEqual(result["updated"], 1)
        self.assertEqual(self.db.query(ArtistThumbnail).one().image_url,
                         "https://new/poster.jpg")


class HelperTests(unittest.TestCase):
    def test_lidarr_remote_poster_prefers_poster_and_ignores_relative_url(self):
        images = [
            {"coverType": "banner", "remoteUrl": "https://x/banner.jpg"},
            {"coverType": "Poster", "remoteUrl": "https://x/poster.jpg",
             "url": "/MediaCover/1/poster.jpg"},
        ]
        self.assertEqual(_lidarr_remote_poster(images), "https://x/poster.jpg")
        self.assertIsNone(_lidarr_remote_poster([{"coverType": "poster",
                                                  "url": "/MediaCover/1/poster.jpg"}]))

    def test_lastfm_placeholder_star_treated_as_no_image(self):
        images = [{"size": "large",
                   "#text": "https://lastfm.freetls.fastly.net/i/u/2a96cbd8b46e442fc41c2b86b821562f.png"}]
        self.assertIsNone(_lastfm_image(images))
        self.assertEqual(_lastfm_image([{"size": "large", "#text": "https://x/real.jpg"}]),
                         "https://x/real.jpg")

    def test_thumbnails_for_returns_hits_only(self):
        db = _db()
        db.add(ArtistThumbnail(name_key="hit", artist_name="Hit",
                               image_url="https://x/hit.jpg", source="lidarr"))
        db.add(ArtistThumbnail(name_key="miss", artist_name="Miss",
                               image_url=None, source=None))
        db.commit()
        self.assertEqual(thumbnails_for(db, ["Hit!", "Miss", "Unknown"]),
                         {"hit": "https://x/hit.jpg"})
        self.assertEqual(thumbnails_for(db, []), {})


class TypeaheadOverlayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = _db()

    async def test_local_thumbnail_overlays_lastfm_placeholder(self):
        self.db.add(ArtistThumbnail(name_key="owned artist", artist_name="Owned Artist",
                                    image_url="https://local/owned.jpg", source="lidarr"))
        self.db.commit()
        lastfm = AsyncMock()
        lastfm.search_artists = AsyncMock(return_value=[
            {"name": "Owned Artist", "mbid": "m1",
             "image": [{"size": "large",
                        "#text": "https://lastfm/2a96cbd8b46e442fc41c2b86b821562f.png"}]},
            {"name": "Stranger", "mbid": "m2", "image": []},
        ])
        lastfm.get_top_tags = AsyncMock(return_value=[])
        with patch("app.services.artist_discovery._lastfm_client", return_value=lastfm):
            result = await search_artist_names(self.db, "artist")
        self.assertTrue(result["ok"])
        by_name = {r["artist_name"]: r for r in result["results"]}
        self.assertEqual(by_name["Owned Artist"]["image_url"], "https://local/owned.jpg")
        self.assertIsNone(by_name["Stranger"]["image_url"])
