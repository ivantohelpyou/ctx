import sqlite3
import stat
import time

from click.testing import CliRunner

from ctx_cli.cli import main
from ctx_cli.corpus import load_config
from ctx_cli.indexer import build_index
from ctx_cli.query import DEFAULT_BUDGET, DEFAULT_LIMIT, apply_budget, render, search


def test_search_finds_by_terms(config):
    build_index(config)
    hits = search(config.index_db_path, "bm25")
    assert any(h.id == "exp-001" for h in hits)


def test_search_ranks_title_above_summary_only_match(config):
    build_index(config)
    # "budget" is in exp-002's title and terms; should rank at or near top.
    hits = search(config.index_db_path, "budget")
    assert hits[0].id == "exp-002"


def test_history_flag_includes_superseded_decision(config):
    build_index(config)
    default_hits = search(config.index_db_path, "database", corpus="decisions")
    assert all(h.id != "adr-001" for h in default_hits)  # superseded, hidden by default

    history_hits = search(config.index_db_path, "database", corpus="decisions", history=True)
    assert any(h.id == "adr-001" for h in history_hits)


def test_history_flag_annotates_superseded_hit_with_chain_head(config):
    """The walker's job: --history must not just include a superseded doc, it
    must say what replaced it — the head of its chain (adr-002 is the fixture's
    only successor, so head == direct successor here; test_history.py covers
    a longer chain where head != direct successor)."""
    build_index(config)
    history_hits = search(config.index_db_path, "database", corpus="decisions", history=True)
    adr1 = next(h for h in history_hits if h.id == "adr-001")
    assert adr1.superseded_by == [("adr-002", "Two database files, built with SEA")]
    assert adr1.cycle is False
    adr2 = next(h for h in history_hits if h.id == "adr-002")
    assert adr2.superseded_by == []  # adr-002 is current, nothing replaced it

    output = render(*apply_budget(history_hits, DEFAULT_BUDGET))
    assert "  superseded-by: adr-002 — Two database files, built with SEA" in output


def test_without_history_flag_no_annotation_and_no_walker_cost_for_accepted_hit(config):
    build_index(config)
    default_hits = search(config.index_db_path, "database", corpus="decisions")
    assert all(h.superseded_by == [] for h in default_hits)


def test_default_get_excludes_superseded_doc_with_no_status(config):
    """Acceptance 2 broke on exactly this shape (round 2 review): a decisions
    doc that omits `status:` entirely indexes with status NULL, which the
    status-only filter used to let straight through even though the graph
    already says something replaced it. The graph is checked regardless of
    status now."""
    decisions = config.rig_root / "docs" / "decisions"
    # adr-001 is superseded by adr-002 in the fixture; add a document with NO
    # status at all that adr-002 also supersedes, so the graph — not the
    # status column — is the only thing that could exclude it.
    (decisions / "adr-000-no-status.md").write_text(
        "---\nid: adr-000\ntitle: No status at all, but superseded\n"
        "summary: omits status entirely.\ndate: 2026-08-30\n"
        "supersedes: []\nsuperseded-by: [adr-002]\n---\n"
    )
    old_text = (decisions / "adr-002-two-databases-sea.md").read_text()
    (decisions / "adr-002-two-databases-sea.md").write_text(
        old_text.replace("supersedes: [adr-001]", "supersedes: [adr-001, adr-000]")
    )
    build_index(config)

    default_hits = search(config.index_db_path, "status", corpus="decisions")
    assert all(h.id != "adr-000" for h in default_hits)

    history_hits = search(config.index_db_path, "status", corpus="decisions", history=True)
    adr0 = next(h for h in history_hits if h.id == "adr-000")
    assert adr0.superseded_by == [("adr-002", "Two database files, built with SEA")]


def test_history_flag_lists_every_head_on_a_fork(config):
    decisions = config.rig_root / "docs" / "decisions"
    (decisions / "split-0.md").write_text(
        "---\nid: split-0\ntitle: Split point\nsummary: forks into two.\n"
        "status: superseded\ndate: 2026-01-01\n"
        "supersedes: []\nsuperseded-by: [split-x, split-y]\n---\n"
    )
    (decisions / "split-x.md").write_text(
        "---\nid: split-x\ntitle: One branch\nsummary: one successor.\n"
        "status: accepted\ndate: 2026-02-01\n"
        "supersedes: [split-0]\nsuperseded-by: []\n---\n"
    )
    (decisions / "split-y.md").write_text(
        "---\nid: split-y\ntitle: The other branch\nsummary: the other successor.\n"
        "status: accepted\ndate: 2026-02-02\n"
        "supersedes: [split-0]\nsuperseded-by: []\n---\n"
    )
    build_index(config)

    history_hits = search(config.index_db_path, "forks into two", corpus="decisions", history=True)
    split0 = next(h for h in history_hits if h.id == "split-0")
    assert split0.superseded_by == [
        ("split-x", "One branch"),
        ("split-y", "The other branch"),
    ]
    output = render(*apply_budget(history_hits, DEFAULT_BUDGET))
    assert "  superseded-by: split-x — One branch; split-y — The other branch" in output


def test_history_flag_annotates_a_cycle_and_never_lists_heads_alongside_it(config):
    decisions = config.rig_root / "docs" / "decisions"
    (decisions / "loop-a.md").write_text(
        "---\nid: loop-a\ntitle: Loop A\nsummary: part of an injected cycle.\n"
        "status: superseded\ndate: 2026-01-01\n"
        "supersedes: []\nsuperseded-by: [loop-b]\n---\n"
    )
    (decisions / "loop-b.md").write_text(
        "---\nid: loop-b\ntitle: Loop B\nsummary: the other half of the cycle.\n"
        "status: superseded\ndate: 2026-01-02\n"
        "supersedes: [loop-a]\nsuperseded-by: []\n---\n"
    )
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    conn.execute("INSERT INTO supersessions (from_id, to_id) VALUES ('loop-a', 'loop-b')")
    conn.commit()
    conn.close()

    history_hits = search(config.index_db_path, "injected cycle", corpus="decisions", history=True)
    loop_a = next(h for h in history_hits if h.id == "loop-a")
    assert loop_a.cycle is True
    output = render(*apply_budget(history_hits, DEFAULT_BUDGET))
    assert "  superseded-by: (cycle)" in output


def test_cli_get_history_warns_once_on_stderr_for_a_cycle(rig_dir):
    decisions = rig_dir / "docs" / "decisions"
    (decisions / "loop-a.md").write_text(
        "---\nid: loop-a\ntitle: Loop A\nsummary: part of an injected cycle.\n"
        "status: superseded\ndate: 2026-01-01\n"
        "supersedes: []\nsuperseded-by: [loop-b]\n---\n"
    )
    (decisions / "loop-b.md").write_text(
        "---\nid: loop-b\ntitle: Loop B\nsummary: the other half of the cycle.\n"
        "status: superseded\ndate: 2026-01-02\n"
        "supersedes: [loop-a]\nsuperseded-by: []\n---\n"
    )
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    conn.execute("INSERT INTO supersessions (from_id, to_id) VALUES ('loop-a', 'loop-b')")
    conn.commit()
    conn.close()

    result = CliRunner().invoke(
        main, ["get", "injected cycle", "--corpus", "decisions", "--history", "--dir", str(rig_dir)]
    )
    assert result.exit_code == 0, result.output
    assert result.output.count("index is corrupt") == 1


def test_byte_identical_history_output_after_rm_and_reindex(config):
    build_index(config)
    hits1 = search(config.index_db_path, "database", corpus="decisions", history=True)
    out1 = render(*apply_budget(hits1, DEFAULT_BUDGET))

    config.index_db_path.unlink()
    build_index(config)
    hits2 = search(config.index_db_path, "database", corpus="decisions", history=True)
    out2 = render(*apply_budget(hits2, DEFAULT_BUDGET))

    assert out1 == out2
    assert out1.encode("utf-8") == out2.encode("utf-8")


def test_corpus_filter(config):
    build_index(config)
    hits = search(config.index_db_path, "worktree", corpus="shared")
    assert all(h.corpus == "shared" for h in hits)
    assert any(h.id == "shared-001" for h in hits)


def test_apply_budget_always_keeps_first_hit_even_if_oversized(config):
    build_index(config)
    hits = search(config.index_db_path, "database", corpus="decisions", history=True)
    kept, dropped = apply_budget(hits, budget=1)
    assert len(kept) == 1


def test_dropped_lines_rendered(config):
    build_index(config)
    hits = search(config.index_db_path, "database", corpus="decisions", history=True)
    kept, dropped = apply_budget(hits[:1], budget=10000)
    dropped_manual = hits[1:]
    output = render(kept, dropped_manual)
    for h in dropped_manual:
        assert f"dropped: {h.id} — {h.title}" in output


def test_output_contract_format(config):
    build_index(config)
    hits = search(config.index_db_path, "fts5")
    kept, dropped = apply_budget(hits, DEFAULT_BUDGET)
    output = render(kept, dropped)
    line = output.splitlines()[0]
    parts = line.split(" — ")
    assert len(parts) == 4  # id, title, path#slug, summary
    assert "#" in parts[2]


def test_byte_identical_output_after_rm_and_reindex(config):
    build_index(config)
    hits1 = search(config.index_db_path, "budget retrieval")
    kept1, dropped1 = apply_budget(hits1, DEFAULT_BUDGET)
    out1 = render(kept1, dropped1)

    config.index_db_path.unlink()
    build_index(config)
    hits2 = search(config.index_db_path, "budget retrieval")
    kept2, dropped2 = apply_budget(hits2, DEFAULT_BUDGET)
    out2 = render(kept2, dropped2)

    assert out1 == out2
    assert out1.encode("utf-8") == out2.encode("utf-8")


def test_query_latency_under_100ms(config):
    build_index(config)
    start = time.perf_counter()
    hits = search(config.index_db_path, "database decision architecture")
    apply_budget(hits, DEFAULT_BUDGET)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 100, f"ctx get took {elapsed_ms:.1f}ms, budget is 100ms"


def _add_widget_docs(rig_dir, count):
    shared_dir = rig_dir / "docs" / "shared"
    for i in range(count):
        (shared_dir / f"widget-{i:02d}.md").write_text(
            f"---\nid: widget-{i:02d}\ntitle: Widget config {i}\n"
            f"summary: widget configuration note {i}.\n---\n"
        )


def test_apply_budget_hard_caps_at_default_limit_of_5(rig_dir):
    """Finding 7: on a corpus with many small hits and a generous budget,
    ctx get must not return dozens of entries — default cap is 5."""
    _add_widget_docs(rig_dir, 8)
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    hits = search(config.index_db_path, "widget config")
    assert len(hits) == 8
    kept, dropped = apply_budget(hits, budget=100_000)
    assert len(kept) == DEFAULT_LIMIT == 5
    assert len(dropped) == 3


def test_apply_budget_limit_override(rig_dir):
    _add_widget_docs(rig_dir, 8)
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    hits = search(config.index_db_path, "widget config")
    kept, dropped = apply_budget(hits, budget=100_000, limit=8)
    assert len(kept) == 8
    assert dropped == []


def test_cli_get_respects_limit_flag(rig_dir):
    _add_widget_docs(rig_dir, 8)
    runner = CliRunner()
    result = runner.invoke(main, ["get", "widget config", "--dir", str(rig_dir), "--limit", "3"])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    kept_lines = [line for line in lines if not line.startswith("dropped:")]
    dropped_lines = [line for line in lines if line.startswith("dropped:")]
    assert len(kept_lines) == 3
    assert len(dropped_lines) == 5


def test_readonly_ctx_dir_get_degrades_instead_of_crashing(rig_dir):
    """Finding 6: `ctx get` only ever reads index.db and must not require a
    writable `.ctx/`; a failed query-log write degrades to a stderr warning,
    never a traceback, and the query itself still succeeds."""
    config = load_config(rig_dir / ".ctx")
    build_index(config)  # index.db exists before we lock the directory down

    ctx_dir = rig_dir / ".ctx"
    mode = ctx_dir.stat().st_mode
    ctx_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)  # read+execute, no write
    try:
        runner = CliRunner()
        result = runner.invoke(main, ["get", "fts5", "--dir", str(rig_dir)])
        assert result.exit_code == 0, result.output
        assert "exp-001" in result.output
        assert "Traceback" not in result.output
    finally:
        ctx_dir.chmod(mode)  # restore so pytest can clean up tmp_path
