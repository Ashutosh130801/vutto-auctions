"""Connection-string normalisation.

Managed Postgres providers (Neon, Supabase, Render, Railway, Aiven) hand you a
**libpq** URL:

    postgresql://user:pw@host/db?sslmode=require&channel_binding=require

That string is correct for `psql` and for psycopg2, and *wrong* for asyncpg,
which takes an ``ssl`` argument and has never heard of ``sslmode`` or
``channel_binding``.  SQLAlchemy passes unrecognised query parameters straight
through to the driver, so pasting a provider URL into ``DATABASE_URL`` fails at
the first connection with:

    TypeError: connect() got an unexpected keyword argument 'sslmode'

which is an unpleasant thing to discover from a crash loop in a hosted log
viewer.  We translate instead: one URL in the environment, correct arguments for
whichever driver is asking.  The app (asyncpg) and Alembic (psycopg2) both read
the same ``DATABASE_URL``.

Everything here is pure string manipulation and is unit-tested in
``tests/test_dburl.py``.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ASYNC_DRIVER = "postgresql+asyncpg"
SYNC_DRIVER = "postgresql+psycopg2"

# libpq sslmode -> asyncpg ssl.  asyncpg has no 'allow'; 'prefer' is the closest
# honest equivalent, and 'disable' means no TLS at all.
_SSLMODE_TO_ASYNCPG = {
    "disable": "disable",
    "allow": "prefer",
    "prefer": "prefer",
    "require": "require",
    "verify-ca": "verify-ca",
    "verify-full": "verify-full",
}

# Recognised by libpq/psycopg2 but not by asyncpg's connect().
_LIBPQ_ONLY = ("channel_binding", "sslrootcert", "sslcert", "sslkey", "target_session_attrs")


def _split_scheme(url: str) -> tuple[str, str]:
    """Return ``(dialect, driver)`` from a SQLAlchemy scheme like
    ``postgresql+asyncpg``.  A bare ``postgresql`` has no driver."""
    scheme = urlsplit(url).scheme
    dialect, _, driver = scheme.partition("+")
    return dialect, driver


def normalise(url: str, *, target: str = "async") -> str:
    """Rewrite ``url`` so it is valid for the requested driver.

    ``target`` is ``"async"`` (asyncpg, used by the app) or ``"sync"``
    (psycopg2, used by Alembic).  Non-PostgreSQL URLs are returned untouched so
    this never gets in the way of an unusual setup.
    """
    parts = urlsplit(url)
    dialect, _driver = _split_scheme(url)
    if dialect not in ("postgres", "postgresql"):
        return url

    params = dict(parse_qsl(parts.query, keep_blank_values=True))

    if target == "async":
        scheme = ASYNC_DRIVER
        # asyncpg speaks `ssl`, not `sslmode`.
        sslmode = params.pop("sslmode", None)
        if sslmode and "ssl" not in params:
            params["ssl"] = _SSLMODE_TO_ASYNCPG.get(sslmode.lower(), "require")
        for key in _LIBPQ_ONLY:
            params.pop(key, None)
    else:
        scheme = SYNC_DRIVER
        # psycopg2 speaks `sslmode`, not `ssl`.
        ssl = params.pop("ssl", None)
        if ssl and "sslmode" not in params:
            params["sslmode"] = "require" if ssl.lower() in ("true", "1") else ssl

    return urlunsplit(
        (scheme, parts.netloc, parts.path, urlencode(params, doseq=True), parts.fragment)
    )


def async_url(url: str) -> str:
    """The URL the application should connect with."""
    return normalise(url, target="async")


def sync_url(url: str) -> str:
    """The URL Alembic should migrate with."""
    return normalise(url, target="sync")


def redact(url: str) -> str:
    """Safe to log: keeps host and database, drops credentials."""
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    _creds, _, host = parts.netloc.rpartition("@")
    return urlunsplit((parts.scheme, f"***@{host}", parts.path, "", ""))
