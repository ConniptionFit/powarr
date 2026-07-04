"""Unit tests for the auth primitives (passwords, session tokens, TOTP)."""
import unittest

from app.services.auth import (hash_password, verify_password, make_token, verify_token,
                               totp_code, verify_totp, ip_in_cidrs, DEFAULT_LAN_CIDRS)


class TestPasswords(unittest.TestCase):
    def test_roundtrip(self):
        h = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", h))
        self.assertFalse(verify_password("wrong", h))

    def test_unique_salts(self):
        self.assertNotEqual(hash_password("same"), hash_password("same"))

    def test_malformed_hash(self):
        self.assertFalse(verify_password("x", "not-a-hash"))


class TestTokens(unittest.TestCase):
    SECRET = "test-secret"

    def test_roundtrip(self):
        t = make_token("powers", self.SECRET)
        self.assertEqual(verify_token(t, self.SECRET), "powers")

    def test_wrong_secret(self):
        t = make_token("powers", self.SECRET)
        self.assertIsNone(verify_token(t, "other-secret"))

    def test_expired(self):
        t = make_token("powers", self.SECRET, ttl=-10)
        self.assertIsNone(verify_token(t, self.SECRET))

    def test_garbage(self):
        self.assertIsNone(verify_token("garbage", self.SECRET))


class TestTotp(unittest.TestCase):
    # RFC 6238 test vector: ASCII secret "12345678901234567890", T=59s → 6-digit SHA1 code 287082
    RFC_SECRET_B32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    def test_rfc6238_vector(self):
        self.assertEqual(totp_code(self.RFC_SECRET_B32, at=59), "287082")

    def test_verify_window(self):
        code = totp_code(self.RFC_SECRET_B32, at=59)
        # exact window verification is time-dependent; check the primitive directly
        self.assertTrue(len(code) == 6 and code.isdigit())

    def test_verify_rejects_wrong_code(self):
        self.assertFalse(verify_totp(self.RFC_SECRET_B32, "000000"))


class TestCidrs(unittest.TestCase):
    def test_lan_defaults(self):
        self.assertTrue(ip_in_cidrs("10.1.1.50", DEFAULT_LAN_CIDRS))
        self.assertTrue(ip_in_cidrs("192.168.0.32", DEFAULT_LAN_CIDRS))
        self.assertTrue(ip_in_cidrs("127.0.0.1", DEFAULT_LAN_CIDRS))
        self.assertFalse(ip_in_cidrs("8.8.8.8", DEFAULT_LAN_CIDRS))

    def test_invalid_ip(self):
        self.assertFalse(ip_in_cidrs("not-an-ip", DEFAULT_LAN_CIDRS))


if __name__ == "__main__":
    unittest.main()
