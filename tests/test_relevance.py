"""Relevance floor (hq-mb81) and the dropped-list cap (hq-x0ra).

The noise cases are the two real queries from the 2026-10-08 measurement
(~/gt/mayor/portfolio-reckoning/technical/ctx-measurement.md) that topped with
an unrelated strategy position because one stemmed word ("heads" -> "head")
matched; the good cases are shapes that measurement judged right.
"""

import sqlite3

from click.testing import CliRunner

from ctx_cli.cli import main
from ctx_cli.corpus import load_config
from ctx_cli.indexer import build_index
from ctx_cli.query import (
    DROPPED_SHOWN,
    WEAK_SHOWN,
    apply_budget,
    min_terms_required,
    query_terms,
    render,
    search,
)


def _write(rig_dir, name, doc_id, title, summary):
    (rig_dir / "docs" / "shared" / f"{name}.md").write_text(
        f'---\nid: {doc_id}\ntitle: "{title}"\nsummary: "{summary}"\n---\n'
    )


def _noise_rig(rig_dir):
    """A corpus with the noise doc and the right answers for the good cases,
    and nothing at all about hotel occupancy or backup volumes."""
    _write(
        rig_dir,
        "pos-99",
        "pos-99",
        "DEFENSIBILITY: several disciplines in one head",
        "What cannot be copied is the combination held in one head.",
    )
    _write(
        rig_dir,
        "nsa-plan",
        "nsa-plan",
        "Surveys tab and guest-code cross-reference",
        "How a guest code converts to a member record in the chapter data.",
    )
    _write(
        rig_dir,
        "purr-plain",
        "purr-plain",
        "purr, in plain language",
        "What purr does for a room, explained in plain language.",
    )
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    ids = {r[0] for r in conn.execute("SELECT id FROM documents")}
    conn.close()
    assert {"pos-99", "nsa-plan", "purr-plain"} <= ids  # else the floor tests prove nothing
    return config


def _misses(config):
    conn = sqlite3.connect(config.ctx_db_path)
    try:
        return [r[0] for r in conn.execute("SELECT query FROM misses")]
    finally:
        conn.close()


# --- the floor -----------------------------------------------------------


def test_one_word_coincidence_is_not_a_result(rig_dir):
    config = _noise_rig(rig_dir)
    for q in ("heads in beds hib page", "HEADS volume backup NUC laptop"):
        assert search(config.index_db_path, q) == [], q


def test_loose_restores_the_old_or_behaviour(rig_dir):
    config = _noise_rig(rig_dir)
    hits = search(config.index_db_path, "heads in beds hib page", loose=True)
    # the old behaviour: one-word matches come back (the fixture's worktrees
    # lesson also says HEAD), pos-99 among them
    assert "pos-99" in [h.id for h in hits]


def test_below_floor_query_prints_only_weak_leads_and_logs_a_miss(rig_dir):
    config = _noise_rig(rig_dir)
    result = CliRunner().invoke(main, ["get", "heads in beds hib page", "--dir", str(rig_dir)])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines and all(line.startswith("weak: ") for line in lines)
    assert any(line.startswith("weak: pos-99 — ") for line in lines)
    assert "heads in beds hib page" in _misses(config)


def test_good_cases_keep_their_right_top_hit(rig_dir):
    config = _noise_rig(rig_dir)
    assert search(config.index_db_path, "guest code conversion")[0].id == "nsa-plan"
    assert search(config.index_db_path, "plain language")[0].id == "purr-plain"


def test_single_term_query_needs_only_that_term(config):
    build_index(config)
    assert search(config.index_db_path, "bm25")[0].id == "exp-001"


def test_stopwords_neither_match_nor_count(rig_dir):
    config = _noise_rig(rig_dir)
    # "in" and "the" are in every summary-ish sentence; they must not lift a
    # doc that matches only one content word over the floor.
    assert search(config.index_db_path, "the head in the room") == []


# --- query_terms / min_terms_required ------------------------------------


def test_query_terms_drops_stopwords_and_duplicates_in_order():
    terms = query_terms("how to ingest a new survey into the Map")
    assert terms == ["ingest", "new", "survey", "Map"]
    assert query_terms("survey Survey survey.") == ["survey"]


def test_query_terms_never_empties_an_all_stopword_query():
    assert query_terms("in the") == ["in", "the"]


def test_min_terms_required():
    assert [min_terms_required(n) for n in range(0, 6)] == [0, 1, 2, 2, 2, 2]


# --- the dropped cap -----------------------------------------------------


def _add_widgets(rig_dir, count):
    for i in range(count):
        name = f"widget-{i:02d}"
        _write(rig_dir, name, name, f"Widget config {i}", f"widget configuration note {i}.")


def test_dropped_list_is_capped_with_a_count_line(rig_dir):
    _add_widgets(rig_dir, 20)
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    hits = search(config.index_db_path, "widget config")
    kept, dropped = apply_budget(hits, budget=100_000, limit=3)
    assert len(dropped) == 17
    lines = render(kept, dropped).splitlines()
    dropped_lines = [line for line in lines if line.startswith("dropped:")]
    assert len(dropped_lines) == DROPPED_SHOWN + 1
    assert dropped_lines[-1] == f"dropped: +{17 - DROPPED_SHOWN} more"
    # the ones shown are the highest-ranked of the cut, in rank order
    shown = [f"dropped: {h.id} — {h.title}" for h in dropped[:DROPPED_SHOWN]]
    assert dropped_lines[:DROPPED_SHOWN] == shown


def test_no_count_line_when_nothing_beyond_the_cap(rig_dir):
    _add_widgets(rig_dir, 3 + DROPPED_SHOWN)
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    hits = search(config.index_db_path, "widget config")
    kept, dropped = apply_budget(hits, budget=100_000, limit=3)
    assert len(dropped) == DROPPED_SHOWN
    assert "more" not in render(kept, dropped)


def test_capped_output_stays_near_budget_on_a_broad_query(rig_dir):
    """hq-x0ra: a 1,500-token budget printed 2.5-4.3k tokens on sea because
    every cut hit got a line. With the cap, overhead is bounded by
    DROPPED_SHOWN + 1 short lines whatever the corpus size."""
    _add_widgets(rig_dir, 200)
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    budget = 100
    out = render(*apply_budget(search(config.index_db_path, "widget config"), budget=budget))
    dropped_chars = sum(len(line) + 1 for line in out.splitlines() if line.startswith("dropped:"))
    assert dropped_chars / 4 < 60  # bounded, not proportional to the 195 cut hits


# --- review round 1 (hq-mb81): term identity is the tokenizer's, not spelling


def test_inflections_of_one_word_are_one_term(rig_dir):
    config = _noise_rig(rig_dir)
    assert query_terms("head heads") == ["head"]
    assert query_terms("backup backups Backups.") == ["backup"]
    # one term, so "head heads" is exactly the one-word query "head" ...
    ids = [h.id for h in search(config.index_db_path, "head")]
    assert [h.id for h in search(config.index_db_path, "head heads")] == ids
    # ... and a repeated inflection can no longer count twice toward the floor
    assert search(config.index_db_path, "heads head beds") == []


def test_words_the_tokenizer_drops_are_not_terms(rig_dir):
    _write(rig_dir, "a-1", "a-1", "Nightly rebuild", "the sync_survey_index job rebuilds nightly")
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    assert query_terms("nightly _ -- __") == ["nightly"]
    for q in ("nightly _", "nightly - _"):
        assert [h.id for h in search(config.index_db_path, q)] == ["a-1"], q


def test_underscored_identifier_is_one_multi_token_term(rig_dir):
    _write(rig_dir, "a-1", "a-1", "Nightly rebuild", "the sync_survey_index job rebuilds nightly")
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    assert query_terms("sync_survey_index nightly") == ["sync_survey_index", "nightly"]
    assert [h.id for h in search(config.index_db_path, "sync_survey_index nightly")] == ["a-1"]


def test_stemmed_stopword_is_still_a_stopword():
    # porter stems "this" -> "thi"; the stopword test must not depend on stems
    assert query_terms("this survey was archived") == ["survey", "archived"]


# --- weak leads when the floor leaves nothing (hq-mb81 follow-up) ------------


def _synonym_rig(rig_dir):
    """Good documents that each match one word of a synonym list."""
    _write(
        rig_dir, "e-192", "e-192", "Categories: a front page of picture boxes", "one box per work"
    )
    _write(rig_dir, "e-170", "e-170", "Facets and signage", "how a visitor narrows the works")
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    return config


def _get(rig_dir, *args):
    result = CliRunner().invoke(main, ["get", *args, "--dir", str(rig_dir)])
    assert result.exit_code == 0, result.output
    return result.output.splitlines()


def test_synonym_list_gets_weak_leads_not_silence(rig_dir):
    config = _synonym_rig(rig_dir)
    lines = _get(rig_dir, "facets categories collections")
    ids = {line.split(" — ")[0].removeprefix("weak: ") for line in lines}
    assert all(line.startswith("weak: ") for line in lines)
    assert {"e-192", "e-170"} <= ids
    # a lead names the file to open, and carries no summary
    assert "weak: e-170 — Facets and signage — " in "\n".join(lines)
    assert all("one box per work" not in line for line in lines)
    assert "facets categories collections" in _misses(config)


def test_no_weak_lines_when_a_real_result_exists(rig_dir):
    _synonym_rig(rig_dir)
    lines = _get(rig_dir, "facets signage")
    assert lines[0].startswith("e-170 — ")
    assert not any(line.startswith("weak:") for line in lines)


def test_weak_leads_are_capped(rig_dir):
    for i in range(10):
        name = f"w-{i:02d}"
        _write(rig_dir, name, name, f"Gizmo {i}", f"note {i}")
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    lines = _get(rig_dir, "gizmo sprocket")
    assert len(lines) == WEAK_SHOWN
    assert all(line.startswith("weak: ") for line in lines)


def test_loose_prints_no_weak_lines(rig_dir):
    _synonym_rig(rig_dir)
    lines = _get(rig_dir, "facets categories collections", "--loose")
    assert lines and not any(line.startswith("weak:") for line in lines)


def test_nothing_at_all_prints_nothing(rig_dir):
    _synonym_rig(rig_dir)
    assert _get(rig_dir, "zebra xylophone") == []


def test_weak_leads_respect_the_corpus_filter(rig_dir):
    _synonym_rig(rig_dir)
    assert _get(rig_dir, "facets categories collections", "--corpus", "explainers") == []


def test_weak_leads_honour_the_lifecycle_filter(rig_dir):
    """agent-1 review (PR #2): the weak search must apply the same
    supersession/status filter as the main one. In the fixture adr-001 is
    superseded by adr-002; both match only 'database' of 'database zebra', so
    the floor empties the result and both are weak candidates."""
    default = "\n".join(_get(rig_dir, "database zebra"))
    assert "weak: adr-002 — " in default
    assert "weak: adr-001 — " not in default  # superseded: hidden by default
    history = "\n".join(_get(rig_dir, "database zebra", "--history"))
    assert "weak: adr-001 — " in history
