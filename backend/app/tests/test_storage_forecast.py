import unittest
from datetime import datetime, timedelta

from app.models.deletion_log import DeletionLog
from app.models.media import MediaItem
from app.services.storage_forecast import build_forecast, project
from app.tests.test_lib07_to_11 import _ApiCase

GB = 1024 ** 3


def _item(db, key, size, days_ago, media_type="movie", pending=False):
    db.add(MediaItem(plex_rating_key=key, title=key, media_type=media_type,
                     file_size=size, added_at=datetime.utcnow() - timedelta(days=days_ago),
                     pending_delete_at=datetime.utcnow() if pending else None))


class ProjectionTests(unittest.TestCase):
    def test_project_without_capacity(self):
        assert project(10, 1.0, None)["status"] == "no_capacity"


    def test_project_growing_gives_fill_date(self):
        r = project(50 * GB, GB, 100 * GB)
        assert r["status"] == "growing"
        assert r["days_until_full"] == 50
        assert r["used_pct"] == 50.0


    def test_project_shrinking_flat_full_distant(self):
        assert project(10, -5.0, 100)["status"] == "shrinking"
        assert project(10, 0.0, 100)["status"] == "flat"
        assert project(100, 1.0, 100)["status"] == "full"
        assert project(1, 1e-9, 10 ** 12)["status"] == "distant"




class ForecastApiTests(_ApiCase):
    def test_build_forecast_nets_adds_against_deletions(self):
        db = self.db
        _item(db, "a", 30 * GB, 5)
        _item(db, "b", 10 * GB, 400)            # outside the window
        _item(db, "show", 99 * GB, 5, media_type="show")  # container, ignored
        _item(db, "gone", 50 * GB, 5, pending=True)       # soft-deleted, ignored
        db.add(DeletionLog(title="x", media_type="movie", file_size=10 * GB,
                           deleted_at=datetime.utcnow() - timedelta(days=2)))
        db.commit()
        f = build_forecast(db, days=30, capacity_bytes=200 * GB)
        assert f["current_bytes"] == 40 * GB
        assert f["added_bytes"] == 30 * GB
        assert f["removed_bytes"] == 10 * GB
        assert f["net_bytes_per_day"] == int(20 * GB / 30)
        assert len(f["points"]) == 30
        assert f["status"] == "growing"


    def test_endpoint(self):
        r = self.client.get("/api/v1/media/storage-forecast", params={"days": 30})
        assert r.status_code == 200
        assert r.json()["status"] == "no_capacity"
        assert self.client.get("/api/v1/media/storage-forecast", params={"days": 3}).status_code == 422
