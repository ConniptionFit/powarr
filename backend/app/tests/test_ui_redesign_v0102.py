"""v0.102.0 — one Auto-add control and one Ollama endpoint."""
import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.app_setting import AppSetting
from app.services.artist_discovery import load_settings


def _put(db, key, value):
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row:
        row = AppSetting(key=key)
        db.add(row)
    row.value = json.dumps(value)
    db.commit()


class LegacyAutoAddFoldTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_legacy_thresholds_fold_into_switch_and_zero_out(self):
        _put(self.db, "artist_discovery", {"enabled": True, "auto_add_enabled": False,
                                           "suggest_connection_threshold": 2,
                                           "auto_add_connection_threshold": 4, "auto_add_all_time_threshold": 6})
        cfg = load_settings(self.db)
        self.assertTrue(cfg.auto_add_enabled)
        self.assertEqual(cfg.auto_add_min_connections, 4)
        self.assertEqual(cfg.auto_add_connection_threshold, 0)
        self.assertEqual(cfg.auto_add_all_time_threshold, 0)
        self.assertTrue(cfg.legacy_auto_add_migrated)

    def test_migration_runs_once(self):
        _put(self.db, "artist_discovery", {"enabled": True, "suggest_connection_threshold": 2,
                                           "auto_add_connection_threshold": 4})
        load_settings(self.db)
        # User turns auto-add off afterwards; reload must not turn it back on.
        raw = json.loads(self.db.query(AppSetting).filter_by(key="artist_discovery").first().value)
        raw["auto_add_enabled"] = False
        _put(self.db, "artist_discovery", raw)
        self.assertFalse(load_settings(self.db).auto_add_enabled)

    def test_no_legacy_thresholds_leaves_switch_alone(self):
        _put(self.db, "artist_discovery", {"enabled": True, "suggest_connection_threshold": 2,
                                           "auto_add_enabled": False})
        self.assertFalse(load_settings(self.db).auto_add_enabled)


class SharedOllamaTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_shared_host_and_model_win(self):
        _put(self.db, "ollama", {"host": "10.1.1.6:11434", "embed_model": "nomic-embed-text"})
        _put(self.db, "artist_discovery", {"suggest_connection_threshold": 2, "embeddings_enabled": True,
                                           "ollama_host": "old:11434", "embed_model": "all-minilm",
                                           "legacy_auto_add_migrated": True, "auto_add_enabled": False})
        cfg = load_settings(self.db)
        self.assertEqual(cfg.ollama_host, "10.1.1.6:11434")
        self.assertEqual(cfg.embed_model, "nomic-embed-text")

    def test_legacy_music_host_is_copied_to_shared_record(self):
        _put(self.db, "artist_discovery", {"suggest_connection_threshold": 2, "embeddings_enabled": True,
                                           "ollama_host": "10.1.1.6:11434", "embed_model": "all-minilm",
                                           "legacy_auto_add_migrated": True, "auto_add_enabled": False})
        cfg = load_settings(self.db)
        self.assertEqual(cfg.ollama_host, "10.1.1.6:11434")
        shared = json.loads(self.db.query(AppSetting).filter_by(key="ollama").first().value)
        self.assertEqual(shared["host"], "10.1.1.6:11434")
        self.assertEqual(shared["embed_model"], "all-minilm")


if __name__ == "__main__":
    unittest.main()
