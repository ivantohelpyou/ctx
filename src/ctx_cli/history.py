"""The supersession walker: `ctx history <id>` and the `--history` annotation
on `ctx get`.

Built on the `supersessions(from_id, to_id)` table `ctx index` already
populates: one row per `supersedes:` entry, `from_id` = the newer document,
`to_id` = the one it replaced (see `indexer.build_index`). `ctx lint` already
rejects cycles and unreciprocated links in a document set that came through
`ctx index` — but this module reads `index.db` directly and must not assume
the index in front of it passed lint. Every walk here is visited-set bounded
and terminates on a corrupt or hand-edited index: a cycle stops the walk
rather than looping forever, and a dangling id (referenced by a
`supersessions` row but with no matching `documents` row) is skipped rather
than raising.
"""

from __future__ import annotations

import dataclasses
import sqlite3


def direct_successors(conn: sqlite3.Connection, doc_id: str) -> list[str]:
    """Documents that directly supersede `doc_id` — normally zero or one,
    but not enforced here (lint's job); sorted for a deterministic walk."""
    rows = conn.execute(
        "SELECT from_id FROM supersessions WHERE to_id = ? ORDER BY from_id", (doc_id,)
    ).fetchall()
    return [r[0] for r in rows]


def direct_predecessors(conn: sqlite3.Connection, doc_id: str) -> list[str]:
    """Documents that `doc_id` directly supersedes — many-to-one is the
    normal case (one box collapsing several positions into one)."""
    rows = conn.execute(
        "SELECT to_id FROM supersessions WHERE from_id = ? ORDER BY to_id", (doc_id,)
    ).fetchall()
    return [r[0] for r in rows]


def head_of(conn: sqlite3.Connection, doc_id: str) -> str:
    """Walk forward via `direct_successors`, deterministically (smallest id
    first when more than one), until a document with no successor — the
    current head of `doc_id`'s chain. Returns `doc_id` itself if it has no
    successor. Cycle-safe: a successor already visited stops the walk at the
    node before it, rather than looping."""
    current = doc_id
    visited = {doc_id}
    while True:
        successors = direct_successors(conn, current)
        if not successors:
            return current
        nxt = successors[0]
        if nxt in visited:
            return current
        visited.add(nxt)
        current = nxt


def head_info(conn: sqlite3.Connection, doc_id: str) -> tuple[str, str] | None:
    """`(head_id, head_title)` if `doc_id` has been superseded (its chain's
    head is not itself), else None."""
    head_id = head_of(conn, doc_id)
    if head_id == doc_id:
        return None
    row = conn.execute("SELECT title FROM documents WHERE id = ?", (head_id,)).fetchone()
    return head_id, (row[0] if row else "")


def _document_row(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, title, status, date FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "title": row[1] or "", "status": row[2], "date": row[3]}


def _forward_path(conn: sqlite3.Connection, doc_id: str) -> list[str]:
    """`doc_id`, then its successor, then that one's, deterministically
    (smallest id first on a fork), to the head. A single path — it must NOT
    also pull in a successor's OTHER predecessors, or asking about one of
    nine positions a later box consolidated would return all nine (real
    data: pos-93 alone absorbed nine others). Cycle-safe like `head_of`,
    which this duplicates rather than calls, so a caller wanting only the
    head isn't paying for a list it discards."""
    path = [doc_id]
    visited = {doc_id}
    current = doc_id
    while True:
        successors = direct_successors(conn, current)
        if not successors:
            return path
        nxt = successors[0]
        if nxt in visited:
            return path
        visited.add(nxt)
        path.append(nxt)
        current = nxt


def _backward_tree(
    conn: sqlite3.Connection, doc_id: str, visited: set[str] | None = None
) -> set[str]:
    """Every id `doc_id` recursively absorbed — its own predecessors, and
    theirs. Deliberately NOT their other successors: a predecessor's own
    lineage before it became part of `doc_id` is in scope; a sibling reached
    only through a shared later successor is not (that's what makes this
    `doc_id`'s history and not the whole cluster's). Visited-set bounded, so
    a cycle in a hand-edited index still terminates."""
    if visited is None:
        visited = set()
    if doc_id in visited:
        return visited
    visited.add(doc_id)
    for pred in direct_predecessors(conn, doc_id):
        _backward_tree(conn, pred, visited)
    return visited


@dataclasses.dataclass
class ChainEntry:
    id: str
    title: str
    status: str | None
    date: str | None
    is_head: bool


def chain(conn: sqlite3.Connection, doc_id: str) -> list[ChainEntry]:
    """`doc_id`'s own supersession chain, oldest to newest (sorted by date,
    then id — undated entries sort first, alongside whatever else has no
    date, rather than crashing on a None/None comparison): what fed into
    `doc_id` (`_backward_tree`, recursive) plus where `doc_id` leads
    (`_forward_path`, to the head). Deliberately not the full connected
    component — a document that ALSO reached the same head by a different
    predecessor is not part of `doc_id`'s own history (real data: nine
    positions were consolidated into one; asking about any single one of
    the nine must not return all nine). `is_head` marks any entry with no
    successor of its own. Empty if `doc_id` isn't in the index at all (a
    dangling id referenced only by a corrupt supersessions row is skipped
    the same way, never raised)."""
    ids = _backward_tree(conn, doc_id) | set(_forward_path(conn, doc_id))
    entries = []
    for i in ids:
        row = _document_row(conn, i)
        if row is None:
            continue
        entries.append(
            ChainEntry(
                id=row["id"],
                title=row["title"],
                status=row["status"],
                date=row["date"],
                is_head=not direct_successors(conn, i),
            )
        )
    entries.sort(key=lambda e: (e.date or "", e.id))
    return entries
