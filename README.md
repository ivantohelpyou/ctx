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

`map:` is validated at load time: an unknown target (a typo like `summry:`
instead of `summary:`) is a config error naming the corpus and the bad key,
not a silently-empty field; mapping `aliases:` into `tags:`/`terms:` is a
config error for the same reason `aliases:` is left out of the hugo preset;
the same target key twice under one corpus's `map:` is a config error too
(most likely a copy-paste that meant two different targets), rather than
YAML's usual last-one-silently-wins.

**Precedence: the hand-authored key always wins.** If a document already has
its own `summary:` (or `tags:`, `id:`, ...) — not empty — the map never
overwrites it with the mapped source; it only fills a target the author left
absent. A document that hand-writes `summary:` alongside Hugo's
`description:` keeps the hand-written one.

**`tags:`/`terms:` accept a comma-separated string, not just a YAML list** —
Hugo's `keywords: "a, b, c"` and `keywords: ["a", "b", "c"]` index to the
same tags either way (other list-shaped fields, like `covers:`, still treat
a bare string as one value — a comma there could be part of a real path).

**`type: hugo` skips drafts by default** — a document with a truthy `draft:`
is excluded from indexing and lint's contract checks, matching what Hugo
itself publishes; `ctx lint` reports how many were skipped, per corpus. Set
`include_drafts: true` on the corpus to index them anyway.

**`date:`/`last-verified:` normalize to `YYYY-MM-DD`** in the index, however
they're written in frontmatter — a bare date, a full ISO datetime (its local
calendar date, kept as written, never shifted for timezone), or a string in
either shape. A value that doesn't parse as any of those is a `ctx lint`
error, not a silently-malformed index value.

### `exclude:` — files in a corpus directory that aren't documents

ctx otherwise treats every `*.md` under a corpus's `path` as a document
`ctx lint` requires `id`/`title`/`summary` on. A README explaining what a
directory holds (a generated-export's "do not edit" note, for one) isn't a
decision or an explainer, so it's excluded by default, everywhere:

```yaml
corpora:
  decisions:
    type: decisions
    path: docs/decisions
    exclude: ["README.md", "drafts/*.md"]   # default is just ["README.md"]
```

Globs are relative to the corpus's own `path`. An excluded file is invisible
to indexing, lint, and the staleness fingerprint alike — all three read the
same file list. `exclude: []` un-hides `README.md` for a corpus that
genuinely wants it treated as a document.

## Quick start

```bash
uv tool install .              # → ctx
cd some-rig
ctx init                       # writes .ctx/config.yaml, .ctx/ROUTING.md, gitignores .ctx/
# edit .ctx/config.yaml to point `explainers:` at the real docs directory
ctx index                      # build .ctx/index.db
ctx get "why did we pick FTS5" --budget 1200
ctx history adr-002            # a decision's supersession chain, oldest to newest
ctx lint                       # frontmatter + supersession integrity, covers coverage %
ctx checkpoint "indexed explainers; next: point research corpus at Hugo docs"
```

`ctx --help` lists every command and flag.

## Output contract

```
id — title — path#slug — summary
  superseded-by: id — title      (only with --history, only if superseded)
...
dropped: id — title
...
```

`ctx get` never scans document bodies at query time — only the frontmatter
index. Read the one or two files it names.

## Supersession: the walker and `--history`

`decisions` corpus documents form a graph via `supersedes:`/`superseded-by:`
(`ctx index` writes it into `index.db`'s `supersessions(from_id, to_id)`
table, `from_id` the newer document). `ctx get` filters on it by default —
acceptance criterion 2 of the plan: **a superseded decision is never
returned without `--history`.** The filter runs in the SQL `WHERE` clause,
before ranking, so a superseded document never consumes budget either; it
isn't ranked-then-dropped, it's never a candidate.

With `--history`, superseded documents are included, each annotated with the
walker's answer to "what replaced this, ultimately" — the HEAD of its chain,
not just its direct successor, so reading one line never leaves you one hop
short of what's current:

```
adr-001 — Two database files, not one — docs/decisions/adr-001....md#... — ...
  superseded-by: adr-002 — Two database files, built with SEA
```

`ctx history <id>` prints the same graph the other way: the full chain
connected to `<id>` (successors and predecessors, transitively), oldest to
newest by `date`, marking whichever entry has no successor of its own as the
current head:

```
$ ctx history adr-001
adr-001 — superseded — 2026-09-01 — Two database files, not one
adr-002 — accepted — 2026-09-05 — Two database files, built with SEA  <- current
```

Both walks (`history.py`) are visited-set bounded: a cycle or a dangling id
in a hand-edited or corrupt `index.db` stops the walk instead of looping or
raising — `ctx lint` is what rejects cycles and unreciprocated links when
they can still be fixed at the source (`ctx index` time); the walker's job
is to not crash on an index that got past it anyway.

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
- The supersession walker and `ctx get --history`'s chained annotation ship
  in Phase 3, against the real decisions corpus (`strategy export --ctx`,
  hq-sl5k) — see "Supersession" above.

## Tests

```bash
uv run pytest
```

Covers: frontmatter parsing, index build, budgeted `ctx get` output, lint rules
(explainer staleness/coverage, decision supersession/cycles, shared byte
ceiling), checkpoint append, and byte-identity of `ctx get` output across a
`rm index.db && ctx index` cycle. Also: `map:` validation (unknown target,
`aliases:` into tags/terms, duplicate target key), hand-authored-key
precedence, tags/terms comma-splitting, `type: hugo` draft-skipping,
`date`/`last-verified` normalization across date/datetime/string input, the
supersession walker (successors/predecessors, cycle/dangling-id safety),
`ctx get --history`'s chained annotation and its budget accounting, `ctx
history`, and per-corpus `exclude:`.
