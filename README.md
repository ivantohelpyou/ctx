# ctx

**Token-lean context retrieval over explainer, decision, and shared corpora.**
Markdown stays the source of truth; `ctx` builds a disposable SQLite FTS5 index
over hand-written frontmatter summaries and returns a budgeted, deterministic
result set instead of a session grepping its way through a few hundred files.

Built from the memo and plan at `~/gt/docs/plans/ctx-context-retrieval.md` /
`ctx-memo.md` (hq-jdeh). No embeddings, no MCP server, no daemon: a CLI that
costs nothing until called.

## What it holds

Two SQLite files, per rig, under `.ctx/` (gitignored):

- **`index.db`** — disposable. `rm .ctx/index.db && ctx index` reconstructs it
  from the corpus frontmatter; the query *output* is byte-for-byte the same,
  every time, for the same query against the same source files.
- **`ctx.db`** — durable. The query log and the miss log (queries that
  returned nothing), built with SQLAlchemy models in `src/ctx_cli/ctxdb/`
  (see `.sea/NOTES.md` for what the `build-with-sea` skill did and didn't do
  this pass).

Corpora are configured per rig in `.ctx/config.yaml`:

```yaml
corpora:
  explainers:
    type: explainers
    path: docs/explainers
  shared:
    type: shared
    path: /home/ivanadamin/gt/docs/shared
```

Frontmatter contract (all corpora): `id`, `title`, `summary` (hand-written,
required — never auto-generate it), `tags`, `terms` (not `aliases:` — that key
collides with Hugo redirect paths in the research rig), `date`.
Corpus-specific additions and lint rules are in
`~/gt/docs/plans/ctx-context-retrieval.md` §Schema.

### Corpora with someone else's frontmatter keys (`type: hugo`, or `map:`)

A corpus that already has frontmatter — for another consumer, in another
schema — doesn't need its files rewritten. A per-corpus `map:` (target key
→ source key) tells the indexer/linter to read the source key as ctx's own,
in memory only; the file on disk, and whatever else reads it, is untouched.
`type: hugo` is a preset for research's `docs/` (Hugo reads `description`,
`keywords`, `aliases`, `date` and must keep doing so):

```yaml
corpora:
  docs:
    type: hugo          # preset map: {summary: description, tags: keywords}
    path: docs           # + derive_id: true (id defaults to the path stem)
```

`aliases:` is deliberately left out of the preset — it's Hugo's URL-redirect
list, not search vocabulary, and must never be read as `terms:`. `terms:` is
a real ctx key and may still be hand-added to a Hugo file; Hugo ignores
frontmatter keys it doesn't know. Any corpus type may set `map:` and
`derive_id:` explicitly, not just `type: hugo`:

```yaml
corpora:
  docs:
    type: shared
    path: docs
    map: {summary: description, tags: keywords}
    derive_id: true
```

## Quick start

```bash
uv tool install .              # → ctx
cd some-rig
ctx init                       # writes .ctx/config.yaml, .ctx/ROUTING.md, gitignores .ctx/
# edit .ctx/config.yaml to point `explainers:` at the real docs directory
ctx index                      # build .ctx/index.db
ctx get "why did we pick FTS5" --budget 1200
ctx lint                       # frontmatter + supersession integrity, covers coverage %
ctx checkpoint "indexed explainers; next: point research corpus at Hugo docs"
```

`ctx --help` lists every command and flag.

## Output contract

```
id — title — path#slug — summary
...
dropped: id — title
...
```

`ctx get` never scans document bodies at query time — only the frontmatter
index. Read the one or two files it names.

## Design decisions that depart from a naive read of the memo

- `terms:` not `aliases:` (Hugo collision, see plan).
- No git hook. `ctx get` compares a sha256 digest of every corpus file's
  `(path, size, mtime_ns)` against the digest recorded at the last `ctx
  index` and reindexes lazily if it differs; `bd` owns the pre-commit/
  post-merge hooks in every rig. The one blind spot a digest can't close:
  a content edit that preserves both size and mtime exactly (only reachable
  via a deliberate `os.utime` restoring the original timestamp after an
  in-place edit of identical length) — if you ever do that, run `ctx index`
  by hand.
- Two database files, not one, because "disposable" and "keeps a durable query
  log" cannot both be true of a single file.
- `decisions` corpus frontmatter contract + lint ship now; the supersession
  *walker* used by `ctx get --history` is deferred to when a rig has ten or
  more decision documents (Phase 3 of the plan).

## Tests

```bash
uv run pytest
```

Covers: frontmatter parsing, index build, budgeted `ctx get` output, lint rules
(explainer staleness/coverage, decision supersession/cycles, shared byte
ceiling), checkpoint append, and byte-identity of `ctx get` output across a
`rm index.db && ctx index` cycle.
