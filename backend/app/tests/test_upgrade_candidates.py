import asyncio
import unittest
from unittest.mock import patch

from app.models.integration import Integration
from app.services import upgrade_candidates as uc
from app.services.secret_box import encrypt
from app.tests.test_lib07_to_11 import _ApiCase


class ParseTests(unittest.TestCase):
    def test_movies(self):
        out = uc.parse_movies([
            {"id": 1, "title": "A", "year": 2001, "monitored": True,
             "movieFile": {"size": 5, "quality": {"quality": {"name": "HDTV-720p"}}}},
            {"id": 2, "title": "B", "movieFile": None},
        ])
        self.assertEqual(out[0]["quality"], "HDTV-720p")
        self.assertEqual(out[0]["size"], 5)
        self.assertIsNone(out[1]["quality"])
        self.assertEqual(out[1]["size"], 0)

    def test_episodes_collapse_by_series(self):
        eps = [
            {"seriesId": 7, "series": {"id": 7, "title": "Show", "year": 2010},
             "episodeFile": {"size": 10, "quality": {"quality": {"name": "SDTV"}}}},
            {"seriesId": 7, "series": {"id": 7, "title": "Show"},
             "episodeFile": {"size": 20, "quality": {"quality": {"name": "WEBDL-480p"}}}},
            {"seriesId": 8, "series": {"id": 8, "title": "Other"}, "episodeFile": None},
            {"series": {}},  # no id: skipped
        ]
        out = {o["title"]: o for o in uc.parse_episodes(eps)}
        self.assertEqual(out["Show"]["episodes"], 2)
        self.assertEqual(out["Show"]["size"], 30)
        self.assertEqual(out["Show"]["quality"], "SDTV, WEBDL-480p")
        self.assertEqual(out["Other"]["episodes"], 1)
        self.assertEqual(len(out), 2)


class EndpointTests(_ApiCase):
    def test_unconfigured_apps(self):
        out = asyncio.run(uc.build(self.db))
        self.assertFalse(out["radarr"]["configured"])
        self.assertEqual(out["sonarr"]["items"], [])

    def test_failure_is_contained_per_app(self):
        self.db.add(Integration(name="radarr", url="http://r", api_key=encrypt("k"), enabled=True))
        self.db.add(Integration(name="sonarr", url="http://s", api_key=encrypt("k"), enabled=True))
        self.db.commit()

        async def fake_paged(client, base, headers, params):
            if "includeSeries" in params:
                raise RuntimeError("sonarr down")
            return ([{"id": 1, "title": "Small", "movieFile": {"size": 1}},
                     {"id": 2, "title": "Big", "movieFile": {"size": 9}}], 2)

        with patch.object(uc, "_paged", fake_paged):
            out = asyncio.run(uc.build(self.db))
        self.assertEqual([i["title"] for i in out["radarr"]["items"]], ["Big", "Small"])
        self.assertEqual(out["radarr"]["total"], 2)
        self.assertEqual(out["sonarr"]["error"], "sonarr down")
        self.assertTrue(out["sonarr"]["configured"])
