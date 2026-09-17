"""Unit tests for NOTIF-01: Discord webhook notification channel."""
import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.app_setting import AppSetting
from app.schemas.settings import NotificationSettings
from app.services import notifier


class ParseActionLinkTests(unittest.TestCase):
    def test_parses_label_and_url(self):
        spec = "http, Accept, https://powarr.example/api/v1/imports/notify-action?token=abc, method=GET, clear=true"
        self.assertEqual(notifier._parse_action_link(spec), ("Accept", "https://powarr.example/api/v1/imports/notify-action?token=abc"))

    def test_garbage_spec_returns_none(self):
        self.assertIsNone(notifier._parse_action_link("not a valid spec"))


class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class _FakeAsyncClient:
    def __init__(self, calls, status_code=200):
        self._calls = calls
        self._status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kwargs):
        self._calls.append((url, kwargs))
        return _FakeResponse(self._status_code)


class NotifyFanoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def _save(self, cfg: NotificationSettings):
        self.db.add(AppSetting(key="notifications", value=json.dumps(cfg.model_dump())))
        self.db.commit()

    async def test_neither_channel_enabled_sends_nothing(self):
        self._save(NotificationSettings())
        calls = []
        with patch("app.services.notifier.httpx.AsyncClient", lambda **kw: _FakeAsyncClient(calls)):
            ok = await notifier.notify(self.db, "t", "m")
        self.assertFalse(ok)
        self.assertEqual(calls, [])

    async def test_discord_only_posts_to_webhook(self):
        self._save(NotificationSettings(discord_enabled=True, discord_webhook_url="https://discord.com/api/webhooks/1/abc"))
        calls = []
        with patch("app.services.notifier.httpx.AsyncClient", lambda **kw: _FakeAsyncClient(calls)):
            ok = await notifier.notify(self.db, "Powarr test", "hello")
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        url, kwargs = calls[0]
        self.assertEqual(url, "https://discord.com/api/webhooks/1/abc")
        self.assertEqual(kwargs["json"]["embeds"][0]["title"], "Powarr test")

    async def test_discord_enabled_but_blank_webhook_sends_nothing(self):
        self._save(NotificationSettings(discord_enabled=True, discord_webhook_url=""))
        calls = []
        with patch("app.services.notifier.httpx.AsyncClient", lambda **kw: _FakeAsyncClient(calls)):
            ok = await notifier.notify(self.db, "t", "m")
        self.assertFalse(ok)
        self.assertEqual(calls, [])

    async def test_ntfy_only_does_not_touch_discord(self):
        self._save(NotificationSettings(enabled=True, ntfy_url="http://ntfy.local", topic="powarr"))
        calls = []
        with patch("app.services.notifier.httpx.AsyncClient", lambda **kw: _FakeAsyncClient(calls)):
            ok = await notifier.notify(self.db, "t", "m")
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        self.assertIn("ntfy.local", calls[0][0])

    async def test_action_links_appended_to_discord_description(self):
        self._save(NotificationSettings(discord_enabled=True, discord_webhook_url="https://discord.com/api/webhooks/1/abc"))
        calls = []
        actions = ["http, Accept, https://x/accept?token=1, method=GET, clear=true",
                   "http, Reject, https://x/reject?token=2, method=GET, clear=true"]
        with patch("app.services.notifier.httpx.AsyncClient", lambda **kw: _FakeAsyncClient(calls)):
            await notifier.notify(self.db, "New suggestion", "matched", actions=actions)
        description = calls[0][1]["json"]["embeds"][0]["description"]
        self.assertIn("[Accept](https://x/accept?token=1)", description)
        self.assertIn("[Reject](https://x/reject?token=2)", description)

    async def test_one_channel_failure_does_not_block_the_other(self):
        self._save(NotificationSettings(enabled=True, ntfy_url="http://ntfy.local", topic="powarr",
                                         discord_enabled=True, discord_webhook_url="https://discord.com/api/webhooks/1/abc"))
        calls = []

        class _PartiallyFailingClient(_FakeAsyncClient):
            async def post(self, url, **kwargs):
                if "ntfy" in url:
                    raise RuntimeError("ntfy unreachable")
                return await super().post(url, **kwargs)

        with patch("app.services.notifier.httpx.AsyncClient", lambda **kw: _PartiallyFailingClient(calls)):
            ok = await notifier.notify(self.db, "t", "m")
        # ntfy raised (caught inside _notify_ntfy, fails soft) but Discord still sent.
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        self.assertIn("discord.com", calls[0][0])


class NotificationsEndpointRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def test_webhook_url_never_echoed_and_set_flag_true(self):
        from app.api.v1.settings import get_notifications, update_notifications
        body = NotificationSettings(discord_enabled=True, discord_webhook_url="https://discord.com/api/webhooks/1/real-secret")
        out = update_notifications(body, self.db)
        self.assertEqual(out.discord_webhook_url, "")
        self.assertTrue(out.discord_webhook_url_set)

        fetched = get_notifications(self.db)
        self.assertEqual(fetched.discord_webhook_url, "")
        self.assertTrue(fetched.discord_webhook_url_set)

    def test_blank_webhook_on_update_keeps_existing(self):
        from app.api.v1.settings import get_notifications, update_notifications
        update_notifications(NotificationSettings(discord_enabled=True, discord_webhook_url="https://discord.com/api/webhooks/1/real-secret"), self.db)
        # Save again with discord_webhook_url blank (as the frontend does when unchanged)
        out = update_notifications(NotificationSettings(discord_enabled=True, discord_webhook_url=""), self.db)
        self.assertTrue(out.discord_webhook_url_set)

        # The underlying stored value actually is preserved (decrypts/reads back non-blank)
        row = self.db.query(AppSetting).filter_by(key="notifications").first()
        stored = json.loads(row.value)
        self.assertTrue(stored["discord_webhook_url"])

    def test_no_webhook_configured_reports_unset(self):
        from app.api.v1.settings import get_notifications
        fetched = get_notifications(self.db)
        self.assertFalse(fetched.discord_webhook_url_set)
        self.assertEqual(fetched.discord_webhook_url, "")


if __name__ == "__main__":
    unittest.main()
