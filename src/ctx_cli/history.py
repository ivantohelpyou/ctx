"""The supersession walker: `ctx history <id>` and the `--history` annotation
on `ctx get`.

Built on the `supersessions(from_id, to_id)` table `ctx index` already
populates: one row per `supersedes:` entry, `from_id` = the newer document,
`to_id` = the one it replaced (see `indexer.build_index`). `ctx lint` already
rejects cycles and unreciprocated links in a document set that came through
`ctx index` — but this module reads `index.db` directly and must not assume
the index in front of it passed lint. Every walk here is path-visited-set
bounded and terminates on a corrupt or hand-edited index: a successor already
on the current path is a cycle, not followed further, and reported rather
than silently swallowed (review finding, hq-sl5k round 2) — a dangling id
(referenced by a `supersessions` row but with no matching `documents` row)
is skipped rather than raising.

`head_of` is plural on purpose: lint allows a document to be superseded by
more than one other (`superseded-by:` reciprocity is checked per entry, not
capped at one), so a fork is valid data, not corruption — round 2's
`split-0`/`split-x`/`split-y` fixture. The first version of this module
followed only `direct_successors()[0]`, silently dropping every sibling
fork; every walk here now follows all of them.
"""

from __future__ import annotations

import dataclasses
import sqlite3


def direct_successors(conn: sqlite3.Connection, doc_id: str) -> list[str]:
    """Documents that directly supersede `doc_id` — one-to-many is valid
    data (a fork), not enforced down to one here or by lint; sorted for a
    deterministic walk."""
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


@dataclasses.dataclass
class HeadWalk:
    heads: list[str]  # sorted, deduped; every true leaf reachable forward
    cycle: bool  # a successor edge pointed back onto the current path


def walk_heads(conn: sqlite3.Connection, doc_id: str) -> HeadWalk:
    """DFS over every forward branch from `doc_id`. A document with zero
    successors is a head, wherever it's reached from. A successor already on
    the path being walked is a cycle: that branch stops there (so the walk
    always terminates, even on a hand-edited index with a loop in it) and
    `cycle=True` is set on the result — it is NOT counted as a head, because
    it does have an outgoing edge, just one this walk refuses to follow.
    `doc_id` itself is the (only) head when it has no successor at all,
    matching "not superseded"."""
    heads: list[str] = []
    cycle = False

    def visit(current: str, path: tuple[str, ...]) -> None:
        nonlocal cycle
        successors = direct_successors(conn, current)
        live = []
        for s in successors:
            if s in path:
                cycle = True
                continue
            live.append(s)
        if not live:
            if not successors:
                heads.append(current)
            return
        for nxt in live:
            visit(nxt, (*path, nxt))

    visit(doc_id, (doc_id,))
    return HeadWalk(heads=sorted(set(heads)), cycle=cycle)


def head_of(conn: sqlite3.Connection, doc_id: str) -> list[str]:
    """Every head reachable forward from `doc_id`, sorted by id. `[doc_id]`
    if it has no successor at all (nothing replaced it)."""
    return walk_heads(conn, doc_id).heads


@dataclasses.dataclass
class HeadInfo:
    heads: list[tuple[str, str]]  # (id, title), sorted by id — every head, not just one
    cycle: bool


def head_info(conn: sqlite3.Connection, doc_id: str) -> HeadInfo | None:
    """`doc_id`'s heads (id, title pairs) if it has been superseded, else
    None. `cycle=True` means the walk hit a loop; `heads` may still be
    non-empty (a real head reached by one branch, a cycle on another) — a
    caller that wants to report a cycle unambiguously (query.py's `(cycle)`
    annotation) should check `.cycle` first, before consulting `.heads`."""
    walk = walk_heads(conn, doc_id)
    if walk.heads == [doc_id] and not walk.cycle:
        return None
    pairs = []
    for hid in walk.heads:
        if hid == doc_id:
            continue
        row = conn.execute("SELECT title FROM documents WHERE id = ?", (hid,)).fetchone()
        pairs.append((hid, row[0] if row else ""))
    return HeadInfo(heads=sorted(pairs), cycle=walk.cycle)


def _document_row(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, title, status, date FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "title": row[1] or "", "status": row[2], "date": row[3]}


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
class TreeLine:
    id: str
    title: str
    status: str | None
    date: str | None
    depth: int  # 0 for every backward ancestor and doc_id itself; +1 per forward hop
    is_head: bool


def history_view(conn: sqlite3.Connection, doc_id: str) -> tuple[list[TreeLine], str | None]:
    """`doc_id`'s own chain for `ctx history`: everything it recursively
    absorbed (flat, oldest first, depth 0) then `doc_id` and everything
    forward of it as a DFS tree — depth increases per hop, branches ordered
    by id (`direct_successors` is already sorted), every true leaf marked
    `is_head`. A document reached only as another head's sibling (fan-in,
    not this id's own lineage) is excluded the same way `_backward_tree`
    already excludes it.

    Returns `(lines, cycle_path)`: `cycle_path` is the first cycle found,
    formatted `"a -> b -> a"`, or `None` if the graph resolved cleanly.
    Empty `lines` means `doc_id` isn't in the index at all.

    One `ids_seen` set spans BOTH the backward pass and the forward DFS: on
    a corrupt index a node can be reachable as both a predecessor and a
    successor of `doc_id` (the loop-a/loop-b cycle fixture: an id that is
    simultaneously the other's predecessor and successor renders once, not
    twice — round 2 review). Cycle DETECTION stays keyed on the per-branch
    `path` tuple, unaffected by `ids_seen`, so a node already rendered is
    still walked for its own successors rather than silently cutting the
    traversal short and missing the cycle."""
    backward_ids = _backward_tree(conn, doc_id) - {doc_id}
    backward_rows = [r for r in (_document_row(conn, i) for i in backward_ids) if r is not None]
    backward_rows.sort(key=lambda r: (r["date"] or "", r["id"]))

    lines: list[TreeLine] = []
    ids_seen: set[str] = set()
    for row in backward_rows:
        lines.append(TreeLine(row["id"], row["title"], row["status"], row["date"], 0, False))
        ids_seen.add(row["id"])

    cycle_path: list[str | None] = [None]

    def visit(current: str, path: tuple[str, ...], depth: int) -> None:
        row = _document_row(conn, current)
        successors = direct_successors(conn, current)
        live = []
        for s in successors:
            if s in path:
                if cycle_path[0] is None:
                    idx = path.index(s)
                    cycle_path[0] = " -> ".join([*path[idx:], s])
                continue
            live.append(s)
        if row is not None and current not in ids_seen:
            lines.append(
                TreeLine(row["id"], row["title"], row["status"], row["date"], depth, not successors)
            )
            ids_seen.add(current)
        # A single live successor continues the same line at the same depth
        # — a long plain chain must not turn into a staircase. Depth only
        # increases at an actual fork, one level per branch, so indentation
        # in `ctx history`'s output means "this is a branch", not "this is
        # N hops from the root".
        next_depth = depth if len(live) == 1 else depth + 1
        for nxt in live:
            visit(nxt, (*path, nxt), next_depth)

    visit(doc_id, (doc_id,), 0)
    return lines, cycle_path[0]
