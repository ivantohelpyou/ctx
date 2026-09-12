"""The supersession walker (`history.py`) and `ctx history <id>`."""

from __future__ import annotations

import sqlite3

from click.testing import CliRunner

from ctx_cli.cli import main
from ctx_cli.history import chain, direct_predecessors, direct_successors, head_info, head_of
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


def test_chain_excludes_siblings_reached_only_via_a_shared_successor(config, rig_dir):
    _add_fanin_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        entries = chain(conn, "sibling-a")
    finally:
        conn.close()
    assert {e.id for e in entries} == {"sibling-a", "hub"}


def test_chain_on_the_consolidator_includes_every_absorbed_sibling(config, rig_dir):
    _add_fanin_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        entries = chain(conn, "hub")
    finally:
        conn.close()
    assert {e.id for e in entries} == {"sibling-a", "sibling-b", "hub"}


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
        assert head_of(conn, "chain-a") == "chain-c"
        assert head_of(conn, "chain-b") == "chain-c"
        assert head_of(conn, "chain-c") == "chain-c"  # already the head
    finally:
        conn.close()


def test_head_info_none_when_not_superseded(config, rig_dir):
    _add_chain_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        assert head_info(conn, "chain-c") is None
        assert head_info(conn, "chain-a") == ("chain-c", "Chain C (current)")
    finally:
        conn.close()


def test_head_of_terminates_on_a_cycle_in_a_corrupt_index(config, rig_dir):
    """The walker must not assume the index in front of it passed lint —
    only `ctx lint` rejects cycles, and only for documents that came through
    `ctx index`. A hand-edited `index.db` can still contain one."""
    _add_chain_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        # chain-c already points nowhere; force a cycle chain-a -> chain-b ->
        # chain-c -> chain-a directly in the table, bypassing lint entirely.
        conn.execute("INSERT INTO supersessions (from_id, to_id) VALUES ('chain-a', 'chain-c')")
        conn.commit()
        # Must return, not loop forever.
        result = head_of(conn, "chain-a")
        assert result in {"chain-a", "chain-b", "chain-c"}
    finally:
        conn.close()


def test_chain_dangling_id_is_skipped_not_raised(config, rig_dir):
    """A supersessions row naming a document that isn't in `documents` (a
    corrupt index, or a row left behind by manual editing) must not raise —
    the walker skips what it can't resolve."""
    _add_chain_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        conn.execute("INSERT INTO supersessions (from_id, to_id) VALUES ('ghost-id', 'chain-c')")
        conn.commit()
        entries = chain(conn, "chain-c")
        assert all(e.id != "ghost-id" for e in entries)
    finally:
        conn.close()


def test_chain_oldest_to_newest_marks_head(config, rig_dir):
    _add_chain_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        entries = chain(conn, "chain-a")
    finally:
        conn.close()
    assert [e.id for e in entries] == ["chain-a", "chain-b", "chain-c"]
    assert [e.is_head for e in entries] == [False, False, True]


def test_chain_empty_for_unknown_id(config, rig_dir):
    _add_chain_docs(rig_dir)
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    try:
        assert chain(conn, "does-not-exist") == []
    finally:
        conn.close()


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


def test_cli_history_unknown_id_errors_cleanly(rig_dir):
    result = CliRunner().invoke(main, ["history", "no-such-id", "--dir", str(rig_dir)])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "no-such-id" in result.output
