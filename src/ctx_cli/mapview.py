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

import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .corpus import CorpusSpec, RigConfig, find_ctx_dir, iter_corpus_files, load_config
from .indexer import index_is_stale
from .mappage import PAGE
from .query import (
    DEFAULT_BUDGET,
    DEFAULT_LIMIT,
    WEAK_SHOWN,
    Hit,
    apply_budget,
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
        rig: dict = {
            "label": rig_label(config),
            "stale": index_is_stale(config),
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
        except sqlite3.Error as e:
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


def _lines(path: str) -> list[str]:
    return Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()


def grep_baseline(query: str, specs: list[CorpusSpec]) -> dict:
    """What a conservative grep agent would have read for `query` over the
    same corpus directories: files containing any keyword, listed, then the
    top GREP_TOP_FILES (most keywords matched, then most matches) read in
    full. Tokens = the file listing plus those reads."""
    kws = [
        w
        for w in re.findall(r"[a-z0-9_]+", query.lower())
        if len(w) >= 4 and w not in GREP_STOPWORDS
    ]
    if not kws:
        return {"keywords": [], "files": 0, "tokens": 0, "top": []}
    found: dict[str, tuple[int, int]] = {}
    for spec in specs:
        for path in iter_corpus_files(spec):
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            matched = sum(1 for k in kws if k in text)
            if matched:
                found[str(path)] = (matched, sum(text.count(k) for k in kws))
    top = sorted(found, key=lambda f: (-found[f][0], -found[f][1], f))[:GREP_TOP_FILES]
    listing = "\n".join(sorted(found))
    read = "".join("\n".join(_lines(f)[:GREP_READ_LINES]) for f in top)
    return {
        "keywords": kws,
        "files": len(found),
        "tokens": tokens(listing) + tokens(read),
        "top": top,
    }


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
    grep = grep_baseline(query, _corpus_dirs(config, corpus))
    return {
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
        "grep": {"keywords": [], "files": 0, "tokens": 0, "top": []},
    }


def query_all(rigs: list[RigConfig], query: str, **opts) -> dict:
    results = []
    for config in rigs:
        if not config.index_db_path.exists():
            results.append(_failed(config, "no index.db"))
            continue
        try:
            results.append(run_query(config, query, **opts))
        except sqlite3.Error as e:
            results.append(_failed(config, str(e)))
    return {
        "query": query,
        "rigs": results,
        "ctx_tokens": sum(r["ctx_tokens"] for r in results),
        "grep_files": sum(r["grep"]["files"] for r in results),
        "grep_tokens": sum(r["grep"]["tokens"] for r in results),
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
        except sqlite3.Error:
            continue
    queries.sort(key=lambda r: r["ts"])
    misses.sort(key=lambda r: r["ts"])
    return {"queries": queries[-HISTORY_ROWS:], "misses": misses[-HISTORY_ROWS:]}


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
            url = urlparse(self.path)
            params = parse_qs(url.query)
            if url.path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif url.path == "/api/map":
                self._json(snapshot(rigs))
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
    """Bound to loopback only; port 0 picks a free one."""
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(rigs))
