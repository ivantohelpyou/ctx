"""ctx.db — the durable half.

The durable half is a SQLAlchemy models.py; FTS5 is a virtual table outside
SQLAlchemy, created in one raw DDL step.

This models.py holds the two tables `index.db` (the disposable FTS5 half,
built by `indexer.py`) does not: the query log and the miss log. It is written
as plain declarative SQLAlchemy (`DeclarativeBase`, `Mapped`/`mapped_column`).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Query(Base):
    """One row per `ctx get` call — the query log the memo asks for."""

    __tablename__ = "queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    corpus: Mapped[str | None] = mapped_column(String(64), nullable=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    budget: Mapped[int] = mapped_column(Integer, nullable=False)
    returned: Mapped[int] = mapped_column(Integer, nullable=False)
    # NULL until something records whether the answer was actually used;
    # v1 has no interactive feedback step, so this stays NULL in practice.
    useful: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class Miss(Base):
    """A query that returned nothing useful — `returned == 0` is logged here
    automatically; a human or a later pass can add a `note` explaining why."""

    __tablename__ = "misses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
