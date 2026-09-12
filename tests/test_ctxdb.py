from sqlalchemy import select
from sqlalchemy.orm import Session

from ctx_cli.ctxdb.db import get_engine, log_query
from ctx_cli.ctxdb.models import Miss, Query


def test_log_query_records_a_row(config):
    log_query(config.ctx_db_path, query="fts5", budget=1500, returned=3, corpus="explainers")
    engine = get_engine(config.ctx_db_path)
    with Session(engine) as session:
        rows = session.scalars(select(Query)).all()
    assert len(rows) == 1
    assert rows[0].query == "fts5"
    assert rows[0].returned == 3


def test_zero_results_logged_as_a_miss(config):
    log_query(config.ctx_db_path, query="nothing matches this", budget=1500, returned=0)
    engine = get_engine(config.ctx_db_path)
    with Session(engine) as session:
        misses = session.scalars(select(Miss)).all()
    assert len(misses) == 1
    assert misses[0].query == "nothing matches this"


def test_nonzero_results_do_not_log_a_miss(config):
    log_query(config.ctx_db_path, query="fts5", budget=1500, returned=2)
    engine = get_engine(config.ctx_db_path)
    with Session(engine) as session:
        misses = session.scalars(select(Miss)).all()
    assert len(misses) == 0
