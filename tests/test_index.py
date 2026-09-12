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


def test_same_tick_delete_and_add_triggers_reindex(config):
    """Round-2 review finding 1: a (count, max-mtime) fingerprint misses a
    same-tick delete-one-add-another where the new file's mtime is no greater
    than the old max — count is unchanged and the max doesn't move, but the
    file *set* did change. A content digest over (path, size, mtime_ns)
    catches it because it hashes the set of files, not a count of them."""
    build_index(config)
    explainers_dir = config.corpora[0].path
    doomed = explainers_dir / "002-budget-arg.md"
    original_stat = doomed.stat()

    doomed.unlink()
    replacement = explainers_dir / "003-replacement.md"
    replacement.write_text(
        "---\nid: exp-003\ntitle: Replacement\nsummary: takes 002's place.\n---\n"
    )
    # Pin the new file's mtime to no later than the one it replaced, so a
    # max-mtime-only check would see nothing — count unchanged, max unchanged.
    os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    assert index_is_stale(config)


def test_edit_with_mtime_tied_to_original_but_size_changed_triggers_reindex(config):
    """Round-2 review finding 2: `current_newest > stored_newest` is strict,
    so an edit whose mtime exactly ties the recorded max was invisible. Here
    the mtime is deliberately restored to its original value after editing
    content (size changes) — size is part of the digest, so this is caught
    even though no mtime in the corpus moved at all."""
    build_index(config)
    doc = config.corpora[0].path / "001-fts5-choice.md"
    stat_before = doc.stat()

    doc.write_text(doc.read_text() + "\nmore content, same original mtime.\n")
    # Restore the *exact* original nanosecond timestamp (not just the same
    # float second) so this test isolates size as the signal, not a rounding
    # artifact of a float round-trip through os.utime.
    os.utime(doc, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))

    assert index_is_stale(config)


def test_rename_only_triggers_reindex(config):
    """Same bytes, same mtime, different path — the digest includes path, so
    a rename (or swap) is caught even with content and timestamps untouched."""
    build_index(config)
    doc = config.corpora[0].path / "001-fts5-choice.md"
    stat_before = doc.stat()
    renamed = config.corpora[0].path / "001-renamed.md"
    doc.rename(renamed)
    os.utime(renamed, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))

    assert index_is_stale(config)


def test_fingerprint_distinguishes_path_with_embedded_newline(config):
    """Round-3 review: entries are joined with a bare '\\n' for hashing, and
    a path can legally contain a literal newline — two different states
    could otherwise serialize identically. json-encoding each entry first
    (which escapes an embedded newline as two characters, `\\n`, inside the
    JSON string) must still tell these two states apart."""
    from ctx_cli.indexer import _collect_source_files, _fingerprint

    explainers_dir = config.corpora[0].path
    normal = explainers_dir / "plain-name.md"
    normal.write_text("---\nid: plain\ntitle: Plain\nsummary: no funny business.\n---\n")

    fp_before = _fingerprint(config, _collect_source_files(config))

    normal.rename(explainers_dir / "weird\nname.md")
    fp_after = _fingerprint(config, _collect_source_files(config))

    assert fp_before != fp_after


def test_fingerprint_cost_on_300_docs_under_20ms(tmp_path, rig_dir):
    """Measures the stat()-everything cost the digest adds; must stay well
    inside the lazy-reindex budget. Median of several calls, with one
    untimed warm-up first: the very first stat() of a just-created file can
    be dominated by filesystem-cache noise unrelated to the fingerprint
    algorithm itself (observed: ~30ms cold vs. ~7-10ms warm on this
    machine), and real usage sees the warm case — the OS file-metadata
    cache persists across the separate `ctx` processes a session actually
    runs, even though each is a fresh interpreter."""
    from ctx_cli.indexer import _collect_source_files, _fingerprint

    explainers_dir = rig_dir / "docs" / "explainers"
    for i in range(300):
        (explainers_dir / f"gen-{i:04d}.md").write_text(
            f"---\nid: gen-{i:04d}\ntitle: Generated {i}\nsummary: filler doc {i}.\n---\n"
        )
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    files = _collect_source_files(config)
    assert len(files) >= 300

    _fingerprint(config, files)  # untimed warm-up

    samples = []
    for _ in range(5):
        start = time.perf_counter()
        _fingerprint(config, files)
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    median_ms = samples[len(samples) // 2]

    assert index_is_stale(config) is False
    assert median_ms < 20, (
        f"fingerprint over {len(files)} files took {median_ms:.2f}ms (median of "
        f"{samples}), budget is 20ms"
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
