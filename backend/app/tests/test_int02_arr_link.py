"""Unit tests for INT-02: manual *arr ID override (search + link/clear)."""
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.integration import Integration
from app.services.arr_link import (
    APP_FOR_MEDIA_TYPE, ID_FIELD_FOR_MEDIA_TYPE, search_arr_candidates,
)


class MappingTests(unittest.TestCase):
    def test_media_type_to_app_mapping(self):
        self.assertEqual(APP_FOR_MEDIA_TYPE, {"movie": "radarr", "episode": "sonarr", "track": "lidarr"})

    def test_media_type_to_field_mapping(self):
        self.assertEqual(ID_FIELD_FOR_MEDIA_TYPE,
                         {"movie": "radarr_id", "episode": "sonarr_id", "track": "lidarr_id"})

    def test_unsupported_media_type_not_mapped(self):
        self.assertNotIn("show", APP_FOR_MEDIA_TYPE)
        self.assertNotIn("artist", APP_FOR_MEDIA_TYPE)
        self.assertNotIn("album", APP_FOR_MEDIA_TYPE)


class SearchArrCandidatesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    async def asyncTearDown(self):
        self.db.close()

    async def test_unsupported_media_type_returns_empty(self):
        out = await search_arr_candidates(self.db, "show", "")
        self.assertEqual(out, [])

    async def test_no_integration_row_returns_empty(self):
        out = await search_arr_candidates(self.db, "movie", "")
        self.assertEqual(out, [])

    async def test_disabled_integration_returns_empty(self):
        self.db.add(Integration(name="radarr", url="http://r", api_key="k", enabled=False))
        self.db.commit()
        out = await search_arr_candidates(self.db, "movie", "")
        self.assertEqual(out, [])

    async def test_radarr_candidates_returned_and_sorted(self):
        self.db.add(Integration(name="radarr", url="http://r", api_key="k", enabled=True))
        self.db.commit()
        fake = AsyncMock()
        fake.get_movies = AsyncMock(return_value=[
            {"id": 2, "title": "Zeta", "year": 2020},
            {"id": 1, "title": "Alpha", "year": 2019},
        ])
        with patch("app.integrations.radarr.RadarrIntegration", return_value=fake):
            out = await search_arr_candidates(self.db, "movie", "")
        self.assertEqual([c["title"] for c in out], ["Alpha", "Zeta"])

    async def test_radarr_search_filters_by_title_substring(self):
        self.db.add(Integration(name="radarr", url="http://r", api_key="k", enabled=True))
        self.db.commit()
        fake = AsyncMock()
        fake.get_movies = AsyncMock(return_value=[
            {"id": 1, "title": "Blade Runner", "year": 1982},
            {"id": 2, "title": "The Matrix", "year": 1999},
        ])
        with patch("app.integrations.radarr.RadarrIntegration", return_value=fake):
            out = await search_arr_candidates(self.db, "movie", "blade")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Blade Runner")

    async def test_sonarr_candidates_use_series_title(self):
        self.db.add(Integration(name="sonarr", url="http://s", api_key="k", enabled=True))
        self.db.commit()
        fake = AsyncMock()
        fake.get_series = AsyncMock(return_value=[{"id": 5, "title": "Jackass", "year": 2000}])
        with patch("app.integrations.sonarr.SonarrIntegration", return_value=fake):
            out = await search_arr_candidates(self.db, "episode", "")
        self.assertEqual(out, [{"id": 5, "title": "Jackass", "year": 2000}])

    async def test_lidarr_candidates_use_artist_name_no_year(self):
        self.db.add(Integration(name="lidarr", url="http://l", api_key="k", enabled=True))
        self.db.commit()
        fake = AsyncMock()
        fake.get_artists = AsyncMock(return_value=[{"id": 9, "artistName": "Godsmack"}])
        with patch("app.integrations.lidarr.LidarrIntegration", return_value=fake):
            out = await search_arr_candidates(self.db, "track", "")
        self.assertEqual(out, [{"id": 9, "title": "Godsmack", "year": None}])

    async def test_limit_applied(self):
        self.db.add(Integration(name="radarr", url="http://r", api_key="k", enabled=True))
        self.db.commit()
        fake = AsyncMock()
        fake.get_movies = AsyncMock(return_value=[{"id": i, "title": f"M{i}", "year": 2000} for i in range(10)])
        with patch("app.integrations.radarr.RadarrIntegration", return_value=fake):
            out = await search_arr_candidates(self.db, "movie", "", limit=3)
        self.assertEqual(len(out), 3)

    async def test_client_exception_fails_soft_to_empty(self):
        self.db.add(Integration(name="radarr", url="http://r", api_key="k", enabled=True))
        self.db.commit()
        fake = AsyncMock()
        fake.get_movies = AsyncMock(side_effect=RuntimeError("connection refused"))
        with patch("app.integrations.radarr.RadarrIntegration", return_value=fake):
            out = await search_arr_candidates(self.db, "movie", "")
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
