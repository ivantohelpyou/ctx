import os
import shutil
import sqlite3
import time

from ctx_cli.corpus import load_config
from ctx_cli.indexer import build_index, index_is_stale


def test_build_index_counts_all_fixture_docs(config):
    count = build_index(config)
    assert count == 5  # 2 explainers + 2 decisions + 1 shared
    assert config.index_db_path.exists()


def test_index_has_expected_tables(config):
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"documents", "supersessions"} <= tables
    conn.close()


def test_supersession_recorded(config):
    build_index(config)
    conn = sqlite3.connect(config.index_db_path)
    rows = conn.execute("SELECT from_id, to_id FROM supersessions").fetchall()
    conn.close()
    assert ("adr-002", "adr-001") in rows


def test_index_is_stale_when_missing(config):
    assert index_is_stale(config)


def test_index_not_stale_after_build(config):
    build_index(config)
    assert not index_is_stale(config)


def test_index_stale_after_source_edit(config):
    build_index(config)
    doc = config.corpora[0].path / "001-fts5-choice.md"
    future = time.time() + 5
    os.utime(doc, (future, future))
    assert index_is_stale(config)


def test_rm_and_reindex_reproduces_same_row_count(config):
    n1 = build_index(config)
    config.index_db_path.unlink()
    n2 = build_index(config)
    assert n1 == n2


def test_corpus_path_outside_rig_root_uses_absolute_path(tmp_path, rig_dir):
    """`shared` lives at a fixed cross-rig location outside any one rig's
    tree — indexing it must not assume every corpus is nested under rig_root."""
    outside_shared = tmp_path / "outside-shared"
    shutil.copytree(rig_dir / "docs" / "shared", outside_shared)
    (rig_dir / ".ctx" / "config.yaml").write_text(
        "corpora:\n"
        "  shared:\n"
        "    type: shared\n"
        f"    path: {outside_shared}\n"
    )
    config = load_config(rig_dir / ".ctx")
    count = build_index(config)
    assert count == 1
    conn = sqlite3.connect(config.index_db_path)
    path_value = conn.execute("SELECT path FROM documents WHERE id = 'shared-001'").fetchone()[0]
    conn.close()
    assert path_value == str(outside_shared / "lesson-001-worktrees.md")
