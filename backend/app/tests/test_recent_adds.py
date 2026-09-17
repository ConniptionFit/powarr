"""AD-22 — Recently Added list (services/artist_discovery.py::list_recent_adds).

- newest-first, limit respected, empty log -> empty list
- discovery adds pull their DiscoveredArtist enrichment (why-suggested fields,
  image, bio); mbid match preferred, artist-name fallback for mbid-less rows
- related adds have no candidate row: enrichment fields empty, source kept
- AD-21 thumbnail cache backfills the image when the candidate's enrichment
  image is gone (AD-08 purge) or never existed
"""
import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ArtistThumbnail
from app.models.artist_add_log import ArtistAddLog
from app.models.artist_discovery import DiscoveredArtist
from app.services.artist_discovery import _norm_artist, list_recent_adds


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _log(db, name, source="discovery", mbid=None, added_at=None, lidarr_id=1):
    row = ArtistAddLog(artist_name=name, musicbrainz_id=mbid, source=source,
                       lidarr_artist_id=lidarr_id,
                       added_at=added_at or datetime.utcnow())
    db.add(row)
    db.commit()
    return row


def _cand(db, name, mbid=None, source="centroid", score=None, seeds=None,
          image="https://img.example/a.jpg", bio="A bio."):
    row = DiscoveredArtist(
        artist_name=name, musicbrainz_id=mbid, source=source,
        similarity_score=score, status="accepted",
        seed_artist_names=json.dumps(seeds) if seeds else None,
        associated_seed_mbids=json.dumps(["m1", "m2"]) if seeds else None,
        genres=json.dumps(["rock"]), image_url=image, bio=bio,
    )
    db.add(row)
    db.commit()
    return row


class TestListRecentAdds(unittest.TestCase):
    def test_empty_log(self):
        db = _db()
        self.assertEqual(list_recent_adds(db), [])

    def test_newest_first_and_limit(self):
        db = _db()
        base = datetime(2026, 7, 18, 12, 0)
        for i in range(5):
            _log(db, f"Artist {i}", added_at=base + timedelta(hours=i))
        out = list_recent_adds(db, limit=3)
        self.assertEqual([r["artist_name"] for r in out],
                         ["Artist 4", "Artist 3", "Artist 2"])

    def test_discovery_add_enriched_via_mbid(self):
        db = _db()
        _cand(db, "Aesop Rock", mbid="mb-1", source="centroid", score=0.87)
        _log(db, "Aesop Rock", mbid="mb-1")
        out = list_recent_adds(db)
        self.assertEqual(out[0]["discovery_source"], "centroid")
        self.assertAlmostEqual(out[0]["similarity_score"], 0.87)
        self.assertEqual(out[0]["image_url"], "https://img.example/a.jpg")
        self.assertEqual(out[0]["bio"], "A bio.")
        self.assertEqual(out[0]["genres"], ["rock"])

    def test_discovery_add_enriched_via_name_fallback(self):
        db = _db()
        _cand(db, "Busdriver", mbid=None, source="graph", seeds=["Aesop Rock", "MF DOOM"])
        _log(db, "Busdriver", mbid=None)
        out = list_recent_adds(db)
        self.assertEqual(out[0]["discovery_source"], "graph")
        self.assertEqual(out[0]["seed_artist_names"], ["Aesop Rock", "MF DOOM"])

    def test_related_add_has_no_enrichment(self):
        db = _db()
        _log(db, "Some Band", source="related", mbid="mb-9")
        out = list_recent_adds(db)
        self.assertEqual(out[0]["source"], "related")
        self.assertIsNone(out[0]["discovery_source"])
        self.assertEqual(out[0]["seed_artist_names"], [])
        self.assertIsNone(out[0]["bio"])

    def test_thumbnail_cache_backfills_missing_image(self):
        db = _db()
        _cand(db, "Purged Artist", mbid="mb-2", image=None, bio=None)
        _log(db, "Purged Artist", mbid="mb-2")
        db.add(ArtistThumbnail(name_key=_norm_artist("Purged Artist"),
                               artist_name="Purged Artist",
                               image_url="https://thumb.example/p.jpg"))
        db.commit()
        out = list_recent_adds(db)
        self.assertEqual(out[0]["image_url"], "https://thumb.example/p.jpg")

    def test_candidate_image_wins_over_thumbnail(self):
        db = _db()
        _cand(db, "Both Sources", mbid="mb-3")
        _log(db, "Both Sources", mbid="mb-3")
        db.add(ArtistThumbnail(name_key=_norm_artist("Both Sources"),
                               artist_name="Both Sources",
                               image_url="https://thumb.example/t.jpg"))
        db.commit()
        out = list_recent_adds(db)
        self.assertEqual(out[0]["image_url"], "https://img.example/a.jpg")


if __name__ == "__main__":
    unittest.main()
