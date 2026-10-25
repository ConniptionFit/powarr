import asyncio
import unittest
from unittest.mock import patch

from app.services import circuit_breaker, scheduler


class TransitionTests(unittest.TestCase):
    def test_new_and_recovered(self):
        self.assertEqual(scheduler.breaker_transitions({"a", "b"}, {"b", "c"}), ({"a"}, {"c"}))
        self.assertEqual(scheduler.breaker_transitions(set(), set()), (set(), set()))
        self.assertEqual(scheduler.breaker_transitions({"a"}, {"a"}), (set(), set()))


class NotifyTests(unittest.TestCase):
    def setUp(self):
        scheduler._outage_notified.clear()
        circuit_breaker._stats.clear()
        circuit_breaker.set_config(2, 10)

    def tearDown(self):
        circuit_breaker._stats.clear()
        scheduler._outage_notified.clear()

    def _run(self, sent):
        from app.schemas.settings import NotificationSettings
        calls = []

        async def fake_notify(db, title, message, **kw):
            calls.append(title)
            return sent

        with patch("app.services.notifier.load_settings",
                   return_value=NotificationSettings(enabled=True)), \
             patch("app.services.notifier.notify", fake_notify):
            asyncio.run(scheduler._notify_integration_outages(None))
        return calls

    def test_alerts_once_then_recovery(self):
        circuit_breaker.record_result("sonarr", False, "boom")
        circuit_breaker.record_result("sonarr", False, "boom")
        self.assertEqual(self._run(True), ["Sonarr is unreachable"])
        self.assertEqual(self._run(True), [])  # still down: no repeat
        circuit_breaker.reset_breaker("sonarr")
        self.assertEqual(self._run(True), ["Sonarr recovered"])

    def test_failed_send_is_retried_next_tick(self):
        circuit_breaker.record_result("radarr", False, "x")
        circuit_breaker.record_result("radarr", False, "x")
        self.assertEqual(self._run(False), ["Radarr is unreachable"])
        self.assertEqual(self._run(True), ["Radarr is unreachable"])

    def test_disabled_channels_do_nothing(self):
        from app.schemas.settings import NotificationSettings
        circuit_breaker.record_result("x", False, "e"); circuit_breaker.record_result("x", False, "e")
        with patch("app.services.notifier.load_settings", return_value=NotificationSettings()):
            asyncio.run(scheduler._notify_integration_outages(None))
        self.assertEqual(scheduler._outage_notified, set())
