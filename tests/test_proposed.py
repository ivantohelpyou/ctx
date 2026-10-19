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
from ctx_cli.query import Hit, apply_budget, search


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


# --- review round 1 (hq-7zfw): who is marked, and where dropped lines go ------


def _shared(rig_dir, doc_id, title, summary, status):
    (rig_dir / "docs" / "shared" / f"{doc_id}.md").write_text(
        f'---\nid: {doc_id}\ntitle: "{title}"\nsummary: "{summary}"\nstatus: {status}\n---\n'
    )


def _gizmo_rig(rig_dir):
    """Accepted and proposed decisions plus a shared doc whose own status is
    'proposed' (not a decision), all matching 'gizmo widget'."""
    _decision(rig_dir, "pos-1", "Gizmo widget gizmo widget", "gizmo widget", "proposed")
    _decision(rig_dir, "pos-2", "Gizmo widget", "gizmo widget plan", "proposed")
    _decision(rig_dir, "pos-3", "Rules", "gizmo widget, the rules", "accepted")
    _decision(rig_dir, "pos-4", "Gizmo widget notes", "notes", "proposed")
    _decision(rig_dir, "pos-5", "Appendix", "gizmo widget trivia", "accepted")
    _shared(rig_dir, "sh-1", "Gizmo widget lesson", "gizmo widget", "proposed")
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    return config


def _ranked(config, query, **kw):
    dec = [c.name for c in config.corpora if c.type == "decisions"]
    return [h.id for h in search(config.index_db_path, query, decisions_corpora=dec, **kw)]


def test_leading_proposed_hits_are_kept_and_later_ones_are_marked_drops(rig_dir):
    config = _gizmo_rig(rig_dir)
    ranked = _ranked(config, "gizmo widget")
    lines = _get(rig_dir, "gizmo widget", "--limit", "10")
    kept = [line.split(" — ")[0] for line in lines if not line.startswith("dropped:")]
    first_other = next(i for i, d in enumerate(ranked) if not d.startswith("pos-")
                       or d in ("pos-3", "pos-5"))
    assert kept[: first_other + 1] == ranked[: first_other + 1]
    for d in ranked[first_other + 1 :]:
        if d in ("pos-1", "pos-2", "pos-4"):
            assert any(line.startswith(f"dropped: {d} — ") and line.endswith(" (proposed)")
                       for line in lines)
            assert d not in kept


def test_a_proposed_status_outside_a_decisions_corpus_is_never_marked(rig_dir):
    _gizmo_rig(rig_dir)
    for args in (["gizmo widget", "--limit", "1"], ["lesson zzz"]):  # a drop, a weak lead
        text = "\n".join(_get(rig_dir, *args))
        assert "sh-1" in text
        assert not any("sh-1" in line and "(proposed)" in line for line in text.splitlines())


def test_history_and_loose_mark_nothing(rig_dir):
    _gizmo_rig(rig_dir)
    for extra in (["--history"], ["--loose"]):
        for q in ("gizmo widget", "gizmo zzz"):
            assert "(proposed)" not in "\n".join(_get(rig_dir, q, "--limit", "1", *extra))


def test_dropped_lines_keep_rank_order(rig_dir):
    config = _gizmo_rig(rig_dir)
    ranked = _ranked(config, "gizmo widget")
    lines = _get(rig_dir, "gizmo widget", "--limit", "1")
    dropped = [line.removeprefix("dropped: ").split(" — ")[0]
               for line in lines if line.startswith("dropped: ") and "more" not in line]
    assert dropped == [d for d in ranked if d in dropped]


def test_a_lead_is_dropped_where_it_ranks_and_takes_no_slot():
    def hit(i, lead=False):
        return Hit(id=f"d-{i}", corpus="decisions", path=f"d-{i}.md", title=f"T{i}",
                   summary="s", status="proposed" if lead else "accepted", lead=lead)

    hits = [hit(1), hit(2, lead=True), hit(3), hit(4)]
    kept, dropped = apply_budget(hits, budget=10_000, limit=2)
    assert [h.id for h in kept] == ["d-1", "d-3"]  # the lead used no slot
    assert [h.id for h in dropped] == ["d-2", "d-4"]  # rank order, lead first


def test_corpus_filter_with_a_proposed_only_result(rig_dir):
    _tailor_rig(rig_dir)
    lines = _get(rig_dir, "tailor runway", "--corpus", "decisions")
    assert lines and all(line.startswith("weak: ") for line in lines)
    assert _get(rig_dir, "tailor runway", "--corpus", "shared") == []


def test_the_map_carries_demoted_docs_like_ctx_get(rig_dir):
    config = _gizmo_rig(rig_dir)
    for q in ("gizmo widget", "gizmo zzz", "gizmo widget plan"):
        res = run_query(config, q, limit=1, sweep=False)
        assert res["text"].splitlines() == _get(rig_dir, q, "--limit", "1")
        dropped_ids = [h["id"] for h in res["dropped"]]
        marked = [c["id"] for c in res["chunks"] if c["kind"] == "dropped"
                  and c["text"].rstrip().endswith("(proposed)")]
        assert set(marked) <= set(dropped_ids)
