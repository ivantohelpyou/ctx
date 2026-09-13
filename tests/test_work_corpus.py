"""`type: work` — the beads-as-markdown pilot corpus (sw-5om, ctx Phase 5b).

Frontmatter contract (importer/shim, plan §5b): `id`, `title`, `summary`,
`status`, `priority`, `type`, `deps`, `closed-reason`, `created`, `closed`,
`tags`, `terms`. These tests exercise ctx's side only: the corpus type, its
lint rules, and `ctx get`'s default exclusion of closed docs (reusing
`--history` rather than adding a new flag).
"""

from __future__ import annotations

from ctx_cli.corpus import load_config
from ctx_cli.indexer import build_index
from ctx_cli.lint import lint
from ctx_cli.query import search

OPEN_DOC = """---
id: sw-aaaa
title: Fix the frobnicator
summary: The frobnicator needs fixing before the demo.
status: open
priority: 2
type: task
deps: []
closed-reason:
created: 2026-09-01
closed:
tags: [task]
terms: []
---

Body describing the frobnicator bug.
"""

CLOSED_DOC = """---
id: sw-bbbb
title: Ship the frobnicator fix
summary: The frobnicator fix shipped.
status: closed
priority: 2
type: task
deps: [sw-aaaa]
closed-reason: shipped in v2
created: 2026-08-01
closed: 2026-08-15
tags: [task]
terms: []
---

Body.
"""


def _write_work_config(rig_dir) -> None:
    (rig_dir / "work").mkdir(exist_ok=True)
    (rig_dir / ".ctx" / "config.yaml").write_text(
        "corpora:\n  work:\n    type: work\n    path: work\n"
    )


def test_clean_work_docs_pass_lint(rig_dir):
    _write_work_config(rig_dir)
    (rig_dir / "work" / "sw-aaaa.md").write_text(OPEN_DOC)
    (rig_dir / "work" / "sw-bbbb.md").write_text(CLOSED_DOC)
    config = load_config(rig_dir / ".ctx")
    report = lint(config)
    assert report.ok, report.errors


def test_invalid_status_is_a_lint_error(rig_dir):
    _write_work_config(rig_dir)
    bad = OPEN_DOC.replace("status: open", "status: sideways")
    (rig_dir / "work" / "sw-aaaa.md").write_text(bad)
    config = load_config(rig_dir / ".ctx")
    report = lint(config)
    assert not report.ok
    assert any("invalid status" in e for e in report.errors)


def test_closed_without_closed_reason_is_a_lint_error(rig_dir):
    _write_work_config(rig_dir)
    bad = CLOSED_DOC.replace("closed-reason: shipped in v2", "closed-reason:")
    (rig_dir / "work" / "sw-bbbb.md").write_text(bad)
    config = load_config(rig_dir / ".ctx")
    report = lint(config)
    assert not report.ok
    assert any("closed-reason" in e for e in report.errors)


def test_dep_on_unknown_id_is_a_lint_error(rig_dir):
    _write_work_config(rig_dir)
    bad = CLOSED_DOC.replace("deps: [sw-aaaa]", "deps: [sw-zzzz]")
    (rig_dir / "work" / "sw-bbbb.md").write_text(bad)
    config = load_config(rig_dir / ".ctx")
    report = lint(config)
    assert not report.ok
    assert any("unknown id 'sw-zzzz'" in e for e in report.errors)


def test_missing_required_field_is_a_lint_error(rig_dir):
    _write_work_config(rig_dir)
    bad = OPEN_DOC.replace("priority: 2\n", "")
    (rig_dir / "work" / "sw-aaaa.md").write_text(bad)
    config = load_config(rig_dir / ".ctx")
    report = lint(config)
    assert not report.ok
    assert any("missing required frontmatter field 'priority'" in e for e in report.errors)


def test_default_get_excludes_closed_doc(rig_dir):
    _write_work_config(rig_dir)
    (rig_dir / "work" / "sw-aaaa.md").write_text(OPEN_DOC)
    (rig_dir / "work" / "sw-bbbb.md").write_text(CLOSED_DOC)
    config = load_config(rig_dir / ".ctx")
    build_index(config)

    default_hits = search(config.index_db_path, "frobnicator", work_corpora=["work"])
    assert any(h.id == "sw-aaaa" for h in default_hits)
    assert all(h.id != "sw-bbbb" for h in default_hits)  # closed, hidden by default


def test_history_flag_includes_closed_doc(rig_dir):
    _write_work_config(rig_dir)
    (rig_dir / "work" / "sw-aaaa.md").write_text(OPEN_DOC)
    (rig_dir / "work" / "sw-bbbb.md").write_text(CLOSED_DOC)
    config = load_config(rig_dir / ".ctx")
    build_index(config)

    history_hits = search(config.index_db_path, "frobnicator", history=True)
    assert any(h.id == "sw-bbbb" for h in history_hits)
    # No supersession annotation for a closed work doc — it wasn't replaced.
    bbbb = next(h for h in history_hits if h.id == "sw-bbbb")
    assert bbbb.superseded_by == []
    assert bbbb.cycle is False


def test_other_corpus_types_unaffected_by_work_filter(rig_dir):
    """`work_corpora` only ever excludes rows from corpora actually of that
    type — an explainer or shared doc keeps showing up regardless of its own
    (nonexistent) status, same guarantee the decisions filter gives (query.py
    docstring, hq-sl5k round 2)."""
    config = load_config(rig_dir / ".ctx")  # fixture rig: explainers/decisions/shared, no work
    build_index(config)
    hits = search(config.index_db_path, "bm25", work_corpora=["work"])
    assert any(h.id == "exp-001" for h in hits)
