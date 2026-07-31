"""Connection-string normalisation.

Pure string manipulation, but it is the difference between "paste the URL your
provider gave you" and "crash loop in a hosted log viewer", so it is worth
pinning down precisely.
"""

from __future__ import annotations

import pytest
from app.core.dburl import async_url, normalise, redact, sync_url

NEON = (
    "postgresql://owner:npg_secret@ep-cool-pine-a1.ap-southeast-1.aws.neon.tech"
    "/neondb?sslmode=require&channel_binding=require"
)
SUPABASE = "postgresql://postgres:pw@db.abcdefgh.supabase.co:5432/postgres?sslmode=require"
RENDER = "postgres://user:pw@dpg-abc123-a.oregon-postgres.render.com/mydb"
LOCAL = "postgresql+asyncpg://vutto:vutto@localhost:5432/vutto"


# ------------------------------------------------------------------- async
def test_neon_url_becomes_valid_for_asyncpg():
    """asyncpg's connect() has no `sslmode` or `channel_binding` kwarg; passing
    them through raises TypeError on the first connection."""
    result = async_url(NEON)
    assert result.startswith("postgresql+asyncpg://")
    assert "ssl=require" in result
    assert "sslmode" not in result
    assert "channel_binding" not in result


def test_asyncpg_url_only_receives_kwargs_asyncpg_accepts():
    """The regression guard: prove the translated URL yields a connect() call
    the installed driver actually supports."""
    import inspect

    import asyncpg
    from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg
    from sqlalchemy.engine import make_url

    _, kwargs = PGDialect_asyncpg().create_connect_args(make_url(async_url(NEON)))
    accepted = set(inspect.signature(asyncpg.connect).parameters)
    assert set(kwargs) <= accepted, f"asyncpg cannot accept {set(kwargs) - accepted}"


@pytest.mark.parametrize(
    ("sslmode", "expected"),
    [
        ("require", "require"),
        ("verify-full", "verify-full"),
        ("verify-ca", "verify-ca"),
        ("prefer", "prefer"),
        ("allow", "prefer"),  # asyncpg has no 'allow'
        ("disable", "disable"),
        ("nonsense", "require"),  # fail closed, never silently plaintext
    ],
)
def test_sslmode_maps_to_an_asyncpg_ssl_value(sslmode, expected):
    assert f"ssl={expected}" in async_url(f"postgresql://u:p@h/db?sslmode={sslmode}")


def test_an_explicit_ssl_param_is_preserved():
    assert "ssl=verify-full" in async_url("postgresql://u:p@h/db?ssl=verify-full")


# -------------------------------------------------------------------- sync
def test_sync_url_uses_psycopg2_and_keeps_sslmode():
    result = sync_url(NEON)
    assert result.startswith("postgresql+psycopg2://")
    assert "sslmode=require" in result


def test_sync_url_translates_an_ssl_param_back_to_sslmode():
    result = sync_url("postgresql+asyncpg://u:p@h/db?ssl=require")
    assert "sslmode=require" in result
    assert "ssl=require" not in result.replace("sslmode=require", "")


# ----------------------------------------------------------------- general
@pytest.mark.parametrize("url", [NEON, SUPABASE, RENDER, LOCAL])
def test_both_drivers_are_produced_for_every_provider_shape(url):
    assert async_url(url).startswith("postgresql+asyncpg://")
    assert sync_url(url).startswith("postgresql+psycopg2://")


@pytest.mark.parametrize("url", [NEON, SUPABASE, RENDER, LOCAL])
def test_host_database_and_credentials_survive(url):
    for produced in (async_url(url), sync_url(url)):
        assert "@" in produced
        assert produced.rsplit("/", 1)[-1].split("?")[0] in url


def test_normalisation_is_idempotent():
    once = async_url(NEON)
    assert async_url(once) == once
    assert sync_url(sync_url(NEON)) == sync_url(NEON)


def test_the_bare_postgres_scheme_is_upgraded():
    """Render and Heroku hand out `postgres://`, which SQLAlchemy 2 rejects."""
    assert async_url(RENDER).startswith("postgresql+asyncpg://")


def test_non_postgres_urls_are_left_alone():
    assert normalise("sqlite+aiosqlite:///./x.db") == "sqlite+aiosqlite:///./x.db"


def test_redaction_drops_credentials_but_keeps_the_target():
    safe = redact(NEON)
    assert "npg_secret" not in safe
    assert "owner" not in safe
    assert "neon.tech" in safe and "neondb" in safe


def test_settings_expose_both_urls(monkeypatch):
    from app.core.config import Settings

    settings = Settings(database_url=NEON)
    assert "ssl=require" in settings.async_database_url
    assert "sslmode=require" in settings.sync_database_url
    assert "npg_secret" not in settings.safe_database_url


# --------------------------------------------------------------- CORS env
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://a.dev,https://b.dev", ["https://a.dev", "https://b.dev"]),
        ("https://a.dev, https://b.dev ", ["https://a.dev", "https://b.dev"]),
        ("https://a.dev/", ["https://a.dev"]),  # trailing slash stripped
        ('["https://a.dev","https://b.dev"]', ["https://a.dev", "https://b.dev"]),
        ("https://only.dev", ["https://only.dev"]),
    ],
)
def test_cors_origins_parses_the_shapes_people_actually_type(monkeypatch, raw, expected):
    """Regression guard for a boot-time crash.

    pydantic-settings JSON-decodes list-typed fields directly from the
    environment, so `CORS_ORIGINS=https://a.dev,https://b.dev` — the form used
    by docker-compose, Render and every deployment doc — raised a
    JSONDecodeError at import. The app would not start at all.
    """
    from app.core.config import Settings

    monkeypatch.setenv("CORS_ORIGINS", raw)
    assert Settings().cors_origins == expected


def test_a_trailing_slash_does_not_break_origin_matching(monkeypatch):
    """Browsers send `Origin: https://x.dev` with no trailing slash; a stray one
    in configuration would silently reject every cross-origin request."""
    from app.core.config import Settings

    monkeypatch.setenv("CORS_ORIGINS", "https://vutto.pages.dev/")
    assert Settings().cors_origins == ["https://vutto.pages.dev"]
