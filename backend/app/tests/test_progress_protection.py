"""Unit tests for LIB-04: watch-progress protect (in-progress items hidden
from deletion suggestions)."""
import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.app_setting import AppSetting
from app.models.integration import Integration
from app.models.media import MediaItem
from app.services.plex_sync import refresh_progress_protection


def _item(rating_key, **over):
    base = dict(plex_rating_key=rating_key, title=rating_key, media_type="episode")
    base.update(over)
    return MediaItem(**base)


class _FakeTautulli:
    def __init__(self, history):
        self._history = history

    async def get_recent_history(self, days=30, length=5000):
        return self._history


class RefreshProgressProtectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def _set_cleanup(self, **kwargs):
        cfg = {"protect_in_progress": True}
        cfg.update(kwargs)
        self.db.add(AppSetting(key="cleanup", value=json.dumps(cfg)))
        self.db.commit()

    def _add_tautulli(self):
        self.db.add(Integration(name="tautulli", url="http://taut", api_key="k", enabled=True))
        self.db.commit()

    async def test_noop_when_setting_off(self):
        self.db.add(AppSetting(key="cleanup", value=json.dumps({"protect_in_progress": False})))
        self.db.commit()
        self._add_tautulli()
        count = await refresh_progress_protection(self.db)
        self.assertEqual(count, 0)

    async def test_noop_when_tautulli_not_configured(self):
        self._set_cleanup()
        count = await refresh_progress_protection(self.db)
        self.assertEqual(count, 0)

    async def test_protects_item_in_progress_band(self):
        self._set_cleanup()
        self._add_tautulli()
        self.db.add(_item("ep1"))
        self.db.add(_item("ep2"))  # not in history — untouched
        self.db.commit()

        history = [{"rating_key": "ep1", "percent_complete": 45.0}]
        with patch("app.integrations.tautulli.TautulliIntegration",
                   return_value=_FakeTautulli(history)):
            count = await refresh_progress_protection(self.db)

        self.assertEqual(count, 1)
        ep1 = self.db.query(MediaItem).filter_by(plex_rating_key="ep1").one()
        ep2 = self.db.query(MediaItem).filter_by(plex_rating_key="ep2").one()
        self.assertTrue(ep1.progress_protected)
        self.assertFalse(bool(ep2.progress_protected))

    async def test_below_min_percent_not_protected(self):
        self._set_cleanup(in_progress_min_percent=5.0)
        self._add_tautulli()
        self.db.add(_item("ep1"))
        self.db.commit()

        history = [{"rating_key": "ep1", "percent_complete": 1.0}]
        with patch("app.integrations.tautulli.TautulliIntegration",
                   return_value=_FakeTautulli(history)):
            count = await refresh_progress_protection(self.db)

        self.assertEqual(count, 0)

    async def test_at_or_above_max_percent_not_protected(self):
        self._set_cleanup(in_progress_max_percent=90.0)
        self._add_tautulli()
        self.db.add(_item("ep1"))
        self.db.commit()

        history = [{"rating_key": "ep1", "percent_complete": 95.0}]
        with patch("app.integrations.tautulli.TautulliIntegration",
                   return_value=_FakeTautulli(history)):
            count = await refresh_progress_protection(self.db)

        self.assertEqual(count, 0)

    async def test_takes_highest_percent_across_multiple_sessions(self):
        self._set_cleanup(in_progress_max_percent=90.0)
        self._add_tautulli()
        self.db.add(_item("ep1"))
        self.db.commit()

        # A rewatch that finished should not be masked by an earlier partial session.
        history = [
            {"rating_key": "ep1", "percent_complete": 40.0},
            {"rating_key": "ep1", "percent_complete": 96.0},
        ]
        with patch("app.integrations.tautulli.TautulliIntegration",
                   return_value=_FakeTautulli(history)):
            count = await refresh_progress_protection(self.db)

        self.assertEqual(count, 0)  # 96% is the best signal — essentially finished

    async def test_resets_stale_protection_no_longer_in_progress(self):
        self._set_cleanup()
        self._add_tautulli()
        self.db.add(_item("ep1", progress_protected=True))
        self.db.commit()

        with patch("app.integrations.tautulli.TautulliIntegration",
                   return_value=_FakeTautulli([])):
            count = await refresh_progress_protection(self.db)

        self.assertEqual(count, 0)
        ep1 = self.db.query(MediaItem).filter_by(plex_rating_key="ep1").one()
        self.assertFalse(bool(ep1.progress_protected))

    async def test_does_not_cascade_to_sibling_episodes(self):
        """Only the specific in-progress item is protected — siblings of the
        same show are untouched, per the LIB-04 spec constraint."""
        self._set_cleanup()
        self._add_tautulli()
        self.db.add(_item("ep1", parent_title="Show"))
        self.db.add(_item("ep2", parent_title="Show"))
        self.db.commit()

        history = [{"rating_key": "ep1", "percent_complete": 50.0}]
        with patch("app.integrations.tautulli.TautulliIntegration",
                   return_value=_FakeTautulli(history)):
            await refresh_progress_protection(self.db)

        ep2 = self.db.query(MediaItem).filter_by(plex_rating_key="ep2").one()
        self.assertFalse(bool(ep2.progress_protected))


if __name__ == "__main__":
    unittest.main()
