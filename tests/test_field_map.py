"""Config-load validation of `map:` (re-i1n review, findings 1, 2, 3, 4).

The reviewer reproduced three ways an unvalidated `map:` misbehaves:
`map: {terms: aliases}` leaks Hugo redirect paths into search terms, a typo'd
target (`summry:`) silently produces empty summaries for every document, and
a mapped source clobbers a hand-authored value instead of losing to it.
"""

from __future__ import annotations

import sqlite3

import pytest

from ctx_cli.corpus import load_config
from ctx_cli.indexer import build_index


def _write_config(rig_dir, corpus_yaml: str) -> None:
    (rig_dir / ".ctx" / "config.yaml").write_text("corpora:\n" + corpus_yaml)


def test_unknown_map_target_is_a_config_error(rig_dir):
    """Finding 1a/4: a typo'd target (`summry:`) must fail loudly at load
    time, not silently produce an empty `summary:` for every document."""
    _write_config(
        rig_dir,
        "  docs:\n"
        "    type: hugo\n"
        "    path: docs/hugo\n"
        "    map:\n"
        "      summry: description\n",
    )
    with pytest.raises(ValueError, match="summry"):
        load_config(rig_dir / ".ctx")


def test_aliases_forbidden_as_source_for_terms(rig_dir):
    """Finding 1b, reproduced exactly: `map: {terms: aliases}` must not be
    allowed to leak Hugo's URL-redirect list into search terms."""
    _write_config(
        rig_dir,
        "  docs:\n"
        "    type: hugo\n"
        "    path: docs/hugo\n"
        "    map:\n"
        "      terms: aliases\n",
    )
    with pytest.raises(ValueError, match="aliases"):
        load_config(rig_dir / ".ctx")


def test_aliases_forbidden_as_source_for_tags(rig_dir):
    _write_config(
        rig_dir,
        "  docs:\n"
        "    type: hugo\n"
        "    path: docs/hugo\n"
        "    map:\n"
        "      tags: aliases\n",
    )
    with pytest.raises(ValueError, match="aliases"):
        load_config(rig_dir / ".ctx")


def test_aliases_still_allowed_as_a_source_for_an_unrelated_target(rig_dir):
    """The prohibition is specifically tags/terms (the search-vocabulary
    fields), not a blanket ban on ever reading `aliases:` for anything."""
    _write_config(
        rig_dir,
        "  docs:\n"
        "    type: hugo\n"
        "    path: docs/hugo\n"
        "    map:\n"
        "      covers: aliases\n",
    )
    config = load_config(rig_dir / ".ctx")
    assert config.corpora[0].field_map["covers"] == "aliases"


def test_duplicate_map_key_is_a_config_error(rig_dir):
    """Finding 1c: the same target key twice under `map:` — most likely a
    copy-paste that meant two different targets — must fail loudly rather
    than silently keeping only whichever line came last."""
    _write_config(
        rig_dir,
        "  docs:\n"
        "    type: hugo\n"
        "    path: docs/hugo\n"
        "    map:\n"
        "      summary: description\n"
        "      summary: title\n",
    )
    with pytest.raises(ValueError, match="duplicate key"):
        load_config(rig_dir / ".ctx")


def test_hand_authored_summary_wins_over_mapped_description(rig_dir):
    """Finding 2: an explicit `summary:` in the file is the product (never
    auto-generated, per the memo) and must never be clobbered by the mapped
    `description:` — the map only fills what the author left absent."""
    doc = rig_dir / "docs" / "hugo" / "1-100-example-survey.md"
    text = doc.read_text(encoding="utf-8")
    text = text.replace(
        'description: "One-line Hugo description used as the ctx summary."',
        'description: "One-line Hugo description used as the ctx summary."\n'
        'summary: "Hand-authored summary that must win."',
    )
    doc.write_text(text)

    _write_config(rig_dir, "  docs:\n    type: hugo\n    path: docs/hugo\n")
    config = load_config(rig_dir / ".ctx")
    build_index(config)

    conn = sqlite3.connect(config.index_db_path)
    row = conn.execute(
        "SELECT summary FROM documents WHERE id = '1-100-example-survey'"
    ).fetchone()
    conn.close()
    assert row == ("Hand-authored summary that must win.",)


def test_keywords_as_comma_string_matches_keywords_as_list(rig_dir):
    """Finding 3: `keywords: "alpha, beta"` and `keywords: ["alpha", "beta"]`
    must index to identical tags."""
    doc = rig_dir / "docs" / "hugo" / "1-100-example-survey.md"
    text = doc.read_text(encoding="utf-8")
    list_form_text = text  # keywords: ["alpha", "beta"], as shipped in the fixture
    comma_text = text.replace('keywords: ["alpha", "beta"]', 'keywords: "alpha, beta"')
    assert comma_text != list_form_text

    _write_config(rig_dir, "  docs:\n    type: hugo\n    path: docs/hugo\n")
    config = load_config(rig_dir / ".ctx")

    doc.write_text(list_form_text)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    list_tags = conn.execute(
        "SELECT tags FROM documents WHERE id = '1-100-example-survey'"
    ).fetchone()
    conn.close()

    doc.write_text(comma_text)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    comma_tags = conn.execute(
        "SELECT tags FROM documents WHERE id = '1-100-example-survey'"
    ).fetchone()
    conn.close()

    assert list_tags == comma_tags == ("alpha beta",)
