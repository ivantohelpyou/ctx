"""Build `.ctx/index.db` — the disposable index — from frontmatter.

`rm index.db && ctx index` must reproduce the same file's query OUTPUT byte for
byte (the output contract, not the raw SQLite bytes, is what is guaranteed).
Determinism comes from: corpora processed in config order, files within a
corpus processed in sorted path order, and no wall-clock data written into any
column that participates in ranking or output.

A build never leaves a half-written `index.db` behind: it is assembled in a
temp file in the same directory and atomically renamed into place only after
it succeeds. Duplicate document ids are detected before any row is written,
so a bad corpus fails cleanly (`DuplicateIdError`) instead of raising a raw
`sqlite3.IntegrityError` partway through.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from . import frontmatter as fm
from .corpus import CorpusSpec, RigConfig, iter_corpus_files

SCHEMA = """
CREATE TABLE documents (
    rowid INTEGER PRIMARY KEY,
    id TEXT UNIQUE NOT NULL,
    corpus TEXT NOT NULL,
    path TEXT NOT NULL,
    title TEXT,
    status TEXT,
    date TEXT,
    summary TEXT,
    tags TEXT,
    terms TEXT,
    last_verified TEXT,
    covers TEXT
);

CREATE TABLE supersessions (
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL
);

CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE VIRTUAL TABLE documents_fts USING fts5(
    title, summary, terms, tags,
    tokenize='porter',
    content='documents', content_rowid='rowid'
);
"""


class DuplicateIdError(Exception):
    """Raised when two documents (in the same corpus or across corpora) share
    an `id`. Caught by the CLI and reported as a clean, non-zero-exit error —
    never a raw sqlite3.IntegrityError partway through a build."""

    def __init__(self, duplicates: dict[str, list[str]]):
        self.duplicates = duplicates
        lines = [
            f"  id {doc_id!r} used by: {', '.join(paths)}"
            for doc_id, paths in sorted(duplicates.items())
        ]
        super().__init__("duplicate document id(s):\n" + "\n".join(lines))


def _joined(values: list[str]) -> str:
    return " ".join(values)


def _display_path(path: Path, rig_root: Path) -> str:
    """Rig-relative path when the corpus lives under the rig (explainers,
    decisions); absolute path when it doesn't (shared, which lives at a fixed
    cross-rig location outside any one rig's tree)."""
    try:
        return str(path.relative_to(rig_root))
    except ValueError:
        return str(path)


def _collect_source_files(config: RigConfig) -> list[tuple[CorpusSpec, Path]]:
    """Every markdown file across every corpus, in deterministic order —
    including ones with no/invalid frontmatter, since deleting *any* of them
    is a directory change that should trigger a reindex."""
    return [(spec, path) for spec in config.corpora for path in iter_corpus_files(spec)]


def find_duplicate_ids(config: RigConfig) -> dict[str, list[str]]:
    """Return {id: [display_path, ...]} for every id used by more than one
    document, across all corpora combined (documents.id is globally unique)."""
    seen: dict[str, list[str]] = {}
    for spec, path in _collect_source_files(config):
        doc = fm.parse_frontmatter(path.read_text(encoding="utf-8"))
        doc_id = doc.get("id") if doc else None
        if not doc_id:
            continue
        seen.setdefault(str(doc_id), []).append(_display_path(path, config.rig_root))
    return {doc_id: paths for doc_id, paths in seen.items() if len(paths) > 1}


def build_index(config: RigConfig) -> int:
    """Rebuild `.ctx/index.db` from scratch. Returns the number of documents
    indexed. Raises `DuplicateIdError` (no index written at all) if any two
    documents share an id."""
    config.ctx_dir.mkdir(parents=True, exist_ok=True)
    final_path = config.index_db_path

    source_files = _collect_source_files(config)

    duplicates = find_duplicate_ids(config)
    if duplicates:
        raise DuplicateIdError(duplicates)

    newest_mtime = 0.0
    for _spec, path in source_files:
        newest_mtime = max(newest_mtime, path.stat().st_mtime)

    fd, tmp_name = tempfile.mkstemp(dir=str(config.ctx_dir), prefix=".index-", suffix=".db")
    os.close(fd)
    tmp_path = Path(tmp_name)
    tmp_path.unlink()  # sqlite3 creates it fresh; the mkstemp call was only to reserve a name
    for suffix in ("-wal", "-shm"):
        extra = Path(str(tmp_path) + suffix)
        if extra.exists():
            extra.unlink()

    try:
        conn = sqlite3.connect(tmp_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

            count = 0
            for spec, path in source_files:
                doc = fm.parse_frontmatter(path.read_text(encoding="utf-8"))
                if not doc:
                    continue
                doc_id = doc.get("id")
                if not doc_id:
                    continue
                rel_path = _display_path(path, config.rig_root)
                tags = _joined(fm.as_list(doc.get("tags")))
                terms = _joined(fm.as_list(doc.get("terms")))
                covers = _joined(fm.as_list(doc.get("covers")))
                date_val = doc.get("date")
                last_verified = doc.get("last-verified") or doc.get("last_verified")
                conn.execute(
                    "INSERT INTO documents "
                    "(id, corpus, path, title, status, date, summary, tags, terms, "
                    " last_verified, covers) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(doc_id),
                        spec.name,
                        rel_path,
                        doc.get("title") or "",
                        doc.get("status"),
                        str(date_val) if date_val is not None else None,
                        doc.get("summary") or "",
                        tags,
                        terms,
                        str(last_verified) if last_verified is not None else None,
                        covers,
                    ),
                )
                rowid = conn.execute(
                    "SELECT rowid FROM documents WHERE id = ?", (str(doc_id),)
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO documents_fts (rowid, title, summary, terms, tags) "
                    "VALUES (?,?,?,?,?)",
                    (rowid, doc.get("title") or "", doc.get("summary") or "", terms, tags),
                )

                supersedes = fm.as_list(doc.get("supersedes"))
                for old_id in supersedes:
                    conn.execute(
                        "INSERT INTO supersessions (from_id, to_id) VALUES (?,?)",
                        (str(doc_id), old_id),
                    )
                count += 1

            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                ("source_count", str(len(source_files))),
            )
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                ("newest_mtime", repr(newest_mtime)),
            )
            conn.commit()
        finally:
            conn.close()

        if final_path.exists():
            final_path.unlink()
        for suffix in ("-wal", "-shm"):
            extra = Path(str(final_path) + suffix)
            if extra.exists():
                extra.unlink()
        os.replace(tmp_path, final_path)
    finally:
        # Clean up the temp file (and its WAL/SHM siblings) if anything above
        # raised before the rename — a failed build must never leave a
        # half-written file at `final_path`, and must not litter `.ctx/` either.
        for p in (tmp_path, Path(str(tmp_path) + "-wal"), Path(str(tmp_path) + "-shm")):
            if p.exists():
                p.unlink()

    return count


def _read_meta(db_path: Path) -> dict[str, str] | None:
    """Read the build-time fingerprint (`source_count`, `newest_mtime`) stored
    in the index itself, or None if the index has no `meta` table (e.g. a
    pre-hq-jdeh-review index, or a corrupt file)."""
    try:
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    except sqlite3.OperationalError:
        return None
    try:
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    return dict(rows)


def index_is_stale(config: RigConfig) -> bool:
    """Stale if the index doesn't exist, if a source file was added or
    removed (source *count* changed), or if any source file is newer than
    what was recorded at build time. Comparing against the build's own
    recorded fingerprint — not the index file's own on-disk mtime — is what
    catches a deleted file (finding: mtime-only checks never notice a
    deletion) and avoids a same-tick mismatch between the index file's write
    time and a source file's mtime (finding: unrelated clocks, not source vs.
    source)."""
    db_path = config.index_db_path
    if not db_path.exists():
        return True
    meta = _read_meta(db_path)
    if meta is None:
        return True

    current_files = _collect_source_files(config)
    try:
        stored_count = int(meta.get("source_count", "-1"))
    except ValueError:
        return True
    if len(current_files) != stored_count:
        return True

    try:
        stored_newest = float(meta.get("newest_mtime", "0"))
    except ValueError:
        return True
    current_newest = 0.0
    for _spec, path in current_files:
        current_newest = max(current_newest, path.stat().st_mtime)
    return current_newest > stored_newest


def ensure_fresh_index(config: RigConfig) -> None:
    """Lazy reindex: rebuild only if a source file changed since the index was
    last built. Must stay well inside the 100 ms budget for a few hundred docs."""
    if index_is_stale(config):
        build_index(config)
