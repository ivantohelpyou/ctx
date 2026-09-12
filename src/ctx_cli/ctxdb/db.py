"""Engine + logging helpers for the durable `ctx.db`."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .models import Base, Miss, Query


def get_engine(ctx_db_path: Path):
    ctx_db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{ctx_db_path}")
    Base.metadata.create_all(engine)
    return engine


def log_query(
    ctx_db_path: Path,
    query: str,
    budget: int,
    returned: int,
    corpus: str | None = None,
) -> None:
    """Log every `ctx get` call; a zero-result query is also logged as a miss
    (memo: "keep a log of queries that returned nothing useful")."""
    engine = get_engine(ctx_db_path)
    with Session(engine) as session:
        session.add(Query(corpus=corpus, query=query, budget=budget, returned=returned))
        if returned == 0:
            session.add(Miss(query=query, note=None))
        session.commit()
