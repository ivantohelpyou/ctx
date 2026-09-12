"""`type: hugo` and the generic `map:` config key (re-i1n).

Research's docs/ corpus is Hugo content: the build reads `description:`,
`keywords:`, `aliases:`, `date:` and must keep reading them unchanged. ctx's
contract keys are `summary`/`tags`/`terms`/`id`. These tests exercise the
per-corpus key map that bridges the two without ever rewriting a source file.
"""

from __future__ import annotations

import sqlite3

from ctx_cli.corpus import load_config
from ctx_cli.indexer import build_index
from ctx_cli.lint import lint
from ctx_cli.query import search


def _write_hugo_config(rig_dir, extra: str = "") -> None:
    (rig_dir / ".ctx" / "config.yaml").write_text(
        "corpora:\n"
        "  docs:\n"
        "    type: hugo\n"
        "    path: docs/hugo\n" + extra
    )


def test_hugo_maps_description_to_summary_and_keywords_to_tags(rig_dir):
    _write_hugo_config(rig_dir)
    config = load_config(rig_dir / ".ctx")
    build_index(config)

    conn = sqlite3.connect(config.index_db_path)
    row = conn.execute(
        "SELECT summary, tags FROM documents WHERE id = '1-100-example-survey'"
    ).fetchone()
    conn.close()

    assert row == (
        "One-line Hugo description used as the ctx summary.",
        "alpha beta",
    )


def test_hugo_source_file_is_never_rewritten(rig_dir):
    doc_path = rig_dir / "docs" / "hugo" / "1-100-example-survey.md"
    before = doc_path.read_text(encoding="utf-8")

    _write_hugo_config(rig_dir)
    config = load_config(rig_dir / ".ctx")
    build_index(config)

    assert doc_path.read_text(encoding="utf-8") == before
    assert "description:" in before  # Hugo still reads this key untouched
    assert "summary:" not in before  # ctx's key was never written to disk


def test_hugo_id_derived_from_path_stem_when_absent(rig_dir):
    _write_hugo_config(rig_dir)
    config = load_config(rig_dir / ".ctx")
    build_index(config)

    conn = sqlite3.connect(config.index_db_path)
    ids = {r[0] for r in conn.execute("SELECT id FROM documents")}
    conn.close()

    assert "1-100-example-survey" in ids


def test_hugo_aliases_never_becomes_terms(rig_dir):
    """The plan's decision, verbatim: `aliases:` is a Hugo URL redirect, not
    search vocabulary, and must never be read as `terms:`."""
    _write_hugo_config(rig_dir)
    config = load_config(rig_dir / ".ctx")
    build_index(config)

    conn = sqlite3.connect(config.index_db_path)
    row = conn.execute(
        "SELECT terms FROM documents WHERE id = '1-100-example-survey'"
    ).fetchone()
    conn.close()

    assert row == ("",)


def test_hugo_terms_key_is_read_when_present(rig_dir):
    """`terms:` is a real ctx key that may be hand-added to a Hugo doc; it
    must be indexed like any other corpus's terms, not silently dropped."""
    doc = rig_dir / "docs" / "hugo" / "1-100-example-survey.md"
    text = doc.read_text(encoding="utf-8")
    text = text.replace("date: 2026-01-01", 'date: 2026-01-01\nterms: ["widget"]')
    doc.write_text(text)

    _write_hugo_config(rig_dir)
    config = load_config(rig_dir / ".ctx")
    build_index(config)

    hits = search(config.index_db_path, "widget", corpus="docs")
    assert any(h.id == "1-100-example-survey" for h in hits)


def test_hugo_no_frontmatter_file_flagged_by_lint_not_invented(rig_dir):
    _write_hugo_config(rig_dir)
    config = load_config(rig_dir / ".ctx")
    report = lint(config)

    assert any(
        "no-frontmatter.md" in e and "no parseable frontmatter" in e for e in report.errors
    )


def test_hugo_doc_with_description_passes_common_lint(rig_dir):
    """A Hugo doc has no `summary:` key on disk — lint must check the mapped
    view (summary derived from description), not the raw file, or every one
    of research's 248 Hugo docs would fail 'missing summary'."""
    _write_hugo_config(rig_dir)
    config = load_config(rig_dir / ".ctx")
    report = lint(config)

    assert not any("1-100-example-survey" in e and "summary" in e for e in report.errors)


def test_generic_map_key_works_without_type_hugo(rig_dir):
    """The bead asks for a generic `map:` with `type: hugo` only as a preset
    — any corpus type may set `map:` explicitly."""
    _write_hugo_config(
        rig_dir,
        extra=(
            "  relabeled:\n"
            "    type: shared\n"
            "    path: docs/hugo\n"
            "    map:\n"
            "      summary: description\n"
            "      tags: keywords\n"
            "    derive_id: true\n"
        ),
    )
    config = load_config(rig_dir / ".ctx")
    relabeled = next(c for c in config.corpora if c.name == "relabeled")
    assert relabeled.field_map == {"summary": "description", "tags": "keywords"}
    assert relabeled.derive_id is True


def test_rm_index_and_reindex_reproduces_hugo_corpus_byte_identical(rig_dir):
    _write_hugo_config(rig_dir)
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    before = search(config.index_db_path, "example survey", corpus="docs")

    config.index_db_path.unlink()
    build_index(config)
    after = search(config.index_db_path, "example survey", corpus="docs")

    assert [h.__dict__ for h in before] == [h.__dict__ for h in after]
