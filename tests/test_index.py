import os
import shutil
import sqlite3
import time

import pytest

from ctx_cli.corpus import load_config
from ctx_cli.indexer import DuplicateIdError, build_index, index_is_stale


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


def test_duplicate_id_raises_before_any_write(rig_dir):
    """Finding 1: a duplicate id must fail cleanly (DuplicateIdError), before
    any row is written — never a raw sqlite3.IntegrityError partway through,
    and never a schema-only index.db left behind."""
    dupe = rig_dir / "docs" / "explainers" / "003-dupe.md"
    dupe.write_text(
        "---\nid: exp-001\ntitle: Duplicate\nsummary: shares an id with exp-001.\n---\n"
    )
    config = load_config(rig_dir / ".ctx")
    with pytest.raises(DuplicateIdError) as excinfo:
        build_index(config)
    assert "exp-001" in str(excinfo.value)
    assert not config.index_db_path.exists()


def test_duplicate_id_across_corpora_detected(rig_dir):
    dupe = rig_dir / "docs" / "shared" / "dupe.md"
    dupe.write_text(
        "---\nid: adr-001\ntitle: Duplicate\nsummary: shares an id with a decision doc.\n---\n"
    )
    config = load_config(rig_dir / ".ctx")
    with pytest.raises(DuplicateIdError) as excinfo:
        build_index(config)
    assert "adr-001" in str(excinfo.value)


def test_failed_build_leaves_no_temp_files_in_ctx_dir(rig_dir):
    dupe = rig_dir / "docs" / "explainers" / "003-dupe.md"
    dupe.write_text("---\nid: exp-001\ntitle: X\nsummary: Y\n---\n")
    config = load_config(rig_dir / ".ctx")
    with pytest.raises(DuplicateIdError):
        build_index(config)
    leftovers = list(config.ctx_dir.glob(".index-*"))
    assert leftovers == []


def test_deleted_source_file_triggers_reindex(config):
    """Finding 2: index_is_stale must notice a *deletion*, not just an edit —
    mtime comparisons alone never see a file that is no longer there."""
    build_index(config)
    assert not index_is_stale(config)

    doomed = config.corpora[0].path / "002-budget-arg.md"
    doomed.unlink()
    assert index_is_stale(config)


def test_staleness_uses_stored_build_fingerprint_not_index_file_mtime(config):
    """Finding 3: comparing against the index *file's own* on-disk mtime can
    miss an edit that lands at or before that mtime (write latency, clock
    ticks). Comparing against the max source mtime recorded *at build time*
    catches it even if the index file's own mtime is later still."""
    build_index(config)

    doc = config.corpora[0].path / "001-fts5-choice.md"
    build_time_newest = doc.stat().st_mtime
    for _spec, p in [(s, f) for s in config.corpora for f in s.path.glob("*.md")]:
        build_time_newest = max(build_time_newest, p.stat().st_mtime)

    # Simulate the index file's own mtime landing well after the real
    # build-time fingerprint (write buffering / clock skew) ...
    future = build_time_newest + 10
    os.utime(config.index_db_path, (future, future))

    # ... then an edit that is newer than the fingerprint but still older
    # than the index file's own (inflated) mtime.
    edit_time = build_time_newest + 3
    os.utime(doc, (edit_time, edit_time))

    assert index_is_stale(config), (
        "a source edit between the build fingerprint and the index file's "
        "own (later) mtime must still be detected as stale"
    )


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
