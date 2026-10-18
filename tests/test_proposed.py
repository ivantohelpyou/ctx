"""Proposed decisions are current, but never rescue a miss as a hit (hq-7zfw).

The shapes are the ones the 2026-10-18 measurement turned on: a proposed
position that out-matches an accepted one ("custom tailor swatches fitting"
returned the accepted CUSTOMERS position over the proposed tailor one), and a
query only proposed positions match, where half the rescued answers were noise.
"""

import sqlite3

from click.testing import CliRunner

from ctx_cli.cli import main
from ctx_cli.corpus import load_config
from ctx_cli.indexer import build_index
from ctx_cli.mapview import run_query


def _decision(rig_dir, doc_id, title, summary, status, supersedes=()):
    sup = ", ".join(supersedes)
    (rig_dir / "docs" / "decisions" / f"{doc_id}.md").write_text(
        f'---\nid: {doc_id}\ntitle: "{title}"\nsummary: "{summary}"\nstatus: {status}\n'
        f"date: 2026-10-01\nsupersedes: [{sup}]\nsuperseded-by: []\n---\n"
    )


def _tailor_rig(rig_dir):
    _decision(rig_dir, "pos-92", "CUSTOMERS: organisations SaaS underserves",
              "Customisation costs more than the licence; nothing fits.", "accepted")
    _decision(rig_dir, "pos-122", "A custom tailor: swatches, a fitting, then I make yours",
              "One-on-one, like a tailor; the room is the runway.", "proposed")
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    return config


def _get(rig_dir, *args):
    result = CliRunner().invoke(main, ["get", *args, "--dir", str(rig_dir)])
    assert result.exit_code == 0, result.output
    return result.output.splitlines()


def _misses(config):
    conn = sqlite3.connect(config.ctx_db_path)
    try:
        return [r[0] for r in conn.execute("SELECT query FROM misses")]
    finally:
        conn.close()


def test_a_proposed_decision_outranks_an_accepted_one_it_out_matches(rig_dir):
    _tailor_rig(rig_dir)
    lines = _get(rig_dir, "custom tailor swatches fitting")
    assert lines[0].startswith("pos-122 — ")
    assert not any(line.startswith("weak:") for line in lines)


def test_only_proposed_matches_come_back_as_marked_leads_and_a_miss(rig_dir):
    config = _tailor_rig(rig_dir)
    lines = _get(rig_dir, "tailor runway")
    assert lines == [line for line in lines if line.startswith("weak: ")]
    assert lines[0].startswith("weak: pos-122 — ") and lines[0].endswith(" (proposed)")
    assert "tailor runway" in _misses(config)


def test_history_returns_proposed_matches_as_hits(rig_dir):
    _tailor_rig(rig_dir)
    lines = _get(rig_dir, "tailor runway", "--history")
    assert lines[0].startswith("pos-122 — ")


def test_loose_returns_proposed_matches_as_hits(rig_dir):
    _tailor_rig(rig_dir)
    assert _get(rig_dir, "tailor runway", "--loose")[0].startswith("pos-122 — ")


def test_a_superseded_proposed_decision_stays_hidden(rig_dir):
    _decision(rig_dir, "pos-50", "Tailor runway, first draft", "the old tailor runway", "proposed")
    _decision(rig_dir, "pos-122", "Tailor runway", "the tailor runway", "proposed",
              supersedes=["pos-50"])
    text = "\n".join(_get(rig_dir, "tailor runway"))
    assert "pos-122" in text and "pos-50" not in text


def test_a_one_word_proposed_lead_is_marked_too(rig_dir):
    _tailor_rig(rig_dir)
    lines = _get(rig_dir, "tailor zebra")  # below the floor everywhere
    assert lines == ["weak: pos-122 — A custom tailor: swatches, a fitting, then I make yours — "
                     "docs/decisions/pos-122.md#a-custom-tailor-swatches-a-fitting-then-i-make-yours"
                     " (proposed)"]


def test_the_map_gives_the_same_answer_as_ctx_get(rig_dir):
    config = _tailor_rig(rig_dir)
    for q in ("custom tailor swatches fitting", "tailor runway"):
        res = run_query(config, q, sweep=False)
        assert res["text"].splitlines() == _get(rig_dir, q)
    assert run_query(config, "tailor runway", sweep=False)["miss"]


def test_a_proposed_decision_below_another_hit_is_a_marked_dropped_line(rig_dir):
    _tailor_rig(rig_dir)
    _decision(rig_dir, "pos-93", "Customisation and licence costs", "per seat", "accepted")
    _decision(rig_dir, "pos-170", "Teach it: the workshop",
              "A workshop on customisation; the licence is mentioned once.", "proposed")
    build_index(load_config(rig_dir / ".ctx"))
    lines = _get(rig_dir, "customisation licence")
    assert lines[0].startswith("pos-93 — ")  # accepted, ranked first
    assert "dropped: pos-170 — Teach it: the workshop (proposed)" in lines
    assert not any(line.startswith("pos-170 — ") for line in lines)
