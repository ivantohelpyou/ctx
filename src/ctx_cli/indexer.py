"""Build `.ctx/index.db` — the disposable index — from frontmatter.

`rm index.db && ctx index` must reproduce the same file's query OUTPUT byte for
byte (the output contract, not the raw SQLite bytes, is what is guaranteed).
Determinism comes from: corpora processed in config order, files within a
corpus processed in sorted path order, and no wall-clock data written into any
column that participates in ranking or output.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import frontmatter as fm
from .corpus import RigConfig, iter_corpus_files

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

CREATE VIRTUAL TABLE documents_fts USING fts5(
    title, summary, terms, tags,
    tokenize='porter',
    content='documents', content_rowid='rowid'
);
"""


def _joined(values: list[str]) -> str:
    return " ".join(values)


def build_index(config: RigConfig) -> int:
    """Rebuild `.ctx/index.db` from scratch. Returns the number of documents indexed."""
    config.ctx_dir.mkdir(parents=True, exist_ok=True)
    db_path = config.index_db_path
    if db_path.exists():
        db_path.unlink()
    for suffix in ("-wal", "-shm"):
        extra = Path(str(db_path) + suffix)
        if extra.exists():
            extra.unlink()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)

        count = 0
        for spec in config.corpora:
            for path in iter_corpus_files(spec):
                doc = fm.parse_frontmatter(path.read_text(encoding="utf-8"))
                if not doc:
                    continue
                doc_id = doc.get("id")
                if not doc_id:
                    continue
                rel_path = str(path.relative_to(config.rig_root))
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
        conn.commit()
    finally:
        conn.close()
    return count


def newest_source_mtime(config: RigConfig) -> float:
    """Latest mtime across every corpus file and the config itself — compared
    against the index's own mtime to decide whether a lazy reindex is due."""
    newest = (config.ctx_dir / "config.yaml").stat().st_mtime
    for spec in config.corpora:
        for path in iter_corpus_files(spec):
            newest = max(newest, path.stat().st_mtime)
    return newest


def index_is_stale(config: RigConfig) -> bool:
    db_path = config.index_db_path
    if not db_path.exists():
        return True
    return newest_source_mtime(config) > db_path.stat().st_mtime


def ensure_fresh_index(config: RigConfig) -> None:
    """Lazy reindex: rebuild only if a source file changed since the index was
    last built. Must stay well inside the 100 ms budget for a few hundred docs."""
    if index_is_stale(config):
        build_index(config)
