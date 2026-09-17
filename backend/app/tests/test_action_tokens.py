"""Unit tests for the ntfy click-to-act signed action tokens.
Run inside the container: python -m unittest discover -s app/tests -v"""
import time
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.action_tokens import make_action_token, verify_action_token


class TestActionTokens(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def test_roundtrip_accept(self):
        t = make_action_token(self.db, 42, "accept")
        self.assertEqual(verify_action_token(self.db, t), (42, "accept"))

    def test_roundtrip_reject(self):
        t = make_action_token(self.db, 7, "reject")
        self.assertEqual(verify_action_token(self.db, t), (7, "reject"))

    def test_expired(self):
        t = make_action_token(self.db, 1, "accept", ttl=-10)
        self.assertIsNone(verify_action_token(self.db, t))

    def test_garbage_token(self):
        self.assertIsNone(verify_action_token(self.db, "not-a-real-token"))

    def test_tampered_signature(self):
        t = make_action_token(self.db, 1, "accept")
        tampered = t[:-4] + ("0" if t[-4] != "0" else "1")
        self.assertIsNone(verify_action_token(self.db, tampered))

    def test_disallowed_action_rejected(self):
        # Same secret, but a payload naming an action outside the closed set —
        # verify_action_token must not trust it even if the signature is valid.
        from app.services.action_tokens import _get_secret
        import base64, hashlib, hmac
        secret = _get_secret(self.db)
        payload = f"5|delete_everything|{int(time.time()) + 3600}"
        sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        forged = base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()
        self.assertIsNone(verify_action_token(self.db, forged))

    def test_secret_persists_across_calls(self):
        # The secret is created lazily on first use and must stay stable —
        # otherwise every previously-issued token would break the next call.
        t1 = make_action_token(self.db, 1, "accept")
        t2 = make_action_token(self.db, 1, "accept")
        self.assertEqual(verify_action_token(self.db, t1), verify_action_token(self.db, t2))


if __name__ == "__main__":
    unittest.main()
