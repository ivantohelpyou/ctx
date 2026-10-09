"""`ctx get` — two-stage retrieval: FTS5 over frontmatter, budgeted output.

Output contract (must be deterministic — same query + same index => same bytes):

    id — title — path#slug — summary
      superseded-by: id — title[; id — title ...]   (--history, if superseded)
      superseded-by: (cycle)                         (--history, if the graph loops)
    ...
    dropped: id — title          (at most DROPPED_SHOWN lines)
    dropped: +N more             (if more were cut)

or, when the relevance floor leaves nothing:

    weak: id — title — path#slug (at most WEAK_SHOWN lines; logged as a miss)

A hit must match at least `min_terms_required()` of the query's content terms
(stopwords removed) or it is not a result at all — it is neither kept nor
dropped, so a query whose only matches are one-word coincidences returns
nothing and is logged as a miss (hq-mb81). `loose=True` restores plain OR.
When the floor leaves nothing, the best one-word matches are shown as `weak:`
lines — a lead, labelled as such, never an answer (`render_weak`).

Bodies are never read at query time; only the index table and FTS5 built from
frontmatter are consulted.
"""

from __future__ import annotations

import dataclasses
import re
import sqlite3
from pathlib import Path

from .history import head_info
from .indexer import FTS_TOKENIZE

# title(10), summary(1), terms(5), tags(1) — matches column order in the FTS5
# table so title and terms outrank a body-shaped summary hit.
BM25_WEIGHTS = (10.0, 1.0, 5.0, 1.0)
DEFAULT_BUDGET = 1500
# Hard cap on returned entries regardless of budget (acceptance: "typical
# query... ≤5 entries"). `--limit` overrides. Without this, a query with many
# small hits and a generous budget can return dozens of entries.
DEFAULT_LIMIT = 5
# `dropped:` lines shown before collapsing the rest into one `+N more` line.
# Uncapped, a broad query on a large corpus printed 100+ dropped lines and
# ran 2-3x over its budget (hq-x0ra: 105 lines on one sea query).
DROPPED_SHOWN = 5
# `weak:` lines shown when the floor leaves no result. A synonym list ("facets
# categories collections") can have good documents that each match one word;
# a bare miss would hide them, five unlabelled hits would be the old noise.
WEAK_SHOWN = 3

# Words that say nothing about which document is meant. OR-ing them in let
# almost any document match ("heads in beds" matched on "in"); counting them
# toward coverage would let a coincidence clear the floor (hq-mb81).
STOPWORDS = frozenset(
    "a an and are as at be by for from how i in into is it of on or our the "
    "this that to was we what when where which who why with you your".split()
)


@dataclasses.dataclass
class Hit:
    id: str
    corpus: str
    path: str
    title: str
    summary: str
    status: str | None
    # Every head reached from this hit — plural because a fork is valid data
    # (lint allows one doc superseded by more than one other), not just its
    # first successor. Only ever populated with --history; walker output,
    # never a raw column.
    superseded_by: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    # True if the walk found a successor edge back onto its own path — a
    # corrupt or hand-edited index, not a data shape `ctx lint` permits.
    cycle: bool = False


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return slug or "doc"


def _quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _fts_tokens(words: list[str]) -> list[tuple[str, ...]]:
    """Each word's tokens exactly as the index tokenizes them (FTS_TOKENIZE:
    porter over unicode61), read back through fts5vocab from a throwaway
    in-memory table. A word's identity is its stemmed tokens, not its
    spelling: "head" and "heads" are one term, and "_" or "--" is none
    (hq-mb81 review, round 1)."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(f"CREATE VIRTUAL TABLE t USING fts5(x, tokenize='{FTS_TOKENIZE}')")
        conn.execute("CREATE VIRTUAL TABLE v USING fts5vocab(t, 'instance')")
        out = []
        for i, w in enumerate(words, start=1):
            conn.execute("INSERT INTO t(rowid, x) VALUES (?, ?)", (i, w))
            toks = conn.execute("SELECT term FROM v WHERE doc = ? ORDER BY offset", (i,))
            out.append(tuple(t for (t,) in toks))
        return out
    finally:
        conn.close()


def query_terms(query: str) -> list[str]:
    """The query's content terms, in first-occurrence order: whitespace-split,
    stopwords removed, words the tokenizer reduces to nothing dropped, and
    words with the same stemmed tokens collapsed to their first spelling. If
    every word is a stopword, the stopwords are the terms — a query is never
    reduced to nothing by the stopword filter."""
    words = [t for t in re.split(r"\s+", query.strip()) if t]
    seen: set[tuple[str, ...]] = set()
    terms: list[str] = []
    stop: list[bool] = []
    for w, toks in zip(words, _fts_tokens(words)):
        if not toks or toks in seen:
            continue
        seen.add(toks)
        terms.append(w)
        stop.append(re.sub(r"[^0-9a-z]", "", w.lower()) in STOPWORDS)
    content = [t for t, is_stop in zip(terms, stop) if not is_stop]
    return content or terms


def min_terms_required(n_terms: int) -> int:
    """How many distinct content terms a hit must match: all of them for a
    one-term query, otherwise two. Two is the smallest number that stops a
    single stemmed coincidence ("heads" -> "head") from carrying a hit, while a
    long, wordy query is not held to matching most of its words."""
    return min(2, n_terms)


def _fts_match_expr(query: str) -> str:
    """Turn free text into a permissive FTS5 MATCH expression: each content
    term quoted (escapes FTS5 operator characters) and OR'd together, so
    Porter stemming and partial-term matches both apply. Stopwords are left
    out (see `query_terms`)."""
    terms = query_terms(query)
    if not terms:
        return '""'
    return " OR ".join(_quote(t) for t in terms)


def format_hit(hit: Hit) -> str:
    return f"{hit.id} — {hit.title} — {hit.path}#{_slugify(hit.title)} — {hit.summary}"


def format_superseded_by(hit: Hit) -> str | None:
    """The walker's one-line annotation on a `--history` hit that has been
    superseded: every HEAD of its chain (plural — a fork is valid data), not
    just a direct successor — reading `id — title` alone should never leave
    you one hop short of what's actually current, or silently missing a
    sibling fork. A cycle takes precedence over any heads also found (a
    corrupt index is the more important thing to say)."""
    if hit.cycle:
        return "  superseded-by: (cycle)"
    if not hit.superseded_by:
        return None
    parts = "; ".join(f"{head_id} — {head_title}" for head_id, head_title in hit.superseded_by)
    return f"  superseded-by: {parts}"


def format_weak(hit: Hit) -> str:
    return f"weak: {hit.id} — {hit.title} — {hit.path}#{_slugify(hit.title)}"


def render_weak(weak: list[Hit]) -> str:
    """Up to WEAK_SHOWN below-floor hits, in rank order, one line each — no
    summary, so a lead costs a line, not a paragraph."""
    lines = [format_weak(h) for h in weak[:WEAK_SHOWN]]
    return "\n".join(lines) + ("\n" if lines else "")


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


def _apply_term_floor(conn: sqlite3.Connection, rows: list, terms: list[str]) -> list:
    """Keep only rows matching at least `min_terms_required(len(terms))`
    distinct terms. Coverage is measured with the same FTS5 index and
    tokenizer as the ranking query (one MATCH per term, so stemming agrees),
    and rank order is preserved — the floor only removes rows."""
    need = min_terms_required(len(terms))
    if need <= 1 or not rows:
        return rows
    coverage: dict[int, int] = {}
    for term in terms:
        for (rid,) in conn.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?", (_quote(term),)
        ):
            coverage[rid] = coverage.get(rid, 0) + 1
    return [r for r in rows if coverage.get(r["rid"], 0) >= need]


def search(
    index_db_path: Path,
    query: str,
    corpus: str | None = None,
    history: bool = False,
    decisions_corpora: list[str] | None = None,
    work_corpora: list[str] | None = None,
    loose: bool = False,
) -> list[Hit]:
    """Run the FTS5 query, status-filtered before ranking, ranked by BM25.

    `decisions_corpora` is the list of corpus NAMES configured with
    `type: decisions` (the caller reads this from `RigConfig`, not this
    module — `search()` only ever opens `index.db`). Lifecycle filtering
    (status, the supersession graph) applies only to those corpora: an
    explainer or shared doc has no lifecycle, and REGRESSED into vanishing
    from default results (review finding, hq-sl5k round 2 — reproduced)
    when a stray `supersedes:` in its own frontmatter, or in another doc's,
    put it in `supersessions` at all. `None`/`[]` means no corpus in this
    rig is type `decisions`, so nothing is lifecycle-filtered.

    `work_corpora` (sw-5om, ctx Phase 5b) is the analogous list for
    `type: work` corpora: a closed bead has no supersession graph, so the
    rule is simpler than decisions' — `status = 'closed'` is excluded by
    default, same as a superseded decision, and `--history` (reused rather
    than adding a separate `--all` flag; plan §5b) brings it back. No
    walker, no annotation: a closed bead isn't superseded by anything, it's
    just done.

    Opens `index.db` read-only: a query never writes to it, so a read-only
    `.ctx/` directory (or a read-only filesystem) must not stop `ctx get` from
    working."""
    uri = index_db_path.resolve().as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        match_expr = _fts_match_expr(query)
        sql = (
            "SELECT documents.rowid AS rid, documents.id, documents.corpus, "
            "documents.path, documents.title, documents.summary, documents.status, "
            "bm25(documents_fts, ?, ?, ?, ?) AS rank "
            "FROM documents_fts JOIN documents ON documents.rowid = documents_fts.rowid "
            "WHERE documents_fts MATCH ?"
        )
        params: list = [*BM25_WEIGHTS, match_expr]
        if corpus:
            sql += " AND documents.corpus = ?"
            params.append(corpus)
        if not history and decisions_corpora:
            # Status filter before ranking (memo: "status beats score"),
            # PLUS the graph (the GRAPH is the truth, not just the status
            # column — a decisions doc that omits `status:` entirely indexes
            # NULL and would otherwise pass through even when `supersessions`
            # already says something replaced it; acceptance 2 broke on
            # exactly that). Both only ever apply to a `type: decisions`
            # corpus — an explainer or shared doc is kept regardless of its
            # own status/graph membership, which is the "or" branch below.
            placeholders = ", ".join(["?"] * len(decisions_corpora))
            sql += (
                f" AND (documents.corpus NOT IN ({placeholders})"
                " OR ("
                "   (documents.status IS NULL OR documents.status = 'accepted')"
                "   AND NOT EXISTS ("
                "     SELECT 1 FROM supersessions s WHERE s.to_id = documents.id"
                "   )"
                " ))"
            )
            params.extend(decisions_corpora)
        if not history and work_corpora:
            # Same shape as the decisions clause above, minus the graph: a
            # work-corpus doc has no supersession chain, only a status a
            # closed bead sets and never contests.
            placeholders = ", ".join(["?"] * len(work_corpora))
            sql += (
                f" AND (documents.corpus NOT IN ({placeholders})"
                " OR (documents.status IS NULL OR documents.status != 'closed'))"
            )
            params.extend(work_corpora)
        sql += " ORDER BY rank ASC, documents.id ASC"
        rows = conn.execute(sql, params).fetchall()
        if not loose:
            rows = _apply_term_floor(conn, rows, query_terms(query))
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
            # Same scoping as the exclusion above: a non-decisions hit never
            # gets a lifecycle annotation, even if a stray graph row exists.
            decisions_set = set(decisions_corpora or [])
            for h in hits:
                if decisions_set and h.corpus not in decisions_set:
                    continue
                info = head_info(conn, h.id)
                if info is not None:
                    h.superseded_by = info.heads
                    h.cycle = info.cycle
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
    lines += [format_dropped(h) for h in dropped[:DROPPED_SHOWN]]
    if len(dropped) > DROPPED_SHOWN:
        lines.append(f"dropped: +{len(dropped) - DROPPED_SHOWN} more")
    return "\n".join(lines) + ("\n" if lines else "")
