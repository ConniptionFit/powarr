import json

from app.models.app_setting import AppSetting
from app.models.integration import Integration
from app.services.settings_export import import_settings, preview_import
from app.tests.test_lib07_to_11 import _ApiCase


class PreviewTests(_ApiCase):
    def _seed(self):
        self.db.add(AppSetting(key="cleanup", value=json.dumps({"a": 1, "nested": {"x": "old"}})))
        self.db.add(AppSetting(key="qdrant", value=json.dumps({"url": "u", "api_key": "live-secret"})))
        self.db.add(Integration(name="sonarr", url="http://old", enabled=False))
        self.db.commit()

    def test_reports_changes_and_hides_redacted(self):
        self._seed()
        payload = {
            "app_settings": {
                "cleanup": {"a": 1, "nested": {"x": "new"}, "extra": True},
                "qdrant": {"url": "u", "api_key": "***REDACTED***"},
                "brand_new": {"k": 1},
            },
            "integrations": [{"name": "sonarr", "url": "http://new", "enabled": True},
                             {"name": "radarr", "url": "r", "enabled": False}],
        }
        out = preview_import(self.db, payload)
        by = {s["key"]: s for s in out["app_settings"]}
        self.assertEqual(by["brand_new"]["status"], "new")
        paths = {c["path"]: c for c in by["cleanup"]["changes"]}
        self.assertEqual(paths["nested.x"]["old"], "old")
        self.assertEqual(paths["nested.x"]["new"], "new")
        self.assertIsNone(paths["extra"]["old"])
        self.assertNotIn("qdrant", by)  # only the redacted field differed -> unchanged
        self.assertEqual(out["settings_unchanged"], 1)
        integ = {i["name"]: i for i in out["integrations"]}
        self.assertEqual({c["path"] for c in integ["sonarr"]["changes"]}, {"url", "enabled"})
        self.assertEqual(integ["radarr"]["status"], "new")

    def test_preview_writes_nothing_and_matches_import(self):
        self._seed()
        before = [(r.key, r.value) for r in self.db.query(AppSetting).order_by(AppSetting.key)]
        payload = {"app_settings": {"cleanup": {"a": 2}}, "integrations": []}
        preview_import(self.db, payload)
        self.assertEqual(before, [(r.key, r.value) for r in self.db.query(AppSetting).order_by(AppSetting.key)])
        import_settings(self.db, payload)
        self.assertEqual(preview_import(self.db, payload)["app_settings"], [])

    def test_endpoint_rejects_bad_shape(self):
        r = self.client.post("/api/v1/settings/import/preview", json={"app_settings": [1]})
        self.assertEqual(r.status_code, 400)
