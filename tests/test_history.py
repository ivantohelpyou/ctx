"""The supersession walker (`history.py`) and `ctx history <id>`."""

from __future__ import annotations

import sqlite3

from click.testing import CliRunner

from ctx_cli.cli import main
from ctx_cli.history import (
    direct_predecessors,
    direct_successors,
    head_info,
    head_of,
    history_view,
)
from ctx_cli.indexer import build_index


def _add_chain_docs(rig_dir):
    """A three-node chain, oldest to newest: chain-a -> chain-b -> chain-c
    (chain-c is the current head). Written directly into the copied fixture
    rig at test time — never into the committed fixture files, which several
    other tests depend on unchanged."""
    decisions = rig_dir / "docs" / "decisions"
    (decisions / "chain-a.md").write_text(
        "---\nid: chain-a\ntitle: Chain A (oldest)\n"
        "summary: the first decision in the chain.\n"
        "status: superseded\ndate: 2026-01-01\n"
        "supersedes: []\nsuperseded-by: [chain-b]\n---\n"
    )
    (decisions / "chain-b.md").write_text(
        "---\nid: chain-b\ntitle: Chain B (middle)\n"
        "summary: the middle decision in the chain.\n"
        "status: superseded\ndate: 2026-02-01\n"
        "supersedes: [chain-a]\nsuperseded-by: [chain-c]\n---\n"
    )
    (decisions / "chain-c.md").write_text(
        "---\nid: chain-c\ntitle: Chain C (current)\n"
        "summary: the current decision.\n"
        "status: accepted\ndate: 2026-03-01\n"
        "supersedes: [chain-b]\nsuperseded-by: []\n---\n"
    )


def _add_fanin_docs(rig_dir):
    """Many-to-one: sibling-a and sibling-b are both independently absorbed
    into hub, with no relationship to each other. Real data shape (a single
    box consolidated nine positions into one) — `ctx history sibling-a` must
    return {sibling-a, hub}, never sibling-b."""
    decisions = rig_dir / "docs" / "decisions"
    (decisions / "sibling-a.md").write_text(
        "---\nid: sibling-a\ntitle: Sibling A\nsummary: absorbed into hub.\n"
        "status: superseded\ndate: 2026-01-01\n"
        "supersedes: []\nsuperseded-by: [hub]\n---\n"
    )
    (decisions / "sibling-b.md").write_text(
        "---\nid: sibling-b\ntitle: Sibling B\nsummary: also absorbed into hub.\n"
        "status: superseded\ndate: 2026-01-02\n"
        "supersedes: []\nsuperseded-by: [hub]\n---\n"
    )
    (decisions / "hub.md").write_text(
        "---\nid: hub\ntitle: Hub (consolidator)\nsummary: consolidates both.\n"
        "status: accepted\ndate: 2026-02-01\n"
        "supersedes: [sibling-a, sibling-b]\nsuperseded-by: []\n---\n"
    )


def _add_split_docs(rig_dir):
    """One-to-many (a fork): split-x AND split-y both directly supersede
    split-0 — lint permits this (reciprocity is checked per entry, not
    capped at one predecessor). Round 2 review fixture: the first walker
    followed only `direct_successors()[0]`, silently dropping split-y."""
    decisions = rig_dir / "docs" / "decisions"
    (decisions / "split-0.md").write_text(
        "---\nid: split-0\ntitle: Split point\nsummary: the document that forked.\n"
        "status: superseded\ndate: 2026-01-01\n"
        "supersedes: []\nsuperseded-by: [split-x, split-y]\n---\n"
    )
    (decisions / "split-x.md").write_text(
        "---\nid: split-x\ntitle: One branch\nsummary: one of the two successors.\n"
        "status: accepted\ndate: 2026-02-01\n"
        "supersedes: [split-0]\nsuperseded-by: []\n---\n"
    )
    (decisions / "split-y.md").write_text(
        "---\nid: split-y\ntitle: The other branch\nsummary: the other successor.\n"
        "status: accepted\ndate: 2026-02-02\n"
        "supersedes: [split-0]\nsuperseded-by: []\n---\n"
    )


def _add_cycle_docs(rig_dir):
    """A pure two-node cycle, injected directly into the index's
    `supersessions` table (never through frontmatter — lint would reject
    it) to prove the walker doesn't just hang or silently return nothing."""
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


def _inject_cycle(config):
    """loop-b's frontmatter has no successor; add the closing edge straight
    into the built index, bypassing lint — a hand-edited/corrupt index."""
    conn = sqlite3.connect(config.index_db_path)
    conn.execute("INSERT INTO supersessions (from_id, to_id) VALUES ('loop-a', 'loop-b')")
    conn.commit()
    conn.close()


def test_direct_successors_and_predecessors(config, rig_dir):
    _add_chain_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        assert direct_successors(conn, "chain-a") == ["chain-b"]
        assert direct_successors(conn, "chain-c") == []
        assert direct_predecessors(conn, "chain-c") == ["chain-b"]
        assert direct_predecessors(conn, "chain-a") == []
    finally:
        conn.close()


def test_head_of_walks_to_the_end_of_the_chain(config, rig_dir):
    _add_chain_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        assert head_of(conn, "chain-a") == ["chain-c"]
        assert head_of(conn, "chain-b") == ["chain-c"]
        assert head_of(conn, "chain-c") == ["chain-c"]  # already the head
    finally:
        conn.close()


def test_head_of_returns_every_head_on_a_fork(config, rig_dir):
    _add_split_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        assert head_of(conn, "split-0") == ["split-x", "split-y"]
    finally:
        conn.close()


def test_head_info_none_when_not_superseded(config, rig_dir):
    _add_chain_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        assert head_info(conn, "chain-c") is None
        info = head_info(conn, "chain-a")
        assert info.heads == [("chain-c", "Chain C (current)")]
        assert info.cycle is False
    finally:
        conn.close()


def test_head_info_lists_every_head_on_a_fork(config, rig_dir):
    _add_split_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        info = head_info(conn, "split-0")
        assert info.heads == [("split-x", "One branch"), ("split-y", "The other branch")]
        assert info.cycle is False
    finally:
        conn.close()


def test_head_of_and_head_info_report_a_cycle(config, rig_dir):
    """The walker must not assume the index in front of it passed lint —
    only `ctx lint` rejects cycles, and only for documents that came through
    `ctx index`. A hand-edited `index.db` can still contain one, and it must
    be reported, not silently resolved to nothing."""
    _add_cycle_docs(rig_dir)
    build_index(config)
    _inject_cycle(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        assert head_of(conn, "loop-a") == []  # no real head — pure cycle
        info = head_info(conn, "loop-a")
        assert info.cycle is True
    finally:
        conn.close()


def test_history_view_excludes_siblings_reached_only_via_a_shared_successor(config, rig_dir):
    _add_fanin_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        lines, cycle = history_view(conn, "sibling-a")
    finally:
        conn.close()
    assert {ln.id for ln in lines} == {"sibling-a", "hub"}
    assert cycle is None


def test_history_view_on_the_consolidator_includes_every_absorbed_sibling(config, rig_dir):
    _add_fanin_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        lines, _cycle = history_view(conn, "hub")
    finally:
        conn.close()
    assert {ln.id for ln in lines} == {"sibling-a", "sibling-b", "hub"}


def test_history_view_dangling_id_is_skipped_not_raised(config, rig_dir):
    """A supersessions row naming a document that isn't in `documents` (a
    corrupt index, or a row left behind by manual editing) must not raise —
    the walker skips what it can't resolve."""
    _add_chain_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        conn.execute("INSERT INTO supersessions (from_id, to_id) VALUES ('ghost-id', 'chain-c')")
        conn.commit()
        lines, _cycle = history_view(conn, "chain-c")
        assert all(ln.id != "ghost-id" for ln in lines)
    finally:
        conn.close()


def test_history_view_oldest_to_newest_marks_head(config, rig_dir):
    _add_chain_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        lines, cycle = history_view(conn, "chain-a")
    finally:
        conn.close()
    assert [ln.id for ln in lines] == ["chain-a", "chain-b", "chain-c"]
    assert [ln.is_head for ln in lines] == [False, False, True]
    # A plain, non-forking chain never indents — depth marks a branch, not a
    # hop count.
    assert [ln.depth for ln in lines] == [0, 0, 0]
    assert cycle is None


def test_history_view_indents_a_fork_and_marks_both_heads(config, rig_dir):
    _add_split_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        lines, cycle = history_view(conn, "split-0")
    finally:
        conn.close()
    by_id = {ln.id: ln for ln in lines}
    assert by_id["split-0"].depth == 0
    assert by_id["split-0"].is_head is False
    assert by_id["split-x"].depth == 1
    assert by_id["split-y"].depth == 1
    assert by_id["split-x"].is_head is True
    assert by_id["split-y"].is_head is True
    assert cycle is None


def test_history_view_reports_the_cycle(config, rig_dir):
    _add_cycle_docs(rig_dir)
    build_index(config)
    _inject_cycle(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        lines, cycle = history_view(conn, "loop-a")
    finally:
        conn.close()
    assert lines  # doesn't come back empty just because of the cycle
    assert cycle is not None
    assert "loop-a" in cycle and "loop-b" in cycle


def test_history_view_dedups_a_node_reachable_both_ways(config, rig_dir):
    """Round 2 review finding: the injected cycle makes loop-b reachable both
    as a predecessor of loop-a (backward pass) and as loop-a's successor
    (forward pass) — it must render once, not twice, and the cycle must
    still be detected (dedup must not cut the walk short)."""
    _add_cycle_docs(rig_dir)
    build_index(config)
    _inject_cycle(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        lines, cycle = history_view(conn, "loop-a")
    finally:
        conn.close()
    ids = [ln.id for ln in lines]
    assert len(ids) == len(set(ids))  # every id exactly once
    assert set(ids) == {"loop-a", "loop-b"}
    assert cycle is not None


def test_history_view_empty_for_unknown_id(config, rig_dir):
    _add_chain_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        lines, cycle = history_view(conn, "does-not-exist")
    finally:
        conn.close()
    assert lines == []
    assert cycle is None


def test_cli_history_prints_oldest_to_newest(rig_dir):
    _add_chain_docs(rig_dir)
    result = CliRunner().invoke(main, ["history", "chain-a", "--dir", str(rig_dir)])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("chain-a")
    assert lines[2].startswith("chain-c")
    assert "<- current" in lines[2]
    assert "<- current" not in lines[0]
    assert "<- current" not in lines[1]


def test_cli_history_indents_a_fork(rig_dir):
    _add_split_docs(rig_dir)
    result = CliRunner().invoke(main, ["history", "split-0", "--dir", str(rig_dir)])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    root = next(ln for ln in lines if ln.startswith("split-0"))
    branches = [ln for ln in lines if "split-x" in ln or "split-y" in ln]
    assert not root.startswith("  ")
    assert len(branches) == 2
    assert all(ln.startswith("  ") for ln in branches)
    assert all("<- current" in ln for ln in branches)
    # deterministic order by id
    assert "split-x" in lines[1] and "split-y" in lines[2]


def test_cli_history_unknown_id_errors_cleanly(rig_dir):
    result = CliRunner().invoke(main, ["history", "no-such-id", "--dir", str(rig_dir)])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "no-such-id" in result.output


def test_cli_history_cycle_exits_2_and_warns_on_stderr(config, rig_dir):
    _add_cycle_docs(rig_dir)
    build_index(config)
    _inject_cycle(config)

    result = CliRunner().invoke(main, ["history", "loop-a", "--dir", str(rig_dir)])
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "cycle detected" in result.output
    assert "loop-a" in result.output

    # Round 2 review: loop-b is reachable both as loop-a's predecessor and
    # its successor on this fixture — each id must appear exactly once.
    ids_printed = [ln.strip().split(" ")[0] for ln in result.output.splitlines()]
    assert ids_printed.count("loop-a") == 1
    assert ids_printed.count("loop-b") == 1
