"""Related Artists search (ad-hoc, read-only artist-similarity lookup) and the
_add_artist_to_lidarr() helper extracted from add_to_lidarr() to share with it.

- _add_artist_to_lidarr: lookup -> profile resolution -> add, including the
  "400 means already there, not a failure" behavior carried over verbatim from
  the pre-extraction add_to_lidarr().
- search_related_artists: fails soft with a clear message when there's no
  query, no Last.fm, or no results; flags already-owned results without
  filtering them out; never touches Qdrant or DiscoveredArtist.
"""
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.schemas.settings import ArtistDiscoverySettings
from app.services.artist_discovery import (
    _add_artist_to_lidarr, add_related_artist, add_to_lidarr, search_related_artists,
    search_related_artists_tracked,
)


class _FakeLidarr:
    def __init__(self, lookup_results=None, add_response=None, add_raises=None,
                existing_artists=None):
        self._lookup_results = lookup_results if lookup_results is not None else [
            {"foreignArtistId": "mbid-1", "artistName": "Some Artist"}]
        self._add_response = add_response if add_response is not None else {"id": 42}
        self._add_raises = add_raises
        self._existing = existing_artists or []

    async def lookup_artist(self, term):
        return self._lookup_results

    async def get_root_folders(self):
        return [{"path": "/music"}]

    async def get_quality_profiles(self):
        return [{"id": 1}]

    async def get_metadata_profiles(self):
        return [{"id": 1}]

    async def add_artist(self, payload):
        if self._add_raises:
            raise self._add_raises
        return self._add_response

    async def get_artists(self):
        return self._existing


def _cfg(**overrides):
    return ArtistDiscoverySettings(root_folder_path="/music", quality_profile_id=1,
                                   metadata_profile_id=1, **overrides)


class AddArtistToLidarrTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_lidarr_id(self):
        lidarr = _FakeLidarr()
        result = await _add_artist_to_lidarr(lidarr, _cfg(), "mbid-1", "Some Artist")
        self.assertTrue(result["ok"])
        self.assertEqual(result["lidarr_artist_id"], 42)
        self.assertFalse(result["already_existed"])

    async def test_no_lookup_results_fails(self):
        lidarr = _FakeLidarr(lookup_results=[])
        result = await _add_artist_to_lidarr(lidarr, _cfg(), None, "Nobody")
        self.assertFalse(result["ok"])
        self.assertIn("No Lidarr lookup results", result["message"])

    async def test_missing_profile_defaults_fails_when_lidarr_has_none_available(self):
        class _NoProfilesLidarr(_FakeLidarr):
            async def get_root_folders(self):
                return []
            async def get_quality_profiles(self):
                return []
            async def get_metadata_profiles(self):
                return []
        lidarr = _NoProfilesLidarr()
        result = await _add_artist_to_lidarr(lidarr, ArtistDiscoverySettings(), "mbid-1", "Some Artist")
        self.assertFalse(result["ok"])
        self.assertIn("root folder", result["message"])

    async def test_400_conflict_treated_as_success_with_existing_id(self):
        resp = httpx.Response(400, request=httpx.Request("POST", "http://lidarr/api/v1/artist"))
        lidarr = _FakeLidarr(
            add_raises=httpx.HTTPStatusError("bad request", request=resp.request, response=resp),
            existing_artists=[{"foreignArtistId": "mbid-1", "artistName": "Some Artist", "id": 99}])
        result = await _add_artist_to_lidarr(lidarr, _cfg(), "mbid-1", "Some Artist")
        self.assertTrue(result["ok"])
        self.assertEqual(result["lidarr_artist_id"], 99)
        self.assertTrue(result["already_existed"])

    async def test_other_http_error_fails(self):
        resp = httpx.Response(500, request=httpx.Request("POST", "http://lidarr/api/v1/artist"))
        lidarr = _FakeLidarr(
            add_raises=httpx.HTTPStatusError("server error", request=resp.request, response=resp))
        result = await _add_artist_to_lidarr(lidarr, _cfg(), "mbid-1", "Some Artist")
        self.assertFalse(result["ok"])


class SearchRelatedArtistsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    async def test_empty_query_short_circuits(self):
        result = await search_related_artists(self.db, "  ")
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"], [])

    async def test_no_lastfm_configured(self):
        with patch("app.services.artist_discovery._lastfm_client", return_value=None):
            result = await search_related_artists(self.db, "Radiohead")
        self.assertFalse(result["ok"])
        self.assertIn("Last.fm", result["message"])

    async def test_no_results_found_gives_clear_message(self):
        lastfm = AsyncMock()
        lastfm.get_similar_artists = AsyncMock(return_value=[])
        with patch("app.services.artist_discovery._lastfm_client", return_value=lastfm):
            result = await search_related_artists(self.db, "Xyzzyplugh")
        self.assertFalse(result["ok"])
        self.assertIn("No related artists found", result["message"])

    async def test_flags_already_owned_without_dropping_it(self):
        lastfm = AsyncMock()
        lastfm.get_similar_artists = AsyncMock(return_value=[
            {"name": "Owned Artist", "mbid": "mbid-owned", "match": "0.9"},
            {"name": "New Artist", "mbid": "mbid-new", "match": "0.5"},
        ])
        with patch("app.services.artist_discovery._lastfm_client", return_value=lastfm), \
             patch("app.services.artist_discovery._lidarr_artist_index",
                   new=AsyncMock(return_value=({"mbid-owned": {"id": 1}}, {}))), \
             patch("app.services.artist_discovery._enrich_candidate",
                   new=AsyncMock(return_value={"image_url": None, "bio": None,
                                               "genres": [], "years_active": None})):
            result = await search_related_artists(self.db, "Seed Artist")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["results"]), 2)
        owned = next(r for r in result["results"] if r["artist_name"] == "Owned Artist")
        new = next(r for r in result["results"] if r["artist_name"] == "New Artist")
        self.assertTrue(owned["already_owned"])
        self.assertFalse(new["already_owned"])
        self.assertAlmostEqual(owned["match_score"], 0.9)


class SearchRelatedArtistsTrackedTests(unittest.IsolatedAsyncioTestCase):
    """search_related_artists_tracked() is the tray-tracked entry point the API
    route calls -- a "related_search" task should exist with determinate
    current/total progress once enrichment starts, and finish done/failed to
    match the underlying search's outcome."""

    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        from app.services import tasks
        tasks._tasks.clear()  # module-level tray state is process-global, not per-test

    def _only_task(self):
        from app.services import tasks
        matches = [t for t in tasks._tasks.values() if t.kind == "related_search"]
        self.assertEqual(len(matches), 1)
        return matches[0]

    async def test_successful_search_finishes_task_done_with_full_progress(self):
        lastfm = AsyncMock()
        lastfm.get_similar_artists = AsyncMock(return_value=[
            {"name": "Artist One", "mbid": "mbid-1", "match": "0.9"},
            {"name": "Artist Two", "mbid": "mbid-2", "match": "0.8"},
        ])
        with patch("app.services.artist_discovery._lastfm_client", return_value=lastfm), \
             patch("app.services.artist_discovery._lidarr_artist_index",
                   new=AsyncMock(return_value=({}, {}))), \
             patch("app.services.artist_discovery._enrich_candidate",
                   new=AsyncMock(return_value={"image_url": None, "bio": None,
                                               "genres": [], "years_active": None})):
            result = await search_related_artists_tracked(self.db, "Seed Artist")
        self.assertTrue(result["ok"])
        task = self._only_task()
        self.assertEqual(task.status, "done")
        self.assertEqual(task.current, 2)
        self.assertEqual(task.total, 2)

    async def test_failed_search_finishes_task_failed(self):
        with patch("app.services.artist_discovery._lastfm_client", return_value=None):
            result = await search_related_artists_tracked(self.db, "Seed Artist")
        self.assertFalse(result["ok"])
        task = self._only_task()
        self.assertEqual(task.status, "failed")
        self.assertIn("Last.fm", task.message)


class _FakePlex:
    def __init__(self, seed=None, sonic=None, related=None):
        self._seed = seed
        self._sonic = sonic or []
        self._related = related or []

    async def find_artist(self, name):
        return self._seed

    async def sonically_similar_artists(self, rating_key, **kwargs):
        return self._sonic

    async def related_artists(self, rating_key, **kwargs):
        return self._related


class PlexSimilarityAugmentTests(unittest.IsolatedAsyncioTestCase):
    """AD-14: Plex Sonic Analysis + Related-hub augmentation on top of Last.fm."""

    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    async def _search(self, plex, lastfm_results=None):
        lastfm = AsyncMock()
        lastfm.get_similar_artists = AsyncMock(return_value=lastfm_results or [
            {"name": "New Artist", "mbid": "mbid-new", "match": "0.5"},
        ])
        with patch("app.services.artist_discovery._lastfm_client", return_value=lastfm), \
             patch("app.services.artist_discovery._lidarr_artist_index",
                   new=AsyncMock(return_value=({}, {}))), \
             patch("app.services.artist_discovery._plex_client", return_value=plex), \
             patch("app.services.artist_discovery._enrich_candidate",
                   new=AsyncMock(return_value={"image_url": None, "bio": None,
                                               "genres": [], "years_active": None})):
            return await search_related_artists(self.db, "Seed Artist")

    async def test_no_plex_configured_leaves_lastfm_only_sources(self):
        result = await self._search(plex=None)
        self.assertEqual(result["results"][0]["similarity_sources"], ["lastfm"])

    async def test_seed_artist_not_found_in_plex_is_a_noop(self):
        plex = _FakePlex(seed=None, sonic=["Some Other Artist"])
        result = await self._search(plex=plex)
        self.assertEqual(result["results"][0]["similarity_sources"], ["lastfm"])
        self.assertEqual(len(result["results"]), 1)

    async def test_sonic_match_on_existing_result_adds_source_badge(self):
        plex = _FakePlex(seed={"ratingKey": "123", "title": "Seed Artist"},
                         sonic=["New Artist"])
        result = await self._search(plex=plex)
        self.assertEqual(len(result["results"]), 1)
        self.assertIn("plex_sonic", result["results"][0]["similarity_sources"])
        self.assertIn("lastfm", result["results"][0]["similarity_sources"])

    async def test_related_hub_new_name_creates_plex_only_entry(self):
        plex = _FakePlex(seed={"ratingKey": "123", "title": "Seed Artist"},
                         related=["Plex Only Artist"])
        result = await self._search(plex=plex)
        self.assertEqual(len(result["results"]), 2)
        plex_only = next(r for r in result["results"] if r["artist_name"] == "Plex Only Artist")
        self.assertEqual(plex_only["similarity_sources"], ["plex_similar"])
        self.assertIsNone(plex_only["musicbrainz_id"])
        self.assertEqual(plex_only["match_score"], 0.0)

    async def test_seed_artist_itself_is_excluded_from_plex_results(self):
        plex = _FakePlex(seed={"ratingKey": "123", "title": "Seed Artist"},
                         sonic=["Seed Artist"])
        result = await self._search(plex=plex)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["artist_name"], "New Artist")

    async def test_both_plex_sources_can_badge_the_same_new_entry(self):
        plex = _FakePlex(seed={"ratingKey": "123", "title": "Seed Artist"},
                         sonic=["Plex Artist"], related=["Plex Artist"])
        result = await self._search(plex=plex)
        plex_entry = next(r for r in result["results"] if r["artist_name"] == "Plex Artist")
        self.assertEqual(set(plex_entry["similarity_sources"]), {"plex_sonic", "plex_similar"})


class AddToLidarrDiscoveryFlowTests(unittest.IsolatedAsyncioTestCase):
    """add_to_lidarr() is the Discovery-queue accept path -- shares
    _add_artist_to_lidarr() with add_related_artist() above, so it should log
    the same way (genuine add logged, already-existed not logged)."""

    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def _make_candidate(self):
        from app.models.artist_discovery import DiscoveredArtist
        cand = DiscoveredArtist(musicbrainz_id="mbid-1", artist_name="Some Artist", status="pending")
        self.db.add(cand)
        self.db.commit()
        return cand.id

    async def _accept_with(self, fake_lidarr):
        from app.models.integration import Integration
        self.db.add(Integration(name="lidarr", enabled=True, url="http://lidarr"))
        self.db.commit()
        cand_id = self._make_candidate()
        with patch("app.api.v1.integrations._get_client", return_value=fake_lidarr):
            return await add_to_lidarr(self.db, cand_id)

    async def test_genuine_add_writes_artist_add_log(self):
        from app.models.artist_add_log import ArtistAddLog
        result = await self._accept_with(_FakeLidarr())
        self.assertTrue(result["ok"])
        logs = self.db.query(ArtistAddLog).all()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].source, "discovery")

    async def test_already_existed_add_does_not_write_artist_add_log(self):
        from app.models.artist_add_log import ArtistAddLog
        resp = httpx.Response(400, request=httpx.Request("POST", "http://lidarr/api/v1/artist"))
        lidarr = _FakeLidarr(
            add_raises=httpx.HTTPStatusError("bad request", request=resp.request, response=resp),
            existing_artists=[{"foreignArtistId": "mbid-1", "artistName": "Some Artist", "id": 99}])
        result = await self._accept_with(lidarr)
        self.assertTrue(result["ok"])
        self.assertEqual(self.db.query(ArtistAddLog).count(), 0)


class AddRelatedArtistTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    async def test_blank_name_rejected(self):
        result = await add_related_artist(self.db, "mbid-1", "  ")
        self.assertFalse(result["ok"])

    async def test_no_lidarr_configured(self):
        result = await add_related_artist(self.db, "mbid-1", "Some Artist")
        self.assertFalse(result["ok"])

    async def _with_lidarr_configured(self, fake_lidarr):
        from app.models.integration import Integration
        self.db.add(Integration(name="lidarr", enabled=True, url="http://lidarr"))
        self.db.commit()
        with patch("app.api.v1.integrations._get_client", return_value=fake_lidarr):
            return await add_related_artist(self.db, "mbid-1", "Some Artist")

    async def test_genuine_add_writes_artist_add_log(self):
        from app.models.artist_add_log import ArtistAddLog
        result = await self._with_lidarr_configured(_FakeLidarr())
        self.assertTrue(result["ok"])
        logs = self.db.query(ArtistAddLog).all()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].source, "related")
        self.assertEqual(logs[0].artist_name, "Some Artist")

    async def test_already_existed_add_does_not_write_artist_add_log(self):
        from app.models.artist_add_log import ArtistAddLog
        resp = httpx.Response(400, request=httpx.Request("POST", "http://lidarr/api/v1/artist"))
        lidarr = _FakeLidarr(
            add_raises=httpx.HTTPStatusError("bad request", request=resp.request, response=resp),
            existing_artists=[{"foreignArtistId": "mbid-1", "artistName": "Some Artist", "id": 99}])
        result = await self._with_lidarr_configured(lidarr)
        self.assertTrue(result["ok"])
        self.assertTrue(result["already_existed"])
        self.assertEqual(self.db.query(ArtistAddLog).count(), 0)
        self.assertIn("Lidarr", result["message"])


if __name__ == "__main__":
    unittest.main()
