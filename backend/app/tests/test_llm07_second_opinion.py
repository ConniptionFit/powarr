"""Unit tests for LLM-07: deletion-score second opinions ("risky delete")."""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.media import MediaItem
from app.schemas.settings import OllamaSettings
from app.services.media_llm import item_summary, rationale_key, second_opinion_key


def _item(**over):
    base = dict(title="Title", media_type="movie", file_size=1024 ** 3, ignored=False,
                score=80.0, watch_count=0)
    base.update(over)
    return MediaItem(**base)


def _ollama(**over):
    base = dict(enabled=True, host="http://x:11434", model="qwen2.5:7b")
    base.update(over)
    return OllamaSettings(**base)


class TaskEnabledSecondOpinionTests(unittest.TestCase):
    def test_disabled_by_default(self):
        ollama = _ollama()  # second_opinion_enabled defaults False
        self.assertFalse(ollama.task_enabled("second_opinion"))

    def test_enabled_when_toggled_on(self):
        ollama = _ollama(second_opinion_enabled=True)
        self.assertTrue(ollama.task_enabled("second_opinion"))

    def test_disabled_without_master_switch(self):
        ollama = _ollama(second_opinion_enabled=True, enabled=False)
        self.assertFalse(ollama.task_enabled("second_opinion"))

    def test_model_for_uses_override(self):
        ollama = _ollama(second_opinion_model="llama3:8b")
        self.assertEqual(ollama.model_for("second_opinion"), "llama3:8b")

    def test_model_for_falls_back_to_shared_model(self):
        ollama = _ollama()
        self.assertEqual(ollama.model_for("second_opinion"), "qwen2.5:7b")

    def test_explain_and_match_unaffected(self):
        # Ternary-to-dict refactor of model_for/task_enabled must not change
        # the existing "match"/"explain" behavior.
        ollama = _ollama(match_model="m", explain_model="e")
        self.assertEqual(ollama.model_for("match"), "m")
        self.assertEqual(ollama.model_for("explain"), "e")
        self.assertTrue(ollama.task_enabled("match"))
        self.assertTrue(ollama.task_enabled("explain"))


class RiskyDeleteTests(unittest.TestCase):
    def test_risky_when_verdict_is_keep(self):
        item = _item(llm_second_opinion="KEEP")
        self.assertTrue(item.risky_delete)

    def test_not_risky_when_verdict_is_delete(self):
        item = _item(llm_second_opinion="DELETE")
        self.assertFalse(item.risky_delete)

    def test_not_risky_when_no_verdict_yet(self):
        item = _item()
        self.assertFalse(item.risky_delete)


class ItemSummaryProtectionFlagsTests(unittest.TestCase):
    def test_no_protection_note_when_no_flags(self):
        item = _item()
        self.assertNotIn("PROTECTED", item_summary(item))

    def test_seerr_protection_named(self):
        item = _item(protected=True)
        self.assertIn("PROTECTED: Seerr-requested", item_summary(item))

    def test_multiple_flags_all_named(self):
        item = _item(seeding_protected=True, progress_protected=True)
        summary = item_summary(item)
        self.assertIn("actively seeding", summary)
        self.assertIn("in-progress watch", summary)

    def test_watch_protected_named(self):
        item = _item(watch_protected=True)
        self.assertIn("watched by another household user recently", item_summary(item))


class SecondOpinionKeyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def test_key_changes_when_score_changes(self):
        ollama = _ollama(second_opinion_enabled=True)
        item = _item(score=80.0)
        k1 = second_opinion_key(ollama, item)
        item.score = 40.0
        k2 = second_opinion_key(ollama, item)
        self.assertNotEqual(k1, k2)

    def test_key_changes_when_model_override_changes(self):
        item = _item()
        k1 = second_opinion_key(_ollama(second_opinion_enabled=True), item)
        k2 = second_opinion_key(_ollama(second_opinion_enabled=True, second_opinion_model="other"), item)
        self.assertNotEqual(k1, k2)

    def test_second_opinion_key_independent_of_rationale_key(self):
        # Different task configs (explain_model vs second_opinion_model) must not
        # collide even when everything else about the item is identical.
        item = _item()
        ollama = _ollama(second_opinion_enabled=True, explain_model="e", second_opinion_model="s")
        self.assertNotEqual(rationale_key(ollama, item), second_opinion_key(ollama, item))


if __name__ == "__main__":
    unittest.main()
