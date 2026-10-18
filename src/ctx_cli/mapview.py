"""`ctx map` — a read-only browser view of what ctx knows and what one query read.

Serves one inline page (see `mappage.py`) and three JSON endpoints from the
stdlib `http.server`, bound to 127.0.0.1:

    /api/map      every document of every rig, plus its supersession links
    /api/query    one query, run through the same `search()` + `apply_budget()`
                  `ctx get` uses, with ctx's tokens beside a keyword grep's
    /api/history  the logged queries and misses from each rig's ctx.db

Nothing here writes. The index is opened the way `search()` opens it
(`mode=ro&immutable=1`), `ctx.db` with `mode=ro` (never through
`get_engine`, which creates the file), and a query is NOT logged. Unlike
`ctx get`, a stale index is not rebuilt: it is reported (`stale`) and read as
it is, because rebuilding is a write.

The grep baseline is the one measured in ctx-measurement.md: the query's
keywords (4+ letters, minus stopwords) matched over the same corpus
directories, then the top 3 files read in full; tokens are chars/4 on both
sides, the convention `apply_budget` already uses.
"""

from __future__ import annotations

import errno
import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from .corpus import (
    CONFIG_FILENAME,
    CorpusSpec,
    RigConfig,
    find_ctx_dir,
    iter_corpus_files,
    load_config,
)
from .indexer import index_is_stale
from .mappage import PAGE
from .query import (
    DEFAULT_BUDGET,
    DEFAULT_LIMIT,
    DROPPED_SHOWN,
    WEAK_SHOWN,
    Hit,
    apply_budget,
    format_dropped,
    format_weak,
    render,
    render_weak,
    search,
)

# Words that say nothing about which file is meant, for the grep baseline only
# (the list ctx-measurement.md used; `query.STOPWORDS` is ctx's own, shorter).
GREP_STOPWORDS = frozenset(
    "the and for with from that this what when where which into onto your have does "
    "after before about how page one two".split()
)
# The sweep reads a file's first lines only, as the measurement did.
GREP_READ_LINES = 2000
GREP_TOP_FILES = 3
# Newest history rows sent to the page; the strip needs days, not every row.
HISTORY_ROWS = 400


def tokens(text: str) -> int:
    """chars/4 — ctx's own convention, the same rule on both sides."""
    return len(text) // 4


def rig_label(config: RigConfig) -> str:
    home = str(Path.home())
    root = str(config.rig_root)
    if home != "/" and (root == home or root.startswith(home + "/")):
        root = "~" + root[len(home):]
    return root


def open_rigs(dirs: list[Path]) -> list[RigConfig]:
    """One config per distinct `.ctx/` found from `dirs` (two --dir values
    that walk up to the same rig are one rig)."""
    rigs: list[RigConfig] = []
    seen: set[Path] = set()
    for d in dirs:
        ctx_dir = find_ctx_dir(d)
        if ctx_dir in seen:
            continue
        seen.add(ctx_dir)
        rigs.append(load_config(ctx_dir))
    return rigs


def _ro(path: Path, immutable: bool) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro" + ("&immutable=1" if immutable else "")
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _chain_order(ids: list[str], links: list[tuple[str, str]]) -> list[str]:
    """`ids` reordered so each supersession chain is contiguous (newest first),
    keeping the incoming order otherwise — the page lays dots out in this order,
    so a chain's lines stay short."""
    nbrs: dict[str, list[str]] = {}
    for new, old in links:
        nbrs.setdefault(new, []).append(old)
        nbrs.setdefault(old, []).append(new)
    known = set(ids)
    out: list[str] = []
    done: set[str] = set()
    for start in ids:
        if start in done:
            continue
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur in done or cur not in known:
                continue
            done.add(cur)
            out.append(cur)
            stack.extend(sorted(nbrs.get(cur, []), reverse=True))
    return out


def snapshot(rigs: list[RigConfig]) -> dict:
    """Every document as a dot-to-be, grouped rig then corpus."""
    out = []
    for config in rigs:
        try:
            stale = index_is_stale(config)
        except PATH_ERRORS:  # a looping or dangling .md symlink made since the last index
            stale = True
        rig: dict = {
            "label": rig_label(config),
            "stale": stale,
            "corpora": [],
            "error": None,
        }
        out.append(rig)
        if not config.index_db_path.exists():
            rig["error"] = "no index.db (run `ctx index`)"
            continue
        try:
            conn = _ro(config.index_db_path, immutable=True)
            try:
                docs = conn.execute(
                    "SELECT id, corpus, title, status, date FROM documents ORDER BY rowid"
                ).fetchall()
                links = [
                    (r["from_id"], r["to_id"])
                    for r in conn.execute("SELECT from_id, to_id FROM supersessions")
                ]
            finally:
                conn.close()
        except (sqlite3.Error, *PATH_ERRORS) as e:
            rig["error"] = str(e)
            continue
        superseded = {old for _, old in links}
        by_corpus: dict[str, list[sqlite3.Row]] = {}
        for d in docs:
            by_corpus.setdefault(d["corpus"], []).append(d)
        names = [c.name for c in config.corpora] + [
            n for n in by_corpus if n not in {c.name for c in config.corpora}
        ]
        for name in names:
            rows = by_corpus.get(name, [])
            row_ids = {r["id"] for r in rows}
            c_links = [(a, b) for a, b in links if a in row_ids and b in row_ids]
            order = _chain_order([r["id"] for r in rows], c_links)
            by_id = {r["id"]: r for r in rows}
            rig["corpora"].append(
                {
                    "name": name,
                    "docs": [
                        {
                            "id": i,
                            "title": by_id[i]["title"] or "",
                            "status": by_id[i]["status"],
                            "date": by_id[i]["date"],
                            "current": i not in superseded
                            and by_id[i]["status"] not in ("superseded", "closed"),
                        }
                        for i in order
                    ],
                    "links": [{"from": a, "to": b} for a, b in c_links],
                }
            )
    return {"rigs": out}


def _corpus_dirs(config: RigConfig, corpus: str | None) -> list[CorpusSpec]:
    specs = [c for c in config.corpora if corpus is None or c.name == corpus]
    return specs


# What a path operation can raise on a broken tree: OSError for a missing or
# unreadable file, RuntimeError for a symlink loop (`Path.resolve()` on 3.12).
PATH_ERRORS = (OSError, RuntimeError)


def _real(path: Path) -> str | None:
    """`path` resolved, or None if it cannot be (a symlink loop)."""
    try:
        return str(path.resolve())
    except PATH_ERRORS:
        return None


def _read(path: Path | str) -> str | None:
    """A corpus file's text, or None if it cannot be read (a broken symlink, a
    permission error): one bad file must not take the whole query down."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except PATH_ERRORS:
        return None


def grep_baseline(query: str, specs: list[CorpusSpec]) -> dict:
    """What a conservative grep agent would have read for `query` over the
    corpus directories of `specs`: files containing any keyword, listed, then
    the top GREP_TOP_FILES (most keywords matched, then most matches) read in
    full. Tokens = the file listing plus those reads.

    Files are keyed by resolved real path, so a directory that appears in
    several specs (a `shared` corpus every rig lists) is swept once and its
    files counted once. Unreadable files are skipped and counted."""
    kws = [
        w
        for w in re.findall(r"[a-z0-9_]+", query.lower())
        if len(w) >= 4 and w not in GREP_STOPWORDS
    ]
    if not kws:
        return {"keywords": [], "files": 0, "tokens": 0, "top": [], "unreadable": 0}
    found: dict[str, tuple[int, int]] = {}
    unreadable = 0
    seen: set[str] = set()
    for spec in specs:
        for path in iter_corpus_files(spec):
            real = _real(path)
            if real is not None:
                if real in seen:
                    continue
                seen.add(real)
            raw = _read(real or path)
            if raw is None:
                unreadable += 1
                continue
            text = raw.lower()
            matched = sum(1 for k in kws if k in text)
            if matched:
                found[real] = (matched, sum(text.count(k) for k in kws))
    top = sorted(found, key=lambda f: (-found[f][0], -found[f][1], f))[:GREP_TOP_FILES]
    listing = "\n".join(sorted(found))
    read = "".join("\n".join((_read(f) or "").splitlines()[:GREP_READ_LINES]) for f in top)
    return {
        "keywords": kws,
        "files": len(found),
        "tokens": tokens(listing) + tokens(read),
        "top": top,
        "unreadable": unreadable,
    }


def _doc_key(config: RigConfig, h: Hit) -> str:
    """One document's identity across rigs: its resolved file path (the index
    stores it rig-relative or absolute), else its id."""
    if not h.path:
        return "id:" + h.id
    path = config.rig_root / h.path
    return _real(path) or str(path)


def _hit_json(h: Hit) -> dict:
    return {"id": h.id, "corpus": h.corpus, "title": h.title, "path": h.path}


def run_query(
    config: RigConfig,
    query: str,
    budget: int = DEFAULT_BUDGET,
    limit: int = DEFAULT_LIMIT,
    corpus: str | None = None,
    history: bool = False,
    loose: bool = False,
    sweep: bool = True,
) -> dict:
    """The body of `ctx get`, minus the index rebuild and the query log: the
    same `search()` calls, the same floor and `weak:` fallback, the same
    `apply_budget()`. `text` is exactly what `ctx get` would print."""
    decisions = [c.name for c in config.corpora if c.type == "decisions"]
    work = [c.name for c in config.corpora if c.type == "work"]

    def run(loose_: bool) -> list[Hit]:
        return search(
            config.index_db_path,
            query,
            corpus=corpus,
            history=history,
            decisions_corpora=decisions,
            work_corpora=work,
            loose=loose_,
        )

    hits = run(loose)
    weak = run(True) if not hits and not loose else []
    kept, dropped = apply_budget(hits, budget, limit=limit)
    text = render(kept, dropped) + render_weak(weak)
    # `sweep=False`: the caller (query_all) does one union sweep itself.
    grep = grep_baseline(query, _corpus_dirs(config, corpus)) if sweep else None

    def chunk(kind: str, h: Hit, text: str) -> dict:
        return {"kind": kind, "key": _doc_key(config, h), "id": h.id, "title": h.title,
                "text": text}

    # What `text` is made of, one entry per printed line, so the cross-rig
    # headline can count a document once however many rigs return it.
    chunks = [chunk("kept", h, render([h], [])) for h in kept]
    chunks += [chunk("dropped", h, format_dropped(h) + "\n") for h in dropped[:DROPPED_SHOWN]]
    if len(dropped) > DROPPED_SHOWN:
        more = f"dropped: +{len(dropped) - DROPPED_SHOWN} more\n"
        chunks.append({"kind": "more", "key": None, "text": more})
    chunks += [chunk("weak", h, format_weak(h) + "\n") for h in weak[:WEAK_SHOWN]]
    return {
        "chunks": chunks,
        "label": rig_label(config),
        "kept": [_hit_json(h) for h in kept],
        "dropped": [_hit_json(h) for h in dropped],
        "weak": [_hit_json(h) for h in weak[:WEAK_SHOWN]],
        "miss": not kept,
        "text": text,
        "ctx_tokens": tokens(text),
        "grep": grep,
    }


def _failed(config: RigConfig, error: str) -> dict:
    """A rig that could not answer: shown as a miss with the reason, so one
    broken index does not blank the whole map."""
    return {
        "label": rig_label(config),
        "error": error,
        "miss": True,
        "kept": [],
        "dropped": [],
        "weak": [],
        "text": "",
        "ctx_tokens": 0,
        "chunks": [],
        "grep": {"keywords": [], "files": 0, "tokens": 0, "top": [], "unreadable": 0},
    }


def query_all(rigs: list[RigConfig], query: str, **opts) -> dict:
    results = []
    for config in rigs:
        if not config.index_db_path.exists():
            results.append(_failed(config, "no index.db"))
            continue
        try:
            results.append(run_query(config, query, sweep=False, **opts))
        except (sqlite3.Error, *PATH_ERRORS) as e:
            results.append(_failed(config, str(e)))
    # Headline numbers are over the UNION of the rigs, never a sum of them: a
    # document several rigs return is one document, and a directory several
    # rigs list (docs/shared) is one sweep. Per-rig numbers stay in `rigs`,
    # labelled, for the small print.
    hits: list[dict] = []
    seen: dict[str, dict] = {}
    lines: list[str] = []
    for kind in ("kept", "dropped", "weak"):  # a doc kept anywhere is not also dropped
        for r in results:
            for c in r["chunks"]:
                if c["kind"] != kind:
                    continue
                if c["key"] in seen:
                    if seen[c["key"]]["kind"] == kind:
                        seen[c["key"]]["rigs"].append(r["label"])
                    continue
                entry = {"kind": kind, "key": c["key"], "id": c["id"], "title": c["title"],
                         "rigs": [r["label"]]}
                seen[c["key"]] = entry
                hits.append(entry)
                lines.append(c["text"])
    for r in results:  # "+N more" lines carry no document; same line, said once
        for c in r["chunks"]:
            if c["kind"] == "more" and c["text"] not in lines:
                lines.append(c["text"])
    # Only rigs ctx actually searched: grep over a corpus ctx never saw would
    # inflate the ratio.
    searched = [c for c, r in zip(rigs, results) if "error" not in r]
    specs = [spec for config in searched for spec in _corpus_dirs(config, opts.get("corpus"))]
    grep = grep_baseline(query, specs)
    return {
        "query": query,
        "rigs": results,
        "hits": hits,
        "ctx_tokens": tokens("".join(lines)),
        "grep_files": grep["files"],
        "grep_tokens": grep["tokens"],
        "grep_unreadable": grep["unreadable"],
        "miss": all(r["miss"] for r in results),
    }


def history_log(rigs: list[RigConfig]) -> dict:
    """The newest HISTORY_ROWS queries and misses across rigs, oldest first.
    A missing or unreadable ctx.db is an empty history, not an error."""
    queries: list[dict] = []
    misses: list[dict] = []
    for config in rigs:
        if not config.ctx_db_path.exists():
            continue
        label = rig_label(config)
        try:
            conn = _ro(config.ctx_db_path, immutable=False)
            try:
                for r in conn.execute(
                    "SELECT ts, corpus, query, budget, returned FROM queries"
                    " ORDER BY ts DESC LIMIT ?", (HISTORY_ROWS,)
                ):
                    queries.append({"rig": label, **dict(r)})
                for r in conn.execute(
                    "SELECT ts, query, note FROM misses ORDER BY ts DESC LIMIT ?", (HISTORY_ROWS,)
                ):
                    misses.append({"rig": label, **dict(r)})
            finally:
                conn.close()
        except (sqlite3.Error, *PATH_ERRORS):
            continue
    queries.sort(key=lambda r: r["ts"])
    misses.sort(key=lambda r: r["ts"])
    return {"queries": queries[-HISTORY_ROWS:], "misses": misses[-HISTORY_ROWS:]}


# Queries shown by the page's "?" panel when no rig's config.yaml lists its own.
DEFAULT_EXAMPLES = [
    {"q": "gas gauge", "note": "few files, but huge ones"},
    {"q": "custom tailor swatches fitting", "note": "one idea, found across rigs"},
    {"q": "droplet DigitalOcean timing x86 harness remote", "note": "where the test suites go"},
    {"q": "how does a new project become a rig", "note": "the question every repo has"},
    {"q": "launch TUI rewrite", "note": "a miss: ctx says it doesn't know"},
]


def examples(rigs: list[RigConfig]) -> list[dict]:
    """The "?" panel's queries: the first rig whose `.ctx/config.yaml` has
    `map: {examples: [{q: ..., note: ...}]}`, else DEFAULT_EXAMPLES. An
    unreadable or malformed list is ignored, not an error: this is a
    convenience for the stage, never a reason for the page not to load."""
    for config in rigs:
        try:
            raw = yaml.safe_load((config.ctx_dir / CONFIG_FILENAME).read_text(encoding="utf-8"))
            listed = (raw or {}).get("map", {}).get("examples")
        except (OSError, yaml.YAMLError, AttributeError):
            continue
        if not isinstance(listed, list):
            continue
        out = [
            {"q": e["q"].strip(), "note": str(e.get("note") or "")}
            for e in listed
            if isinstance(e, dict) and isinstance(e.get("q"), str) and e["q"].strip()
        ]
        if out:
            return out
    return list(DEFAULT_EXAMPLES)


def _int(params: dict, key: str, default: int) -> int:
    try:
        return int(params.get(key, [default])[0])
    except ValueError:
        return default


def make_handler(rigs: list[RigConfig]):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict, status: int = 200) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

        def do_GET(self):  # noqa: N802 (http.server's name)
            try:
                self._route()
            except Exception as e:  # a broken file or index must answer, not hang the page
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)

        def _route(self) -> None:
            url = urlparse(self.path)
            params = parse_qs(url.query)
            if url.path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif url.path == "/api/map":
                self._json(snapshot(rigs))
            elif url.path == "/api/examples":
                self._json({"examples": examples(rigs)})
            elif url.path == "/api/history":
                self._json(history_log(rigs))
            elif url.path == "/api/query":
                q = (params.get("q", [""])[0]).strip()
                if not q:
                    self._json({"error": "empty query"}, 400)
                    return
                self._json(
                    query_all(
                        rigs,
                        q,
                        budget=_int(params, "budget", DEFAULT_BUDGET),
                        limit=_int(params, "limit", DEFAULT_LIMIT),
                        corpus=(params.get("corpus", [""])[0] or None),
                        history=params.get("history", ["0"])[0] == "1",
                        loose=params.get("loose", ["0"])[0] == "1",
                    )
                )
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, format, *args):  # keep the projector terminal quiet
            pass

    return Handler


def make_server(rigs: list[RigConfig], port: int = 0) -> ThreadingHTTPServer:
    """Bound to loopback only. Port 0 picks a free one; so does a requested
    port that is already taken (a talk is no place for "address in use") —
    read the port actually bound from `server.server_address`."""
    handler = make_handler(rigs)
    try:
        return ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as e:
        if port == 0 or e.errno != errno.EADDRINUSE:
            raise
        return ThreadingHTTPServer(("127.0.0.1", 0), handler)
