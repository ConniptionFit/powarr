"""Unit tests for SP-13: prune stale tracks (blacklisted artist or track left
Plex) from a Managed Smart Playlist, both the real Plex playlist and the
local ledger."""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.media import MediaItem
from app.models.smart_playlist import SmartPlaylist, SmartPlaylistTrack
from app.schemas.settings import SmartPlaylistSettings
from app.services.playlist_generator import prune_stale_tracks


class _FakePlex:
    def __init__(self, items, *, remove_ok=True):
        self._items = items  # list of {"ratingKey": ..., "playlistItemID": ...}
        self._remove_ok = remove_ok
        self.removed_calls: list[tuple[str, object]] = []

    async def get_playlist_items(self, playlist_rating_key):
        return self._items

    async def remove_from_playlist(self, playlist_rating_key, playlist_item_id):
        self.removed_calls.append((playlist_rating_key, playlist_item_id))
        return self._remove_ok


class PruneStaleTracksTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.pl = SmartPlaylist(genre_tag="rock", title="Rock", plex_playlist_id="999",
                                enabled=True, track_count=3)
        self.db.add(self.pl)
        self.db.commit()
        self.cfg = SmartPlaylistSettings(prune_stale_tracks_enabled=True,
                                         blacklisted_artists=["Bad Artist"])

    def tearDown(self):
        self.db.close()

    async def test_blacklisted_artist_track_pruned(self):
        self.db.add(SmartPlaylistTrack(playlist_id=self.pl.id, plex_key="k1", artist_name="Bad Artist"))
        self.db.add(MediaItem(plex_rating_key="k1", title="T", media_type="track"))
        self.db.commit()
        plex = _FakePlex([{"ratingKey": "k1", "playlistItemID": 111}])

        removed = await prune_stale_tracks(self.db, plex, self.pl, self.cfg)

        self.assertEqual(removed, 1)
        self.assertEqual(plex.removed_calls, [("999", 111)])
        self.assertEqual(self.db.query(SmartPlaylistTrack).count(), 0)
        self.assertEqual(self.pl.track_count, 2)

    async def test_track_gone_from_plex_pruned(self):
        # No MediaItem row for this plex_key — track has left the synced library.
        self.db.add(SmartPlaylistTrack(playlist_id=self.pl.id, plex_key="k2", artist_name="Good Artist"))
        self.db.commit()
        plex = _FakePlex([{"ratingKey": "k2", "playlistItemID": 222}])

        removed = await prune_stale_tracks(self.db, plex, self.pl, self.cfg)

        self.assertEqual(removed, 1)
        self.assertEqual(plex.removed_calls, [("999", 222)])

    async def test_healthy_track_not_pruned(self):
        self.db.add(SmartPlaylistTrack(playlist_id=self.pl.id, plex_key="k3", artist_name="Good Artist"))
        self.db.add(MediaItem(plex_rating_key="k3", title="T", media_type="track"))
        self.db.commit()
        plex = _FakePlex([{"ratingKey": "k3", "playlistItemID": 333}])

        removed = await prune_stale_tracks(self.db, plex, self.pl, self.cfg)

        self.assertEqual(removed, 0)
        self.assertEqual(plex.removed_calls, [])
        self.assertEqual(self.db.query(SmartPlaylistTrack).count(), 1)

    async def test_missing_from_plex_playlist_still_prunes_ledger(self):
        # Ledger row exists but Plex's own playlist items list doesn't have it
        # (already removed some other way) — still clean up the stale ledger row.
        self.db.add(SmartPlaylistTrack(playlist_id=self.pl.id, plex_key="k4", artist_name="Bad Artist"))
        self.db.commit()
        plex = _FakePlex([])  # empty — k4 not found

        removed = await prune_stale_tracks(self.db, plex, self.pl, self.cfg)

        self.assertEqual(removed, 1)
        self.assertEqual(plex.removed_calls, [])  # never called — nothing to remove there

    async def test_no_plex_playlist_id_no_op(self):
        pl = SmartPlaylist(genre_tag="jazz", title="Jazz", plex_playlist_id=None, enabled=True)
        self.db.add(pl)
        self.db.commit()
        removed = await prune_stale_tracks(self.db, _FakePlex([]), pl, self.cfg)
        self.assertEqual(removed, 0)

    async def test_no_stale_tracks_skips_plex_items_fetch(self):
        self.db.add(SmartPlaylistTrack(playlist_id=self.pl.id, plex_key="k5", artist_name="Good Artist"))
        self.db.add(MediaItem(plex_rating_key="k5", title="T", media_type="track"))
        self.db.commit()

        class _NoFetchPlex(_FakePlex):
            async def get_playlist_items(self, playlist_rating_key):
                raise AssertionError("should not fetch playlist items when nothing is stale")

        removed = await prune_stale_tracks(self.db, _NoFetchPlex([]), self.pl, self.cfg)
        self.assertEqual(removed, 0)


if __name__ == "__main__":
    unittest.main()
