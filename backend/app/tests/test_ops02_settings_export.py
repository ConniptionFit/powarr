"""Unit tests for OPS-02: config-as-code settings export/import."""
import json
import tempfile
import time
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings as app_settings
from app.database import Base
from app.models.app_setting import AppSetting
from app.models.integration import Integration
from app.services import settings_export


class ExportImportRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def test_export_includes_every_app_setting(self):
        self.db.add_all([
            AppSetting(key="ollama", value=json.dumps({"host": "10.1.1.4"})),
            AppSetting(key="cleanup", value=json.dumps({"soft_delete_days": 3})),
        ])
        self.db.commit()
        data = settings_export.export_settings(self.db)
        self.assertEqual(data["app_settings"]["ollama"], {"host": "10.1.1.4"})
        self.assertEqual(data["app_settings"]["cleanup"], {"soft_delete_days": 3})

    def test_export_handles_null_value_gracefully(self):
        self.db.add(AppSetting(key="empty", value=None))
        self.db.commit()
        data = settings_export.export_settings(self.db)
        self.assertIsNone(data["app_settings"]["empty"])

    def test_export_never_includes_integration_secrets(self):
        self.db.add(Integration(name="sonarr", url="http://sonarr:8989", api_key="SECRET-KEY",
                                username="admin", password="hunter2", enabled=True,
                                extra_config=json.dumps({"token": "also-secret"})))
        self.db.commit()
        data = settings_export.export_settings(self.db)
        dumped = json.dumps(data)
        self.assertNotIn("SECRET-KEY", dumped)
        self.assertNotIn("hunter2", dumped)
        self.assertNotIn("also-secret", dumped)
        self.assertEqual(data["integrations"], [
            {"name": "sonarr", "url": "http://sonarr:8989", "enabled": True},
        ])

    def test_export_powarr_version_is_informational_passthrough(self):
        data = settings_export.export_settings(self.db, "9.9.9")
        self.assertEqual(data["powarr_version"], "9.9.9")

    def test_import_creates_new_app_setting_rows(self):
        result = settings_export.import_settings(self.db, {"app_settings": {"cleanup": {"a": 1}}})
        self.assertEqual(result["app_settings_imported"], 1)
        row = self.db.query(AppSetting).filter_by(key="cleanup").first()
        self.assertEqual(json.loads(row.value), {"a": 1})

    def test_import_overwrites_existing_app_setting_row(self):
        self.db.add(AppSetting(key="cleanup", value=json.dumps({"a": 1})))
        self.db.commit()
        settings_export.import_settings(self.db, {"app_settings": {"cleanup": {"a": 2}}})
        row = self.db.query(AppSetting).filter_by(key="cleanup").first()
        self.assertEqual(json.loads(row.value), {"a": 2})

    def test_import_creates_new_integration_row(self):
        result = settings_export.import_settings(
            self.db, {"integrations": [{"name": "radarr", "url": "http://r", "enabled": True}]})
        self.assertEqual(result["integrations_updated"], 1)
        row = self.db.query(Integration).filter_by(name="radarr").first()
        self.assertEqual(row.url, "http://r")
        self.assertTrue(row.enabled)

    def test_import_never_touches_existing_integration_secrets(self):
        self.db.add(Integration(name="radarr", url="http://old", api_key="KEEP-ME",
                                username="user", password="pass", enabled=False,
                                extra_config="keep-this-too"))
        self.db.commit()
        settings_export.import_settings(
            self.db, {"integrations": [{"name": "radarr", "url": "http://new", "enabled": True}]})
        row = self.db.query(Integration).filter_by(name="radarr").first()
        self.assertEqual(row.url, "http://new")
        self.assertTrue(row.enabled)
        # Secrets and extra_config untouched — the import payload never had them.
        self.assertEqual(row.api_key, "KEEP-ME")
        self.assertEqual(row.username, "user")
        self.assertEqual(row.password, "pass")
        self.assertEqual(row.extra_config, "keep-this-too")

    def test_import_skips_integration_entry_missing_name(self):
        result = settings_export.import_settings(self.db, {"integrations": [{"url": "http://x"}]})
        self.assertEqual(result["integrations_updated"], 0)

    def test_import_rejects_non_dict_app_settings(self):
        with self.assertRaises(ValueError):
            settings_export.import_settings(self.db, {"app_settings": "not-a-dict"})

    def test_import_rejects_non_list_integrations(self):
        with self.assertRaises(ValueError):
            settings_export.import_settings(self.db, {"integrations": "not-a-list"})

    def test_import_missing_keys_defaults_to_no_op(self):
        result = settings_export.import_settings(self.db, {})
        self.assertEqual(result, {"app_settings_imported": 0, "integrations_updated": 0})

    # Regression tests for a real secret-leak bug found via live verification
    # against production data: QdrantSettings (AppSetting key "qdrant") stores
    # a raw api_key field directly in its own JSON blob — a plain "dump every
    # row" export put a real Qdrant API key in plaintext in the downloaded
    # file before _redact()/_unredact_merge() were added.

    def test_export_redacts_secret_shaped_fields_in_app_settings(self):
        self.db.add(AppSetting(key="qdrant", value=json.dumps(
            {"url": "http://q:6333", "api_key": "real-secret-value", "collection": "x"})))
        self.db.commit()
        data = settings_export.export_settings(self.db)
        self.assertEqual(data["app_settings"]["qdrant"]["api_key"], "***REDACTED***")
        # Non-secret fields in the same blob are untouched.
        self.assertEqual(data["app_settings"]["qdrant"]["url"], "http://q:6333")
        self.assertEqual(data["app_settings"]["qdrant"]["collection"], "x")
        self.assertNotIn("real-secret-value", json.dumps(data))

    def test_export_does_not_redact_falsy_secret_field(self):
        self.db.add(AppSetting(key="qdrant", value=json.dumps(
            {"url": "", "api_key": "", "collection": "x"})))
        self.db.commit()
        data = settings_export.export_settings(self.db)
        # An unset ("") secret field stays "" rather than becoming the
        # sentinel string — nothing real to hide, and it keeps a fresh export
        # of an unconfigured instance looking genuinely empty.
        self.assertEqual(data["app_settings"]["qdrant"]["api_key"], "")

    def test_import_restores_existing_live_secret_instead_of_the_sentinel(self):
        # This instance already has a real key configured...
        self.db.add(AppSetting(key="qdrant", value=json.dumps(
            {"url": "http://old", "api_key": "still-the-real-key", "collection": "x"})))
        self.db.commit()
        # ...importing a redacted export (e.g. re-importing its own export, or
        # one from another instance) must not clobber that real key with the
        # literal placeholder string.
        settings_export.import_settings(self.db, {"app_settings": {
            "qdrant": {"url": "http://new", "api_key": "***REDACTED***", "collection": "x"},
        }})
        row = self.db.query(AppSetting).filter_by(key="qdrant").first()
        value = json.loads(row.value)
        self.assertEqual(value["url"], "http://new")  # non-secret field did update
        self.assertEqual(value["api_key"], "still-the-real-key")  # secret preserved

    def test_import_drops_redacted_field_when_instance_has_no_prior_value(self):
        # Fresh instance, key doesn't exist yet — nothing to restore from, so
        # the sentinel is dropped rather than written verbatim as "the key".
        settings_export.import_settings(self.db, {"app_settings": {
            "qdrant": {"url": "http://new", "api_key": "***REDACTED***", "collection": "x"},
        }})
        row = self.db.query(AppSetting).filter_by(key="qdrant").first()
        value = json.loads(row.value)
        self.assertEqual(value["url"], "http://new")
        self.assertNotIn("api_key", value)

    def test_round_trip_export_then_import_preserves_data(self):
        self.db.add_all([
            AppSetting(key="ollama", value=json.dumps({"model": "qwen2.5:7b"})),
            Integration(name="lidarr", url="http://l", api_key="secret", enabled=True),
        ])
        self.db.commit()
        exported = settings_export.export_settings(self.db)

        engine2 = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine2)
        db2 = sessionmaker(bind=engine2)()
        try:
            settings_export.import_settings(db2, exported)
            row = db2.query(AppSetting).filter_by(key="ollama").first()
            self.assertEqual(json.loads(row.value), {"model": "qwen2.5:7b"})
            integ = db2.query(Integration).filter_by(name="lidarr").first()
            self.assertEqual(integ.url, "http://l")
            self.assertTrue(integ.enabled)
            self.assertIsNone(integ.api_key)  # never carried over — must be re-entered
        finally:
            db2.close()


class SettingsExportFileManagementTests(unittest.TestCase):
    """Mirrors test_backup.py's TestBackupFileManagement for the export-file
    side (list/prune) — same isolation pattern (temp data_dir)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_data_dir = app_settings.data_dir
        app_settings.data_dir = self._tmp.name

    def tearDown(self):
        app_settings.data_dir = self._orig_data_dir
        self._tmp.cleanup()

    def _touch(self, name: str):
        d = settings_export.export_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / name
        path.write_text("{}")
        return path

    def test_list_exports_empty_when_no_dir(self):
        self.assertEqual(settings_export.list_settings_exports(), [])

    def test_list_exports_newest_first(self):
        p1 = self._touch("powarr-settings-20260101T000000Z.json")
        time.sleep(0.01)
        p2 = self._touch("powarr-settings-20260102T000000Z.json")
        names = [f["name"] for f in settings_export.list_settings_exports()]
        self.assertEqual(names, [p2.name, p1.name])

    def test_prune_keeps_only_retention_count(self):
        for i in range(5):
            self._touch(f"powarr-settings-{i}.json")
            time.sleep(0.01)
        deleted = settings_export.prune_settings_exports(2)
        self.assertEqual(deleted, 3)
        self.assertEqual(len(settings_export.list_settings_exports()), 2)

    def test_prune_zero_retention_means_unlimited(self):
        for i in range(3):
            self._touch(f"powarr-settings-{i}.json")
        self.assertEqual(settings_export.prune_settings_exports(0), 0)
        self.assertEqual(len(settings_export.list_settings_exports()), 3)

    def test_safe_export_path_rejects_traversal(self):
        with self.assertRaises(ValueError):
            settings_export.safe_export_path("../../etc/passwd")

    def test_safe_export_path_rejects_wrong_prefix(self):
        with self.assertRaises(ValueError):
            settings_export.safe_export_path("powarr-backup-2026.sql")

    def test_safe_export_path_accepts_valid_name(self):
        p = settings_export.safe_export_path("powarr-settings-20260101T000000Z.json")
        self.assertTrue(str(p).endswith("powarr-settings-20260101T000000Z.json"))


class RunSettingsExportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_data_dir = app_settings.data_dir
        app_settings.data_dir = self._tmp.name
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        app_settings.data_dir = self._orig_data_dir
        self._tmp.cleanup()

    def test_run_settings_export_writes_a_real_file(self):
        self.db.add(AppSetting(key="ollama", value=json.dumps({"model": "x"})))
        self.db.commit()
        result = settings_export.run_settings_export(self.db, "0.72.0")
        self.assertTrue(result["ok"])
        with open(result["path"]) as f:
            written = json.load(f)
        self.assertEqual(written["app_settings"]["ollama"], {"model": "x"})
        self.assertEqual(written["powarr_version"], "0.72.0")


if __name__ == "__main__":
    unittest.main()
