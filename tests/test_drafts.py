"""`draft:` handling for `type: hugo` (re-i1n review, finding 5).

ctx must not surface what the public Hugo site hides: a page with
`draft: true` never ships. The hugo preset skips it by default; a corpus
may opt back in with `include_drafts: true`.
"""

from __future__ import annotations

import sqlite3

from ctx_cli.corpus import load_config
from ctx_cli.indexer import build_index
from ctx_cli.lint import lint


def _write_config(rig_dir, extra: str = "") -> None:
    (rig_dir / ".ctx" / "config.yaml").write_text(
        "corpora:\n  docs:\n    type: hugo\n    path: docs/hugo\n" + extra
    )


def _mark_draft(rig_dir):
    doc = rig_dir / "docs" / "hugo" / "1-100-example-survey.md"
    text = doc.read_text(encoding="utf-8")
    text = text.replace("date: 2026-01-01", "date: 2026-01-01\ndraft: true")
    doc.write_text(text)


def test_draft_excluded_from_index_by_default(rig_dir):
    _mark_draft(rig_dir)
    _write_config(rig_dir)
    config = load_config(rig_dir / ".ctx")
    build_index(config)

    conn = sqlite3.connect(config.index_db_path)
    ids = {r[0] for r in conn.execute("SELECT id FROM documents")}
    conn.close()
    assert "1-100-example-survey" not in ids


def test_include_drafts_true_overrides_the_hugo_default(rig_dir):
    _mark_draft(rig_dir)
    _write_config(rig_dir, extra="    include_drafts: true\n")
    config = load_config(rig_dir / ".ctx")
    build_index(config)

    conn = sqlite3.connect(config.index_db_path)
    ids = {r[0] for r in conn.execute("SELECT id FROM documents")}
    conn.close()
    assert "1-100-example-survey" in ids


def test_lint_reports_skipped_draft_count_and_does_not_flag_it(rig_dir):
    _mark_draft(rig_dir)
    _write_config(rig_dir)
    config = load_config(rig_dir / ".ctx")
    report = lint(config)

    assert report.skipped_drafts["docs"] == 1
    assert not any("1-100-example-survey" in e for e in report.errors)


def test_non_hugo_corpus_does_not_skip_drafts_by_default(rig_dir):
    """`skip_drafts` defaults to false for any type other than `hugo` — a
    `draft:` key in an explainer/decision/shared doc means nothing to ctx
    unless the corpus opts in."""
    doc = rig_dir / "docs" / "shared" / "lesson-001-worktrees.md"
    text = doc.read_text(encoding="utf-8")
    text = text.replace("---\n", "---\ndraft: true\n", 1)
    doc.write_text(text)

    config = load_config(rig_dir / ".ctx")  # default fixture config, no hugo corpus
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    ids = {r[0] for r in conn.execute("SELECT id FROM documents")}
    conn.close()
    assert "shared-001" in ids
