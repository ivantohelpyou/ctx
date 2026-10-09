# ctx

**Token-lean context retrieval over explainer, decision, and shared corpora.**
Markdown stays the source of truth; `ctx` builds a disposable SQLite FTS5 index
over hand-written frontmatter summaries and returns a budgeted, deterministic
result set instead of a session grepping its way through a few hundred files.

Built from the memo and plan at `~/gt/docs/plans/ctx-context-retrieval.md` /
`ctx-memo.md` (hq-jdeh). No embeddings, no MCP server, no daemon: a CLI that
costs nothing until called.

## What it holds

Two SQLite files, per rig, under `.ctx/`:

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
    path: ~/gt/docs/shared
```

**`path:` is expanded at load time** — `~` (`expanduser`) and `$VAR`/`${VAR}`
(`expandvars`, applied first) both work, and a relative path is resolved
against the rig root. Write the cross-rig `shared` corpus as `~/gt/docs/shared`
in a *committed* config, never as a resolved absolute path
(`/home/<you>/gt/docs/shared`) — the literal form only works on the machine
and user it was written for, and a corpus whose resolved `path` isn't a
directory returns zero documents forever with no signal on its own (see
below for how `ctx index`/`ctx lint` surface that instead).

### What to commit

`.ctx/config.yaml` and `.ctx/ROUTING.md` are the routing surface a rig's
`CLAUDE.md` points at (never inlines) — they must exist in a fresh clone with
**no** `ctx init` run, so both are **tracked**, not gitignored. Everything
else under `.ctx/` is disposable or durable-but-local state, and stays
ignored. A rig's `.gitignore` gets these lines (`ctx init` writes them,
narrower than a blanket `.ctx/`, so it never re-ignores the two tracked
files):

```gitignore
.ctx/index.db*
.ctx/ctx.db*
.ctx/checkpoints/
```

| path | tracked? | why |
|---|---|---|
| `.ctx/config.yaml` | yes | corpus config; must survive a fresh clone |
| `.ctx/ROUTING.md` | yes | what `CLAUDE.md` points at; must survive a fresh clone |
| `.ctx/index.db*` | no | disposable — `ctx index` reconstructs it byte-for-byte |
| `.ctx/ctx.db*` | no | durable but local — a query/miss log, not shared state |
| `.ctx/checkpoints/` | no | session handoff notes, local to whoever's working |

`ctx init` never overwrites an existing `config.yaml` or `ROUTING.md` — safe
to run in a fresh clone that already has both tracked (exits 0, "exists,
left alone"); it only fills in what's actually missing. `ctx init --preset
explainers|hugo|work|mayor` writes a config shaped for that corpus instead of
the generic `explainers`-guess default — see `_PRESET_CORPUS` in
`src/ctx_cli/corpus.py` for what each preset writes; the `shared` path it
writes is the portable `~/gt/docs/shared` form, not a resolved absolute path.

**A corpus whose resolved `path` isn't a directory** (typo, wrong-machine
absolute path, corpus not yet checked out) is a **`ctx lint` ERROR** naming
the corpus and the path — worse than an empty corpus, since `iter_corpus_files`
would otherwise return nothing with no signal at all. `ctx index` (and the
lazy reindex `ctx get`/`ctx history` trigger) print the same fact as a
one-line stderr **warning** instead — exit 0, every other corpus still
indexes; only `ctx lint`, or `ctx index --strict` on an unrelated decisions
problem, ever refuses to publish.

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

### `type: work` — beads-as-markdown (sw-5om, Phase 5b pilot)

A fourth corpus type, for a rig piloting `bd`-as-markdown: one file per work
item (`speakeasy/work/<id>.md` for the pilot rig), written by a `bd`-shaped
shim instead of `bd` itself. Frontmatter contract: `id`, `title`, `summary`,
`status`, `priority`, `type`, `deps`, `closed-reason`, `created`, `closed`,
`tags`, `terms`.

```yaml
corpora:
  work:
    type: work
    path: work
```

`ctx lint` on a `work` corpus additionally requires `status`/`priority`/
`type`/`created`; `status` must be one of `open`, `in_progress`, `blocked`,
`closed`; a `closed` doc must also carry `closed`/`closed-reason`; every
`deps` id must resolve to another document in the same corpus.

**`ctx get` excludes `closed` docs by default**, same as a superseded
decision — `--history` brings them back (no separate `--all` flag; the
existing "include non-current" flag already says what this means). Unlike
`decisions`, there's no supersession graph and no walker: a closed bead
isn't superseded by anything, so `--history` never adds an annotation line
for a `work` hit, just includes it.

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
ctx init [--preset explainers|hugo|work|mayor]   # writes config.yaml + ROUTING.md
                                # (tracked), gitignores index.db/ctx.db/checkpoints/
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
  superseded-by: id — title[; id — title ...]   (--history, if superseded)
  superseded-by: (cycle)                         (--history, if the graph loops)
...
dropped: id — title          (at most 5 lines)
dropped: +N more             (if more were cut)
```

`ctx get` never scans document bodies at query time — only the frontmatter
index. Read the one or two files it names.

**Relevance floor.** Stopwords (`in`, `the`, `how`, …) are left out of the
match, and a hit must match at least two of the query's remaining words, or
the only one if there is just one. A query whose only matches are one-word
coincidences returns nothing and is logged as a miss, so the miss log shows
the gap instead of five confident wrong answers. `--loose` turns the floor off
(any hit matching any one word). Measured on 19 real logged queries
(2026-10-09): every noise answer became a miss, every good top hit was kept.

## Supersession: the walker and `--history`

`decisions` corpus documents form a graph via `supersedes:`/`superseded-by:`
(`ctx index` writes it into `index.db`'s `supersessions(from_id, to_id)`
table, `from_id` the newer document). **The graph is the truth, not the
`status` column** — acceptance criterion 2 of the plan ("a superseded
decision is never returned without `--history`") has to hold even for a
document that omitted `status:` entirely (indexes as NULL, which would
otherwise pass a status-only filter) or carries a stale one. So `ctx get`'s
default filter excludes a document if EITHER its status says non-current,
OR it has any successor edge at all in `supersessions`, whatever its status
says — both checked in the SQL `WHERE` clause, before ranking, so an
excluded document never consumes budget; it isn't ranked-then-dropped, it's
never a candidate.

With `--history`, superseded documents are included, each annotated with the
walker's answer to "what replaced this, ultimately" — every HEAD of its
chain, not just a direct successor. Plural on purpose: `ctx lint` permits a
document to be superseded by more than one other (a fork is valid data, not
corruption), so reading one line must never silently drop a sibling:

```
adr-001 — Two database files, not one — docs/decisions/adr-001....md#... — ...
  superseded-by: adr-002 — Two database files, built with SEA
```

`ctx history <id>` prints `<id>`'s own chain — what it recursively absorbed
(flat, oldest first) then `<id>` and everything forward of it, indented one
level per fork, every current head marked:

```
$ ctx history adr-001
adr-001 — superseded — 2026-09-01 — Two database files, not one
adr-002 — accepted — 2026-09-05 — Two database files, built with SEA  <- current
```

A fork prints as nested branches (`split-x`/`split-y` both directly
supersede `split-0`):

```
$ ctx history split-0
split-0 — superseded — 2026-01-01 — Split point
  split-x — accepted — 2026-02-01 — One branch  <- current
  split-y — accepted — 2026-02-02 — The other branch  <- current
```

**Cycles are reported, never silent.** Both walks (`history.py`) are
path-visited-set bounded, so a cycle in a hand-edited or corrupt `index.db`
always terminates — but termination alone used to look like success (exit
0, no head). Now: `ctx history` prints what it found, then
`cycle detected: a -> b -> a — index is corrupt, run ctx lint` on stderr and
exits 2; `ctx get --history` annotates the hit `superseded-by: (cycle)` and
warns once on stderr. `ctx lint` is what rejects cycles and unreciprocated
links when they can still be fixed at the source (`ctx index` time); the
walker's job is to fail loudly on an index that got past it anyway, not to
resolve it.

**`ctx index` runs the decisions structural checks on every build** —
reciprocity, cycles, an `accepted` doc something else claims to supersede —
the cheap subset of `ctx lint` that most affects whether `--history`'s
output means anything. By default it warns on stderr and still publishes
(`N decisions lint errors — run ctx lint`, exit 0); `ctx index --strict`
refuses to publish instead (exit 1, `index.db` left exactly as it was) —
the existing atomic build (temp file, renamed only on success) already
makes "don't publish" as simple as not calling it.

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
  hq-sl5k) — see "Supersession" above, including the round-2 review fixes:
  graph-based (not status-based) exclusion, fan-out, and cycle reporting.

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
supersession walker (successors/predecessors, fan-out/fan-in, cycle
detection and its exit codes/warnings), `ctx get --history`'s graph-based
exclusion and chained annotation (including the no-status-but-superseded
case) and its budget accounting, `ctx history`'s indented tree output,
`ctx index --strict`, per-corpus `exclude:`, and `type: work`'s lint rules
(status enum, closed requires closed-reason, deps existence) and its
`--history`-gated closed-doc exclusion (sw-5om).
