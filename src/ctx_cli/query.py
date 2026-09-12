"""`ctx get` — two-stage retrieval: FTS5 over frontmatter, budgeted output.

Output contract (must be deterministic — same query + same index => same bytes):

    id — title — path#slug — summary
      superseded-by: id — title      (only with --history, only if superseded)
    ...
    dropped: id — title
    ...

Bodies are never read at query time; only the index table and FTS5 built from
frontmatter are consulted.
"""

from __future__ import annotations

import dataclasses
import re
import sqlite3
from pathlib import Path

from .history import head_info

# title(10), summary(1), terms(5), tags(1) — matches column order in the FTS5
# table so title and terms outrank a body-shaped summary hit.
BM25_WEIGHTS = (10.0, 1.0, 5.0, 1.0)
DEFAULT_BUDGET = 1500
# Hard cap on returned entries regardless of budget (acceptance: "typical
# query... ≤5 entries"). `--limit` overrides. Without this, a query with many
# small hits and a generous budget can return dozens of entries.
DEFAULT_LIMIT = 5


@dataclasses.dataclass
class Hit:
    id: str
    corpus: str
    path: str
    title: str
    summary: str
    status: str | None
    # (head_id, head_title) when this hit has been superseded — only ever
    # set with --history (search()'s only caller of head_info); walker output,
    # never a raw column.
    superseded_by: tuple[str, str] | None = None


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return slug or "doc"


def _fts_match_expr(query: str) -> str:
    """Turn free text into a permissive FTS5 MATCH expression: each word quoted
    (escapes FTS5 operator characters) and OR'd together, so Porter stemming and
    partial-term matches both apply."""
    terms = [t for t in re.split(r"\s+", query.strip()) if t]
    if not terms:
        return '""'
    escaped = ['"' + t.replace('"', '""') + '"' for t in terms]
    return " OR ".join(escaped)


def format_hit(hit: Hit) -> str:
    return f"{hit.id} — {hit.title} — {hit.path}#{_slugify(hit.title)} — {hit.summary}"


def format_superseded_by(hit: Hit) -> str | None:
    """The walker's one-line annotation on a `--history` hit that has been
    superseded: the HEAD of its chain (the current document), not just its
    direct successor — reading `id — title` alone should never leave you one
    hop short of what's actually current."""
    if not hit.superseded_by:
        return None
    head_id, head_title = hit.superseded_by
    return f"  superseded-by: {head_id} — {head_title}"


def format_dropped(hit: Hit) -> str:
    return f"dropped: {hit.id} — {hit.title}"


def _rendered_char_len(hit: Hit) -> int:
    """`format_hit` plus its `superseded-by` annotation line (if any), with
    the joining newline counted — what `apply_budget` actually spends on this
    hit once rendered, so a chained hit's second line consumes budget too."""
    length = len(format_hit(hit))
    annotation = format_superseded_by(hit)
    if annotation:
        length += 1 + len(annotation)
    return length


def search(
    index_db_path: Path,
    query: str,
    corpus: str | None = None,
    history: bool = False,
) -> list[Hit]:
    """Run the FTS5 query, status-filtered before ranking, ranked by BM25.

    Opens `index.db` read-only: a query never writes to it, so a read-only
    `.ctx/` directory (or a read-only filesystem) must not stop `ctx get` from
    working."""
    uri = index_db_path.resolve().as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        match_expr = _fts_match_expr(query)
        sql = (
            "SELECT documents.id, documents.corpus, documents.path, documents.title, "
            "documents.summary, documents.status, "
            "bm25(documents_fts, ?, ?, ?, ?) AS rank "
            "FROM documents_fts JOIN documents ON documents.rowid = documents_fts.rowid "
            "WHERE documents_fts MATCH ?"
        )
        params: list = [*BM25_WEIGHTS, match_expr]
        if corpus:
            sql += " AND documents.corpus = ?"
            params.append(corpus)
        if not history:
            # Status filter before ranking (memo: "status beats score"). Only
            # decisions carry a lifecycle status; anything else passes through.
            sql += " AND (documents.status IS NULL OR documents.status = 'accepted')"
        sql += " ORDER BY rank ASC, documents.id ASC"
        rows = conn.execute(sql, params).fetchall()
        hits = [
            Hit(
                id=r["id"],
                corpus=r["corpus"],
                path=r["path"],
                title=r["title"] or "",
                summary=r["summary"] or "",
                status=r["status"],
            )
            for r in rows
        ]
        if history:
            # Only --history ever surfaces a superseded hit, so only --history
            # pays for the walk (one bounded query per hit; see history.py).
            for h in hits:
                h.superseded_by = head_info(conn, h.id)
    finally:
        conn.close()
    return hits


def apply_budget(
    hits: list[Hit], budget: int, limit: int = DEFAULT_LIMIT
) -> tuple[list[Hit], list[Hit]]:
    """Greedily fill to `budget` tokens (chars/4) in rank order, capped at
    `limit` entries either way. The first hit is always kept even if it alone
    exceeds budget, so a query never returns zero results purely because one
    entry is large. Rank order (and its `documents.id` tie-break, from the
    SQL `ORDER BY`) is preserved, so which entries get dropped is
    deterministic — same query, same index, same bytes."""
    kept: list[Hit] = []
    cumulative_tokens = 0.0
    cutoff = None
    for i, hit in enumerate(hits):
        if len(kept) >= limit:
            cutoff = i
            break
        line_tokens = _rendered_char_len(hit) / 4
        if kept and cumulative_tokens + line_tokens > budget:
            cutoff = i
            break
        kept.append(hit)
        cumulative_tokens += line_tokens
    dropped = hits[cutoff:] if cutoff is not None else []
    return kept, dropped


def render(kept: list[Hit], dropped: list[Hit]) -> str:
    lines = []
    for h in kept:
        lines.append(format_hit(h))
        annotation = format_superseded_by(h)
        if annotation:
            lines.append(annotation)
    lines += [format_dropped(h) for h in dropped]
    return "\n".join(lines) + ("\n" if lines else "")
