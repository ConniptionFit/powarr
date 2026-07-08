"""Regression tests for SEC-01: upstream API keys / download-client passwords are
write-only over the /integrations API. A read must never carry the stored secret,
and a blank field or the display mask must never overwrite a stored secret."""
import unittest
from types import SimpleNamespace

from app.api.v1.integrations import SECRET_MASK, _is_new_secret, _public_config


def _row(**kw):
    base = dict(name="sonarr", url="http://sonarr:8989", api_key="", username="",
                password="", enabled=True, extra_config=None)
    base.update(kw)
    return SimpleNamespace(**base)


class PublicConfigMaskingTests(unittest.TestCase):
    def test_stored_api_key_is_masked_never_echoed(self):
        secret = "a1b2c3d4e5f6realkey"
        cfg = _public_config(_row(api_key=secret), {})
        self.assertEqual(cfg.api_key, SECRET_MASK)
        self.assertTrue(cfg.api_key_set)
        # The real secret must not appear anywhere in the serialized payload.
        self.assertNotIn(secret, str(cfg.model_dump()))

    def test_absent_api_key_reports_unset(self):
        cfg = _public_config(_row(api_key=""), {})
        self.assertIsNone(cfg.api_key)
        self.assertFalse(cfg.api_key_set)

    def test_stored_password_is_masked_never_echoed(self):
        secret = "s3cr3t-webui-pass"
        cfg = _public_config(_row(name="qbittorrent", username="admin", password=secret), {})
        self.assertEqual(cfg.password, SECRET_MASK)
        self.assertTrue(cfg.password_set)
        self.assertNotIn(secret, str(cfg.model_dump()))

    def test_username_is_not_a_secret_and_passes_through(self):
        cfg = _public_config(_row(name="qbittorrent", username="admin", password="x"), {})
        self.assertEqual(cfg.username, "admin")


class NewSecretGuardTests(unittest.TestCase):
    """_is_new_secret decides whether an incoming update value replaces the stored
    secret. Only a real, non-mask value counts — this is what stops a URL-only edit
    (blank secret field) or an echoed mask from wiping/round-tripping a key."""

    def test_none_and_blank_are_not_new_secrets(self):
        self.assertFalse(_is_new_secret(None))
        self.assertFalse(_is_new_secret(""))

    def test_mask_is_not_a_new_secret(self):
        self.assertFalse(_is_new_secret(SECRET_MASK))

    def test_real_value_is_a_new_secret(self):
        self.assertTrue(_is_new_secret("brand-new-key"))


if __name__ == "__main__":
    unittest.main()
