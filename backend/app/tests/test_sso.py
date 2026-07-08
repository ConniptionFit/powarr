"""SEC-02 / SSO: Powarr trusts an Authentik-asserted identity ONLY from a
configured trusted proxy, closes the leftmost-XFF spoof, and gates direct/LAN
access behind the 'allow LAN without SSO' toggle — all backward-compatible when
SSO is off."""
import unittest

from app.services import auth


class _Headers:
    def __init__(self, d=None):
        self._d = {k.lower(): v for k, v in (d or {}).items()}

    def get(self, k, default=None):
        return self._d.get(k.lower(), default)


class _Req:
    def __init__(self, peer, headers=None, cookies=None):
        self.client = type("C", (), {"host": peer})() if peer is not None else None
        self.headers = _Headers(headers)
        self.cookies = cookies or {}


def _cfg(**over):
    c = auth.default_config()
    c.update(over)
    return c


TRUSTED = ["192.168.112.0/20"]   # NPM on the shared proxy network
PROXY_PEER = "192.168.112.2"     # NPM's IP
LAN_PEER = "10.1.1.50"           # a direct LAN client


class SsoIdentityTests(unittest.TestCase):
    def test_identity_trusted_from_proxy(self):
        cfg = _cfg(sso_enabled=True, sso_trusted_proxies=TRUSTED)
        req = _Req(PROXY_PEER, {"X-Authentik-Username": "alice"})
        self.assertEqual(auth.sso_identity(req, cfg), "alice")

    def test_forged_header_from_direct_client_is_ignored(self):
        # The spoof: a LAN client sets X-Authentik-Username itself. Peer isn't a
        # trusted proxy, so the header must be ignored.
        cfg = _cfg(sso_enabled=True, sso_trusted_proxies=TRUSTED)
        req = _Req(LAN_PEER, {"X-Authentik-Username": "admin"})
        self.assertIsNone(auth.sso_identity(req, cfg))

    def test_no_identity_when_sso_off(self):
        cfg = _cfg(sso_enabled=False, sso_trusted_proxies=TRUSTED)
        req = _Req(PROXY_PEER, {"X-Authentik-Username": "alice"})
        self.assertIsNone(auth.sso_identity(req, cfg))

    def test_trusted_proxy_but_no_header(self):
        cfg = _cfg(sso_enabled=True, sso_trusted_proxies=TRUSTED)
        self.assertIsNone(auth.sso_identity(_Req(PROXY_PEER), cfg))


class ClientIpTrustTests(unittest.TestCase):
    def test_xff_trusted_only_from_proxy_when_sso_on(self):
        cfg = _cfg(sso_enabled=True, sso_trusted_proxies=TRUSTED)
        req = _Req(PROXY_PEER, {"X-Forwarded-For": "203.0.113.9, 192.168.112.2"})
        self.assertEqual(auth.client_ip(req, cfg), "203.0.113.9")

    def test_xff_ignored_from_direct_client_when_sso_on(self):
        # SEC-02 core: a LAN client forging XFF no longer moves its apparent IP.
        cfg = _cfg(sso_enabled=True, sso_trusted_proxies=TRUSTED)
        req = _Req(LAN_PEER, {"X-Forwarded-For": "8.8.8.8"})
        self.assertEqual(auth.client_ip(req, cfg), LAN_PEER)

    def test_legacy_private_peer_trust_when_sso_off(self):
        cfg = _cfg(sso_enabled=False)
        req = _Req("172.31.0.1", {"X-Forwarded-For": "10.1.1.9"})
        self.assertEqual(auth.client_ip(req, cfg), "10.1.1.9")

    def test_legacy_public_peer_not_trusted_when_sso_off(self):
        cfg = _cfg(sso_enabled=False)
        req = _Req("8.8.8.8", {"X-Forwarded-For": "10.1.1.9"})  # 8.8.8.8 = genuinely global
        self.assertEqual(auth.client_ip(req, cfg), "8.8.8.8")


class EvaluateRequestTests(unittest.TestCase):
    def test_open_when_both_off(self):
        state = auth.evaluate_request(_Req(LAN_PEER), _cfg(enabled=False, sso_enabled=False))
        self.assertTrue(state["allowed"])
        self.assertIsNone(state["via"])

    def test_sso_authenticated_via_proxy(self):
        cfg = _cfg(sso_enabled=True, sso_trusted_proxies=TRUSTED)
        state = auth.evaluate_request(_Req(PROXY_PEER, {"X-Authentik-Username": "alice"}), cfg)
        self.assertTrue(state["allowed"])
        self.assertTrue(state["authenticated"])
        self.assertEqual(state["via"], "sso")

    def test_forged_header_lan_client_denied_without_bypass(self):
        cfg = _cfg(sso_enabled=True, sso_trusted_proxies=TRUSTED,
                   sso_allow_lan_without_sso=False, lan_bypass=True)
        state = auth.evaluate_request(_Req(LAN_PEER, {"X-Authentik-Username": "admin"}), cfg)
        self.assertFalse(state["allowed"])  # spoof ignored, no LAN bypass, no session

    def test_lan_toggle_allows_direct_without_sso(self):
        cfg = _cfg(sso_enabled=True, sso_trusted_proxies=TRUSTED,
                   sso_allow_lan_without_sso=True, lan_bypass=True)
        state = auth.evaluate_request(_Req(LAN_PEER), cfg)
        self.assertTrue(state["allowed"])
        self.assertTrue(state["bypassed"])
        self.assertEqual(state["via"], "lan")

    def test_localhost_break_glass_session(self):
        secret = "s" * 64
        cfg = _cfg(enabled=True, username="powers", session_secret=secret,
                   sso_enabled=True, sso_trusted_proxies=TRUSTED,
                   sso_allow_lan_without_sso=False)
        token = auth.make_token("powers", secret)
        state = auth.evaluate_request(_Req("127.0.0.1", cookies={auth.COOKIE_NAME: token}), cfg)
        self.assertTrue(state["allowed"])
        self.assertEqual(state["via"], "session")

    def test_legacy_lan_bypass_when_sso_off(self):
        cfg = _cfg(enabled=True, username="powers", lan_bypass=True)
        state = auth.evaluate_request(_Req(LAN_PEER), cfg)
        self.assertTrue(state["allowed"])
        self.assertTrue(state["bypassed"])

    def test_legacy_public_denied_when_sso_off(self):
        cfg = _cfg(enabled=True, username="powers", lan_bypass=True)
        state = auth.evaluate_request(_Req("203.0.113.5"), cfg)
        self.assertFalse(state["allowed"])


if __name__ == "__main__":
    unittest.main()
