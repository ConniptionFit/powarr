"""Fernet encryption for upstream integration secrets at rest (v0.34.0, SEC-03).

Key: POWARR_FERNET_KEY (urlsafe-base64 32-byte key). When unset, values stay
cleartext (fail-soft — no lockout on upgrade). Legacy cleartext rows decrypt as
passthrough; encrypt-on-write converts them over time.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

logger = logging.getLogger("powarr")

# Fernet tokens are urlsafe-base64 and start with this version prefix.
_FERNET_PREFIX = "gAAAAA"


@lru_cache(maxsize=1)
def _fernet():
    key = (os.environ.get("POWARR_FERNET_KEY") or "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:
        logger.warning(f"POWARR_FERNET_KEY invalid — secrets stay cleartext: {e}")
        return None


def encryption_enabled() -> bool:
    return _fernet() is not None


def encrypt(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    f = _fernet()
    if not f:
        return value
    if value.startswith(_FERNET_PREFIX):
        return value  # already ciphertext
    try:
        return f.encrypt(value.encode()).decode()
    except Exception as e:
        logger.warning(f"secret encrypt failed — storing cleartext: {e}")
        return value


def decrypt(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    f = _fernet()
    if not f or not value.startswith(_FERNET_PREFIX):
        return value  # cleartext legacy or no key
    try:
        return f.decrypt(value.encode()).decode()
    except Exception as e:
        logger.warning(f"secret decrypt failed — returning raw value: {e}")
        return value
