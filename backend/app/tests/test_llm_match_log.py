"""LLM-LOG-01 match-review call log: record/backfill/prune against an in-memory
SQLite session, plus SP-04 playlist-name reply parsing (mocked _generate — no
real network)."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.failed_import import FailedImport
from app.models.llm_match_log import LlmMatchLog
from app.services import llm_assist, llm_match_log


class LlmMatchLogTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def _capture(self, **kw):
        base = {"replied": True, "prompt_hash": "abcd1234", "scaffold_version": "test.v1",
                "latency_ms": 1200, "raw": '{"agrees": true}', "parse_ok": True}
        base.update(kw)
        return base

    def test_record_writes_row(self):
        llm_match_log.record(
            self.db, failed_import_id=7, site="scan", source_app="lidarr",
            model="qwen2.5:7b", release_title="Artist-Album-2020-GRP",
            candidate_title="Artist - Album", context="ctx", det_summary="det",
            capture=self._capture(), agrees=True, confidence_adjustment=0.1,
            enforced=False, checks=(True, False))
        self.db.commit()
        row = self.db.query(LlmMatchLog).one()
        self.assertEqual(row.failed_import_id, 7)
        self.assertEqual(row.prompt_hash, "abcd1234")
        self.assertTrue(row.evidence_artist_ok)
        self.assertFalse(row.evidence_album_ok)
        self.assertIsNone(row.resolution)

    def test_maintain_backfills_terminal_resolution(self):
        fi = FailedImport(source_app="lidarr", raw_title="x", status="accepted",
                          resolved_at=datetime(2026, 7, 10))
        self.db.add(fi)
        self.db.commit()
        llm_match_log.record(
            self.db, failed_import_id=fi.id, site="rescore", source_app="lidarr",
            model="m", release_title="r", candidate_title="c", context="", det_summary="",
            capture=self._capture(), agrees=True, confidence_adjustment=0.0,
            enforced=False, checks=None)
        self.db.commit()
        result = llm_match_log.maintain(self.db)
        self.assertEqual(result["backfilled"], 1)
        row = self.db.query(LlmMatchLog).one()
        self.assertEqual(row.resolution, "accepted")
        self.assertEqual(row.resolved_at, datetime(2026, 7, 10))

    def test_maintain_leaves_open_rows_unresolved(self):
        fi = FailedImport(source_app="sonarr", raw_title="x", status="suggested")
        self.db.add(fi)
        self.db.commit()
        llm_match_log.record(
            self.db, failed_import_id=fi.id, site="scan", source_app="sonarr",
            model="m", release_title="r", candidate_title="c", context="", det_summary="",
            capture=self._capture(), agrees=False, confidence_adjustment=-0.2,
            enforced=False, checks=None)
        self.db.commit()
        result = llm_match_log.maintain(self.db)
        self.assertEqual(result["backfilled"], 0)
        self.assertIsNone(self.db.query(LlmMatchLog).one().resolution)

    def test_maintain_prunes_by_age_and_cap(self):
        old = LlmMatchLog(created_at=datetime.utcnow() - timedelta(days=120),
                          site="scan", raw_reply="old")
        fresh = LlmMatchLog(created_at=datetime.utcnow(), site="scan", raw_reply="fresh")
        self.db.add_all([old, fresh])
        self.db.commit()
        result = llm_match_log.maintain(self.db)
        self.assertEqual(result["pruned"], 1)
        self.assertEqual(self.db.query(LlmMatchLog).one().raw_reply, "fresh")

    def test_maintain_enforces_row_cap(self):
        with patch.object(llm_match_log, "MAX_ROWS", 3):
            for i in range(5):
                self.db.add(LlmMatchLog(
                    created_at=datetime.utcnow() - timedelta(minutes=5 - i),
                    site="scan", raw_reply=f"r{i}"))
            self.db.commit()
            result = llm_match_log.maintain(self.db)
        self.assertEqual(result["pruned"], 2)
        kept = [r.raw_reply for r in self.db.query(LlmMatchLog)
                .order_by(LlmMatchLog.created_at).all()]
        self.assertEqual(kept, ["r2", "r3", "r4"])  # oldest two dropped


class ReviewMatchCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_filled_on_reply(self):
        capture: dict = {}
        with patch.object(llm_assist, "_generate",
                          new=AsyncMock(return_value='{"agrees": true, "confidence_adjustment": 0.1, "reason": "ok"}')):
            result = await llm_assist.review_match(
                "host", "model", "release", "candidate", det_summary="det",
                capture=capture)
        self.assertIsNotNone(result)
        self.assertTrue(capture["replied"])
        self.assertTrue(capture["parse_ok"])
        self.assertEqual(capture["scaffold_version"], llm_assist.SCAFFOLD_VERSION)
        self.assertEqual(len(capture["prompt_hash"]), 16)
        self.assertIn("agrees", capture["raw"])

    async def test_capture_untouched_when_no_reply(self):
        capture: dict = {}
        with patch.object(llm_assist, "_generate", new=AsyncMock(return_value=None)):
            result = await llm_assist.review_match(
                "host", "model", "release", "candidate", det_summary="det",
                capture=capture)
        self.assertIsNone(result)
        self.assertNotIn("replied", capture)

    async def test_capture_parse_failure_recorded(self):
        capture: dict = {}
        with patch.object(llm_assist, "_generate",
                          new=AsyncMock(return_value="total nonsense, no verdict")):
            result = await llm_assist.review_match(
                "host", "model", "release", "candidate", det_summary="det",
                capture=capture)
        self.assertIsNone(result)
        self.assertTrue(capture["replied"])
        self.assertFalse(capture["parse_ok"])


class SuggestPlaylistNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_clean_name(self):
        with patch.object(llm_assist, "_generate",
                          new=AsyncMock(return_value='"Midnight Circuit"\n')):
            name = await llm_assist.suggest_playlist_name("h", "m", "synthwave", ["A"])
        self.assertEqual(name, "Midnight Circuit")

    async def test_rejects_prose_reply(self):
        prose = ("Sure! Here are some great ideas for your playlist that I think "
                 "you will really enjoy listening to on repeat")
        with patch.object(llm_assist, "_generate", new=AsyncMock(return_value=prose)):
            self.assertIsNone(await llm_assist.suggest_playlist_name("h", "m", "rock"))

    async def test_none_reply_fails_soft(self):
        with patch.object(llm_assist, "_generate", new=AsyncMock(return_value=None)):
            self.assertIsNone(await llm_assist.suggest_playlist_name("h", "m", "rock"))

    async def test_strips_think_block(self):
        raw = "<think>naming things is hard</think>Velvet Static"
        with patch.object(llm_assist, "_generate", new=AsyncMock(return_value=raw)):
            name = await llm_assist.suggest_playlist_name("h", "m", "shoegaze")
        self.assertEqual(name, "Velvet Static")


if __name__ == "__main__":
    unittest.main()
