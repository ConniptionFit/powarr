import os
import unittest
from unittest import mock


class TestSecretBox(unittest.TestCase):
    def test_passthrough_without_key(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("POWARR_FERNET_KEY", None)
            from app.services import secret_box
            secret_box._fernet.cache_clear()
            self.assertFalse(secret_box.encryption_enabled())
            self.assertEqual(secret_box.encrypt("hello"), "hello")
            self.assertEqual(secret_box.decrypt("hello"), "hello")
            secret_box._fernet.cache_clear()

    def test_roundtrip_with_key(self):
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            self.skipTest("cryptography not installed")
        key = Fernet.generate_key().decode()
        with mock.patch.dict(os.environ, {"POWARR_FERNET_KEY": key}):
            from app.services import secret_box
            secret_box._fernet.cache_clear()
            self.assertTrue(secret_box.encryption_enabled())
            enc = secret_box.encrypt("super-secret")
            self.assertNotEqual(enc, "super-secret")
            self.assertTrue(enc.startswith("gAAAAA"))
            self.assertEqual(secret_box.decrypt(enc), "super-secret")
            # legacy cleartext still readable
            self.assertEqual(secret_box.decrypt("plain"), "plain")
            secret_box._fernet.cache_clear()


class TestSafeBackupPath(unittest.TestCase):
    def test_rejects_traversal(self):
        from app.services.backup import safe_backup_path
        with self.assertRaises(ValueError):
            safe_backup_path("../etc/passwd")
        with self.assertRaises(ValueError):
            safe_backup_path("evil.sql")


class TestCircuitBreaker(unittest.TestCase):
    def test_opens_after_threshold(self):
        from app.services import circuit_breaker
        circuit_breaker._stats.clear()
        circuit_breaker.set_config(threshold=3, cooldown_minutes=10)
        for _ in range(3):
            circuit_breaker.record_result("sonarr", False, "boom")
        self.assertTrue(circuit_breaker.breaker_open("sonarr"))
        circuit_breaker.reset_breaker("sonarr")
        self.assertFalse(circuit_breaker.breaker_open("sonarr"))


if __name__ == "__main__":
    unittest.main()
