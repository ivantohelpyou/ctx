"""Date normalization (re-i1n review, finding 6).

PyYAML parses an unquoted `date: 2026-01-01` as `datetime.date`, and
`date: 2026-08-25T10:00:00-07:00` as a tz-aware `datetime.datetime` whose
naive `str()` has a space and an offset (`"2026-08-25 10:00:00-07:00"`).
Every `date`/`last-verified` value must normalize to plain `YYYY-MM-DD` in
the index, taking the calendar date as written — never shifting for
timezone — and an unparseable value must be a `ctx lint` error.
"""

from __future__ import annotations

import sqlite3

import pytest

from ctx_cli.corpus import load_config
from ctx_cli.frontmatter import DateParseError, normalize_date
from ctx_cli.indexer import build_index
from ctx_cli.lint import lint


def _write_config(rig_dir) -> None:
    (rig_dir / ".ctx" / "config.yaml").write_text(
        "corpora:\n  docs:\n    type: hugo\n    path: docs/hugo\n"
    )


@pytest.mark.parametrize(
    "raw_date, expected",
    [
        ("2026-01-01", "2026-01-01"),  # PyYAML: datetime.date
        ('"2026-01-01"', "2026-01-01"),  # PyYAML: plain string
        ("2026-08-25T10:00:00-07:00", "2026-08-25"),  # PyYAML: tz-aware datetime
        ("2026-08-25 10:00:00", "2026-08-25"),  # PyYAML: naive datetime
    ],
)
def test_all_date_input_forms_normalize_to_the_same_index_value(rig_dir, raw_date, expected):
    doc = rig_dir / "docs" / "hugo" / "1-100-example-survey.md"
    text = doc.read_text(encoding="utf-8").replace("date: 2026-01-01", f"date: {raw_date}")
    doc.write_text(text)

    _write_config(rig_dir)
    config = load_config(rig_dir / ".ctx")
    build_index(config)

    conn = sqlite3.connect(config.index_db_path)
    row = conn.execute(
        "SELECT date FROM documents WHERE id = '1-100-example-survey'"
    ).fetchone()
    conn.close()
    assert row == (expected,)


def test_datetime_date_never_shifted_for_timezone():
    """A tz-aware datetime's *local* calendar date is kept verbatim — no
    conversion to UTC or any other zone."""
    import yaml

    parsed = yaml.safe_load("date: 2026-08-25T23:30:00+09:00\n")
    assert normalize_date(parsed["date"]) == "2026-08-25"


def test_unparseable_date_raises():
    with pytest.raises(DateParseError):
        normalize_date("not a date")


def test_unparseable_date_is_a_lint_error_not_an_index_failure(rig_dir):
    doc = rig_dir / "docs" / "hugo" / "1-100-example-survey.md"
    text = doc.read_text(encoding="utf-8").replace("date: 2026-01-01", "date: not-a-real-date")
    doc.write_text(text)

    _write_config(rig_dir)
    config = load_config(rig_dir / ".ctx")

    build_index(config)  # must not raise — indexing degrades, doesn't fail

    report = lint(config)
    assert not report.ok
    assert any("unparseable 'date'" in e for e in report.errors)


def test_rm_index_and_reindex_reproduces_normalized_date_byte_identical(rig_dir):
    doc = rig_dir / "docs" / "hugo" / "1-100-example-survey.md"
    text = doc.read_text(encoding="utf-8").replace(
        "date: 2026-01-01", "date: 2026-08-25T10:00:00-07:00"
    )
    doc.write_text(text)

    _write_config(rig_dir)
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    before = conn.execute(
        "SELECT date FROM documents WHERE id = '1-100-example-survey'"
    ).fetchone()
    conn.close()

    config.index_db_path.unlink()
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    after = conn.execute(
        "SELECT date FROM documents WHERE id = '1-100-example-survey'"
    ).fetchone()
    conn.close()

    assert before == after == ("2026-08-25",)
