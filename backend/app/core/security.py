"""Password hashing and JWT issuance/verification.

Design notes
------------
* **Argon2id** for passwords (memory-hard, the current OWASP recommendation).
  ``needs_rehash`` lets us transparently upgrade parameters over time.
* **Short-lived access tokens** (15 min) + **long-lived rotating refresh
  tokens**.  Refresh tokens are opaque random strings; only their SHA-256 digest
  is stored, so a database leak cannot be replayed.  Each rotation issues a new
  token in the same *family*; presenting an already-rotated token is treated as
  theft and revokes the whole family (see ``services/auth.py``).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings

# 64 MiB / 3 passes / 4 lanes — OWASP's recommended Argon2id profile.
#
# Under APP_ENV=test we deliberately use the cheapest legal parameters. Hashing
# is by far the slowest thing in the suite (hundreds of users across ~80 tests),
# and no test asserts anything about the *cost* of the KDF — only that hashing
# and verification are correct, which is parameter-independent. Production and
# CI both run the real profile.
_TEST_MODE = settings.app_env == "test"
_hasher = (
    PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, hash_len=16, salt_len=8)
    if _TEST_MODE
    else PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)
)


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


# A real hash of a value nobody can log in with.  Verifying against it burns
# the same CPU as a genuine check, which is how ``authenticate`` keeps its
# response time independent of whether the email exists.
DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, raw)
    except (VerifyMismatchError, InvalidHashError, VerificationError, ValueError, TypeError):
        return False


def password_needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except (InvalidHashError, ValueError):
        return True


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- JWT ------------------------------------------------------------------
def create_access_token(
    *, subject: str, role: str, token_version: int, extra: dict[str, Any] | None = None
) -> tuple[str, datetime]:
    now = utcnow()
    expires = now + timedelta(seconds=settings.access_token_ttl_seconds)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "tv": token_version,  # bumped on logout-everywhere / password change
        "typ": "access",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": uuid.uuid4().hex,
        "iss": settings.app_name,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm), expires


def decode_access_token(token: str) -> dict[str, Any]:
    """Raises ``jwt.PyJWTError`` subclasses on any problem."""
    return jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.app_name,
        options={"require": ["exp", "sub", "typ"]},
    )


# --- Opaque refresh tokens -------------------------------------------------
def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Keyed digest — a DB dump alone is not enough to forge a token."""
    return hmac.new(settings.secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
