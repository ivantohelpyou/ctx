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

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from . import frontmatter as fm
from .corpus import (
    CorpusSpec,
    RigConfig,
    is_draft_skipped,
    iter_corpus_files,
    resolved_frontmatter,
)
from .lint import decisions_structural_errors

# The FTS5 tokenizer, named once: query.py tokenizes query terms with the same
# spec so a term's identity (its stemmed tokens) agrees with the index's.
FTS_TOKENIZE = "porter"

SCHEMA = f"""
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
    tokenize='{FTS_TOKENIZE}',
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


def _normalize_date_field(value):
    """`date`/`last-verified` as stored in the index: `YYYY-MM-DD` when the
    value parses (see `frontmatter.normalize_date`); otherwise fall back to
    `str(value)` so one bad value doesn't fail the whole build — `ctx lint`
    is what rejects it (re-i1n review, finding 6)."""
    if value is None:
        return None
    try:
        return fm.normalize_date(value)
    except fm.DateParseError:
        return str(value)


def _display_path(path: Path, rig_root: Path) -> str:
    """Rig-relative path when the corpus lives under the rig (explainers,
    decisions); absolute path when it doesn't (shared, which lives at a fixed
    cross-rig location outside any one rig's tree).

    Plain string-prefix arithmetic rather than `Path.relative_to()`: both
    `path` and `rig_root` are already absolute/resolved by the time they get
    here (`CorpusSpec.path` and `find_ctx_dir`'s walk both call `.resolve()`),
    so this is safe, and `relative_to()`'s general-purpose part-by-part
    reconstruction measured as the dominant cost of fingerprinting a few
    hundred files — not the stat() calls next to it.

    Precondition: `path` is strictly below a corpus root, never equal to
    `rig_root` itself — no caller ever passes a bare rig root, so the
    `path == rig_root` case (which this returns as the raw root, unlike
    `relative_to()`'s ".") is unreachable in practice (round-3 review)."""
    path_str = str(path)
    prefix = str(rig_root) + os.sep
    if path_str.startswith(prefix):
        return path_str[len(prefix) :]
    return path_str


def _collect_source_files(config: RigConfig) -> list[tuple[CorpusSpec, Path]]:
    """Every markdown file across every corpus, in deterministic order —
    including ones with no/invalid frontmatter, since deleting *any* of them
    is a directory change that should trigger a reindex."""
    return [(spec, path) for spec in config.corpora for path in iter_corpus_files(spec)]


def _fingerprint(config: RigConfig, files: list[tuple[CorpusSpec, Path]]) -> str:
    """A sha256 digest over the sorted (corpus, path, size, mtime_ns) of every
    corpus file. Stored in `meta` at build time; `index_is_stale` recomputes
    it and compares for equality.

    This is a *content-shape* fingerprint, not just a count or a single max
    mtime: a same-tick delete-one-add-another (count unchanged, new file's
    mtime no greater than the old max) changes the digest because the file
    *set* changed; an edit whose mtime ties the previous max (`>` missed it)
    changes the digest because that file's own (path, size, mtime_ns) entry
    changed. It also catches renames and swaps for free, since the path is
    part of what is hashed.

    One blind spot remains, and cannot be closed by stat() alone: a content
    edit that preserves both size and mtime_ns exactly. That only happens via
    a deliberate `os.utime` restoring the original timestamp after an
    in-place edit of the same length — not a real editor workflow, but
    documented here and in the README's "when to run `ctx index` by hand"
    note.

    Entries are joined with a bare "\n" for hashing, but each entry is
    `json.dumps`-encoded first: a path can legally contain a literal newline
    (round-3 review), and json escapes it (`\\n`, two characters, inside the
    JSON string) rather than emitting the raw byte — so the character used
    as the entry separator can never appear *as* a separator inside a field,
    and two different file sets can't serialize to the same joined string."""
    entries = []
    for spec, path in files:
        st = path.stat()
        rel = _display_path(path, config.rig_root)
        entries.append((spec.name, rel, st.st_size, st.st_mtime_ns))
    entries.sort()
    lines = "\n".join(json.dumps(list(e)) for e in entries)
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def find_duplicate_ids(config: RigConfig) -> dict[str, list[str]]:
    """Return {id: [display_path, ...]} for every id used by more than one
    document, across all corpora combined (documents.id is globally unique)."""
    seen: dict[str, list[str]] = {}
    for spec, path in _collect_source_files(config):
        raw = fm.parse_frontmatter(path.read_text(encoding="utf-8"))
        doc = resolved_frontmatter(spec, path, raw)
        if doc and is_draft_skipped(spec, doc):
            continue
        doc_id = doc.get("id") if doc else None
        if not doc_id:
            continue
        seen.setdefault(str(doc_id), []).append(_display_path(path, config.rig_root))
    return {doc_id: paths for doc_id, paths in seen.items() if len(paths) > 1}


def warn_missing_corpus_dirs(config: RigConfig) -> list[str]:
    """One-line stderr warning, exit 0, for every corpus whose resolved
    `path` is not a directory — a config error (typo, wrong-machine
    absolute path, corpus not yet checked out) that `iter_corpus_files`
    otherwise swallows silently, returning zero documents forever with no
    signal (hq-synj review finding 1). The other corpora still index; this
    never blocks a build, it only makes the empty one visible. Returns the
    messages (also used by `ctx lint`, which reports the same thing as a
    LINT ERROR rather than a warning — a bad path is worse than a corpus
    that's merely empty)."""
    messages = []
    for spec in config.corpora:
        if not spec.path.is_dir():
            messages.append(f"corpus {spec.name!r}: path {spec.path} is not a directory")
    return messages


def build_index(config: RigConfig) -> int:
    """Rebuild `.ctx/index.db` from scratch. Returns the number of documents
    indexed. Raises `DuplicateIdError` (no index written at all) if any two
    documents share an id."""
    config.ctx_dir.mkdir(parents=True, exist_ok=True)
    final_path = config.index_db_path

    for msg in warn_missing_corpus_dirs(config):
        print(f"warning: {msg}", file=sys.stderr)

    source_files = _collect_source_files(config)

    duplicates = find_duplicate_ids(config)
    if duplicates:
        raise DuplicateIdError(duplicates)

    fingerprint = _fingerprint(config, source_files)

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
                raw = fm.parse_frontmatter(path.read_text(encoding="utf-8"))
                doc = resolved_frontmatter(spec, path, raw)
                if not doc:
                    continue
                if is_draft_skipped(spec, doc):
                    continue
                doc_id = doc.get("id")
                if not doc_id:
                    continue
                rel_path = _display_path(path, config.rig_root)
                tags = _joined(fm.as_tag_list(doc.get("tags")))
                terms = _joined(fm.as_tag_list(doc.get("terms")))
                covers = _joined(fm.as_list(doc.get("covers")))
                date_str = _normalize_date_field(doc.get("date"))
                last_verified = _normalize_date_field(
                    doc.get("last-verified") or doc.get("last_verified")
                )
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
                        date_str,
                        doc.get("summary") or "",
                        tags,
                        terms,
                        last_verified,
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
                ("fingerprint", fingerprint),
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
    """Read the build-time `fingerprint` stored in the index itself, or None
    if the index has no `meta` table (e.g. a pre-hq-jdeh-review index, or a
    corrupt file)."""
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
    """Stale if the index doesn't exist, or if the current corpus's
    fingerprint (see `_fingerprint`) differs from the one recorded at build
    time. Comparing digests rather than a count-plus-max-mtime pair is what
    catches a same-tick delete-and-add (count unchanged, new file's mtime no
    greater than the old max — round-2 review finding 1) and an edit whose
    mtime exactly ties the previous max (`>` never sees an equal value —
    finding 2): both change which (path, size, mtime_ns) triples exist, so
    both change the digest, regardless of direction or magnitude.

    Blind spot, inherent to any stat()-based check and not closed by this
    fix: a content edit that preserves both size and mtime_ns exactly (only
    reachable via a deliberate `os.utime` restoring the original timestamp
    after an in-place edit of identical length) is invisible. Run `ctx index`
    by hand if you ever do that."""
    db_path = config.index_db_path
    if not db_path.exists():
        return True
    meta = _read_meta(db_path)
    if meta is None:
        return True

    stored_fingerprint = meta.get("fingerprint")
    if stored_fingerprint is None:
        return True

    current_files = _collect_source_files(config)
    return _fingerprint(config, current_files) != stored_fingerprint


def ensure_fresh_index(config: RigConfig) -> None:
    """Lazy reindex: rebuild only if a source file changed since the index was
    last built. Must stay well inside the 100 ms budget for a few hundred docs.

    `ctx index` warns on a decisions structural problem (reciprocity, cycles,
    accepted-but-superseded); this is the OTHER path into a rebuild — every
    `ctx get`/`ctx history` that finds the index stale — and used to run
    silently, so a corpus could drift out of shape for as long as nobody ran
    `ctx index` or `ctx lint` by hand (round 2 review). Same one-line stderr
    warning, same exit 0: `--strict` is `ctx index`-only, an explicit choice
    to refuse publishing, not something a read command should ever do."""
    if index_is_stale(config):
        build_index(config)
        errors = decisions_structural_errors(config)
        if errors:
            print(f"{len(errors)} decisions lint errors — run ctx lint", file=sys.stderr)
