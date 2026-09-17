"""Unit tests for LLM-08: per-source_app and per-Plex-library LLM policy overlays."""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.media import MediaItem
from app.schemas.settings import ImportMatchingSettings, LlmPolicies, OllamaSettings
from app.services.import_matcher import _match_policy
from app.services.media_llm import _explain_policy, eligible_candidates, rationale_key


def _ollama(**over):
    base = dict(enabled=True, host="http://x:11434", model="qwen2.5:7b")
    base.update(over)
    return OllamaSettings(**base)


def _cfg(**over):
    base = dict()
    base.update(over)
    return ImportMatchingSettings(**base)


class MatchPolicyTests(unittest.TestCase):
    def test_no_overlay_falls_back_to_global(self):
        ollama = _ollama()
        cfg = _cfg()
        enabled, model, blend = _match_policy(ollama, cfg, LlmPolicies(), "sonarr")
        self.assertTrue(enabled)
        self.assertEqual(model, "qwen2.5:7b")
        self.assertAlmostEqual(blend, 0.3)

    def test_overlay_disables_one_app_only(self):
        ollama = _ollama()
        cfg = _cfg()
        policies = LlmPolicies(by_app={"sonarr": {"match_enabled": False}})
        sonarr_enabled, _, _ = _match_policy(ollama, cfg, policies, "sonarr")
        radarr_enabled, _, _ = _match_policy(ollama, cfg, policies, "radarr")
        self.assertFalse(sonarr_enabled)
        self.assertTrue(radarr_enabled)

    def test_overlay_enables_app_when_global_toggle_off(self):
        ollama = _ollama(match_enabled=False)
        cfg = _cfg()
        policies = LlmPolicies(by_app={"lidarr": {"match_enabled": True}})
        lidarr_enabled, _, _ = _match_policy(ollama, cfg, policies, "lidarr")
        readarr_enabled, _, _ = _match_policy(ollama, cfg, policies, "readarr")
        self.assertTrue(lidarr_enabled)
        self.assertFalse(readarr_enabled)

    def test_master_switch_off_overrides_any_overlay(self):
        ollama = _ollama(enabled=False)
        cfg = _cfg()
        policies = LlmPolicies(by_app={"sonarr": {"match_enabled": True}})
        enabled, _, _ = _match_policy(ollama, cfg, policies, "sonarr")
        self.assertFalse(enabled)

    def test_no_host_overrides_any_overlay(self):
        ollama = _ollama(host="")
        cfg = _cfg()
        policies = LlmPolicies(by_app={"sonarr": {"match_enabled": True}})
        enabled, _, _ = _match_policy(ollama, cfg, policies, "sonarr")
        self.assertFalse(enabled)

    def test_model_override(self):
        ollama = _ollama()
        cfg = _cfg()
        policies = LlmPolicies(by_app={"radarr": {"match_model": "llama3:8b"}})
        _, model, _ = _match_policy(ollama, cfg, policies, "radarr")
        self.assertEqual(model, "llama3:8b")
        _, sonarr_model, _ = _match_policy(ollama, cfg, policies, "sonarr")
        self.assertEqual(sonarr_model, "qwen2.5:7b")

    def test_blend_weight_override(self):
        ollama = _ollama()
        cfg = _cfg()
        policies = LlmPolicies(by_app={"lidarr": {"llm_blend_weight": 0.8}})
        _, _, blend = _match_policy(ollama, cfg, policies, "lidarr")
        self.assertAlmostEqual(blend, 0.8)
        _, _, sonarr_blend = _match_policy(ollama, cfg, policies, "sonarr")
        self.assertAlmostEqual(sonarr_blend, 0.3)

    def test_blend_weight_zero_is_respected_not_treated_as_unset(self):
        # 0.0 is a valid weight (ignore the LLM entirely) — must not fall back
        # to the global default just because it's falsy.
        ollama = _ollama()
        cfg = _cfg()
        policies = LlmPolicies(by_app={"sonarr": {"llm_blend_weight": 0.0}})
        _, _, blend = _match_policy(ollama, cfg, policies, "sonarr")
        self.assertEqual(blend, 0.0)

    def test_unknown_app_falls_back_to_global(self):
        ollama = _ollama()
        cfg = _cfg()
        enabled, model, blend = _match_policy(ollama, cfg, LlmPolicies(), None)
        self.assertTrue(enabled)
        self.assertEqual(model, "qwen2.5:7b")
        self.assertAlmostEqual(blend, 0.3)


class ExplainPolicyTests(unittest.TestCase):
    def test_no_overlay_falls_back_to_global(self):
        ollama = _ollama()
        enabled, model = _explain_policy(ollama, LlmPolicies(), "Movies")
        self.assertTrue(enabled)
        self.assertEqual(model, "qwen2.5:7b")

    def test_overlay_disables_one_library_only(self):
        ollama = _ollama()
        policies = LlmPolicies(by_library={"Anime": {"explain_enabled": False}})
        anime_enabled, _ = _explain_policy(ollama, policies, "Anime")
        movies_enabled, _ = _explain_policy(ollama, policies, "Movies")
        self.assertFalse(anime_enabled)
        self.assertTrue(movies_enabled)

    def test_overlay_enables_library_when_global_toggle_off(self):
        ollama = _ollama(explain_enabled=False)
        policies = LlmPolicies(by_library={"Movies": {"explain_enabled": True}})
        movies_enabled, _ = _explain_policy(ollama, policies, "Movies")
        tv_enabled, _ = _explain_policy(ollama, policies, "TV Shows")
        self.assertTrue(movies_enabled)
        self.assertFalse(tv_enabled)

    def test_master_switch_off_overrides_any_overlay(self):
        ollama = _ollama(enabled=False)
        policies = LlmPolicies(by_library={"Movies": {"explain_enabled": True}})
        enabled, _ = _explain_policy(ollama, policies, "Movies")
        self.assertFalse(enabled)

    def test_model_override(self):
        ollama = _ollama()
        policies = LlmPolicies(by_library={"Music": {"explain_model": "phi4:14b"}})
        _, model = _explain_policy(ollama, policies, "Music")
        self.assertEqual(model, "phi4:14b")
        _, movies_model = _explain_policy(ollama, policies, "Movies")
        self.assertEqual(movies_model, "qwen2.5:7b")


_item_counter = [0]


def _item(**over):
    _item_counter[0] += 1
    base = dict(plex_rating_key=f"rk{_item_counter[0]}", title="Title", media_type="movie",
                file_size=1024 ** 3, ignored=False, score=80.0, watch_count=0,
                library_section="Movies")
    base.update(over)
    return MediaItem(**base)


class EligibleCandidatesLibraryFilterTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def test_disabled_library_excluded_from_backlog(self):
        self.db.add_all([
            _item(library_section="Movies"),
            _item(library_section="Anime"),
        ])
        self.db.commit()
        ollama = _ollama()
        policies = LlmPolicies(by_library={"Anime": {"explain_enabled": False}})
        out = eligible_candidates(self.db, ollama, None, 50, policies)
        libs = {i.library_section for i in out}
        self.assertEqual(libs, {"Movies"})

    def test_disabled_library_excluded_even_with_explicit_ids(self):
        item = _item(library_section="Anime")
        self.db.add(item)
        self.db.commit()
        ollama = _ollama()
        policies = LlmPolicies(by_library={"Anime": {"explain_enabled": False}})
        out = eligible_candidates(self.db, ollama, [item.id], 50, policies)
        self.assertEqual(out, [])

    def test_rationale_key_changes_with_library_model_override(self):
        item = _item(library_section="Music")
        ollama = _ollama()
        k1 = rationale_key(ollama, item, LlmPolicies())
        k2 = rationale_key(ollama, item, LlmPolicies(by_library={"Music": {"explain_model": "other"}}))
        self.assertNotEqual(k1, k2)


if __name__ == "__main__":
    unittest.main()
