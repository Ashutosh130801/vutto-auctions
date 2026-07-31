"""Operational entrypoints: ``python -m app.cli <command>``.

Why not just call the ``alembic`` console script?  Because console scripts live
on ``PATH``, and ``PATH`` is the least portable thing about a deployment.
Buildpack platforms, slim images and ``sh`` entrypoints all find ways to lose
it, and the failure — ``alembic: not found`` — happens *after* the image builds
fine, in a hosted log viewer, at deploy time.

Invoking through the interpreter that is already running the app cannot lose
that race.  The ``alembic`` CLI still works for local development; this is the
form the containers use.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def migrate(revision: str = "head") -> int:
    """Apply migrations, resolving alembic.ini relative to the package.

    Resolving the path this way means the command works from any working
    directory, which matters because platforms disagree about what the CWD is.
    """
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    command.upgrade(config, revision)
    return 0


def downgrade(revision: str = "-1") -> int:
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    command.downgrade(config, revision)
    return 0


def seed() -> int:
    import asyncio

    from app.db.session import dispose_engine
    from app.seed import seed as run_seed

    async def _run() -> None:
        try:
            await run_seed()
        finally:
            await dispose_engine()

    asyncio.run(_run())
    return 0


def check() -> int:
    """Print the resolved configuration, with credentials redacted.

    The first thing you want when a deployment misbehaves is confirmation of
    what it actually read from the environment.
    """
    from app.core.config import settings

    print(f"app_env       : {settings.app_env}")
    print(f"database      : {settings.safe_database_url}")
    print(f"redis         : {'configured' if settings.redis_url else 'disabled'}")
    print(f"cors_origins  : {settings.cors_origins}")
    print(f"metrics       : {settings.metrics_enabled}")
    print(f"secret_key    : {'set' if settings.secret_key else 'MISSING'}")
    return 0


COMMANDS: dict[str, Callable[..., int]] = {
    "migrate": migrate,
    "downgrade": downgrade,
    "seed": seed,
    "check": check,
}


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] not in COMMANDS:
        print(f"usage: python -m app.cli {{{'|'.join(COMMANDS)}}} [args]", file=sys.stderr)
        return 2
    command, *rest = args
    return COMMANDS[command](*rest)


if __name__ == "__main__":
    raise SystemExit(main())
