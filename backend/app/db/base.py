"""Declarative base + shared column conventions."""

from __future__ import annotations

import uuid
from datetime import datetime
from ipaddress import ip_address

from sqlalchemy import DateTime, MetaData, TypeDecorator, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names so Alembic autogenerate produces stable diffs.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class INETStr(TypeDecorator):
    """``INET`` that behaves like ``str`` in Python and never rejects input.

    Postgres ``INET`` is the right storage type — it validates, sorts correctly
    and supports subnet operators — but it has two sharp edges:

    1. asyncpg returns ``ipaddress.IPv4Address`` objects, which blow up Pydantic
       serialisation at the API boundary.
    2. It **rejects** anything that is not an address. Client addresses arrive
       from proxies and test harnesses and are not always well formed; a
       hostname in ``X-Forwarded-For`` would otherwise raise mid-INSERT and turn
       a successful login into a 500 while writing its own audit log.

    Both are normalised here, once, rather than at every call site. An
    unparseable address is stored as NULL: losing a diagnostic field is always
    preferable to failing the user's request over it.
    """

    impl = INET
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        try:
            return str(ip_address(str(value)))
        except ValueError:
            return None

    def process_result_value(self, value, dialect):
        return str(value) if value is not None else None


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
