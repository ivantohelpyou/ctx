# ctx

**A small command-line tool that answers your coding agent's "where did we decide that?" from
your project's own notes, in a few hundred tokens instead of a grep sweep.**

When an AI coding agent (Claude Code, Codex, Cursor's agent, …) needs context it doesn't have
(why the database is split in two, what the rule is for reviews, what was tried last month), it
usually greps the repo and reads whole files until it finds something. That works, and it is
expensive: every file it reads stays in the session's context and is paid for again on every
later turn.

ctx gives the agent one command to ask first:

```
$ ctx get "two databases"
adr-002 — Two database files, built with SEA — docs/decisions/adr-002-two-databases-sea.md#two-database-files-built-with-sea — Same split as adr-001, plus the durable half's models.py goes through build-with-sea.
```

One line per hit: id, title, file and heading, and a one-sentence summary that a person wrote.
The agent then opens only the file it needs. If nothing in your notes answers the question, ctx
says so (and logs the question), instead of returning five confident wrong answers.

- **No embeddings, no vector database, no server, no daemon.** A CLI and two SQLite files.
- **Your markdown stays the source of truth.** The index is disposable: delete it and rebuild it,
  and every answer comes back byte-for-byte identical.
- **Token-budgeted.** `--budget 800` fills to about 800 tokens with the best hits, then lists
  what it dropped.
- **Misses are data.** Every question that found nothing is logged: that's the list of notes to
  write next, and the agent that hit the miss is usually the one that writes the note.

## Does it help? Measured

On **19 real questions** agents had already asked across 7 repos (the most recent four per repo,
not hand-picked), compared with a deliberately conservative grep agent that greps the same
directories and reads the top 3 matching files:

| | ctx | grep and read |
|---|---:|---:|
| Tokens, 17 answered questions | **21,185** | **304,611** |
| Median per question | ~900 | ~5,800 |
| Median ratio | | **~9× more** |

The gap grows with the size of the notes: about 3× on a 6k-token corpus, about 10× on 551k, and
about 110× on 12M. Tokens are counted as characters/4 on both sides.

**The honest part.** That first run showed ctx's top hit was right only about half the time; the
rest was confident noise, because it always filled its slots. Two fixes followed, both measured
on the same 19 questions:

1. **A relevance floor.** A hit must match at least two of the question's words. Every noise
   answer became a logged miss, and every good answer was kept. The floor cost some recall
   (a list of synonyms can have good documents that each match one word), so those now come
   back as up to three lines labelled `weak:`, leads rather than answers.
2. **A real budget.** The "dropped" list is capped, so answers on the largest corpus went from
   2.5–4.3k tokens to 0.85–1.35k on a 1,500 budget: **25–43× smaller than grep-and-read** there.

## How it works

Your repo keeps short markdown notes, each starting with frontmatter that includes a
**one-sentence summary written at the time the thing was decided**. Who writes them is whoever
did the work. In a repo run by coding agents that is the agent, at the end of the task, as part
of "done"; a person reviews the note, not the index. Nothing here is typed after the fact:

```markdown
---
id: exp-001
title: Why FTS5 over a vector index
summary: SQLite FTS5 with Porter stemming and BM25 is enough at a few hundred docs; no embeddings in v1.
tags: [search, sqlite]
terms: [fts5, bm25, full-text search]
date: 2026-09-01
---

(the full explanation…)
```

`ctx index` puts the frontmatter (not the bodies) into a SQLite FTS5 full-text index with Porter
stemming and BM25 ranking. `ctx get` searches it, applies the relevance floor and the budget, and
prints one line per hit. It never reads document bodies at query time.

The summaries are the point. A summary written by whoever made the decision, at the moment they
made it, is a better search target than any chunk of the body, and it is what the next agent
reads first. ctx never generates summaries itself, by design: a summary invented later by a
model that was not there is a guess dressed as a record. `ctx lint` fails a note without one.

Three kinds of notes are built in, and you can point ctx at any directory of markdown:

- **explainers**: why something works the way it does;
- **decisions**: decision records, with `supersedes:` links, so an outdated decision is never
  returned as current (`ctx history <id>` shows the chain);
- **shared**: conventions and lessons shared across several repos.

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install git+https://github.com/ivantohelpyou/ctx   # installs the `ctx` command

cd your-repo
ctx init                 # writes .ctx/config.yaml and .ctx/ROUTING.md, gitignores the databases
# edit .ctx/config.yaml: point each corpus at a directory of your notes
ctx index                # build the index
ctx get "token budget"
ctx lint                 # check frontmatter, decision links, and which code each explainer covers
```

To also search notes you share across several repos, add a `shared` corpus:
`ctx init --shared ~/notes/shared`, or set `CTX_SHARED_DIR` once in your shell profile.

### With a coding agent

Add one line to your agent's instructions (`CLAUDE.md`, `AGENTS.md`, …):

```markdown
Before grepping for context, run `ctx get "<question>"` and read `.ctx/ROUTING.md`.
```

`.ctx/ROUTING.md` (written by `ctx init`) tells the agent how to read the output. For carrying
context across sessions or a `/clear`, there is a checkpoint file:

```bash
ctx checkpoint "indexed explainers; next: point the docs corpus at the Hugo site"
# next session reads .ctx/checkpoints/current.md
```

## Commands

| Command | What it does |
|---|---|
| `ctx init [--preset explainers\|hugo\|work\|mayor] [--shared PATH]` | Create `.ctx/` with a config shaped for the preset |
| `ctx index [--strict]` | Build the index (`--strict`: refuse to publish if decision links are broken) |
| `ctx get "<question>" [--budget N] [--limit N] [--corpus NAME] [--history] [--loose]` | Ask (defaults: 1,500 tokens, 5 hits) |
| `ctx history <id>` | A decision's supersession chain, oldest to newest |
| `ctx lint` | Check frontmatter, decision links, explainer staleness and coverage |
| `ctx checkpoint "<note>"` | Append a session note to `.ctx/checkpoints/current.md` |

`ctx --help` lists every flag.

## Output

```
id — title — path#slug — summary
  superseded-by: id — title          (with --history, if a newer decision replaced it)
...
dropped: id — title                  (at most 5 lines: what the budget left out)
dropped: +N more
```

or, when nothing clears the relevance floor:

```
weak: id — title — path#slug         (at most 3; the question is logged as a miss)
```

**Relevance floor.** Stopwords (`in`, `the`, `how`, …) are left out, and a hit must match at
least two of the remaining words, or the only one if there is just one. `--loose` turns the floor
off.

## Reference

### Files

Two SQLite files per repo, under `.ctx/`:

- **`index.db`**: disposable. `rm .ctx/index.db && ctx index` rebuilds it from the notes, and the
  output of `ctx get` is byte-for-byte the same for the same question against the same files.
- **`ctx.db`**: durable. The query log and the miss log (questions that found nothing), as
  SQLAlchemy models in `src/ctx_cli/ctxdb/`.

Two files, not one, because "disposable" and "keeps a durable query log" can't both be true of a
single file.

There is no git hook. `ctx get` compares a digest of every note's path, size and modification
time with the one recorded at the last `ctx index`, and reindexes if they differ. The one case a
digest can't see is an edit that keeps both size and modification time identical; if you ever
restore a timestamp by hand after editing, run `ctx index`.

### Configuration

Corpora are configured in `.ctx/config.yaml`:

```yaml
corpora:
  explainers:
    type: explainers
    path: docs/explainers
  decisions:
    type: decisions
    path: docs/decisions
  shared:
    type: shared
    path: ~/notes/shared
```

`path:` is expanded when loaded: `$VAR` / `${VAR}` first, then `~`; a relative path is resolved
against the repo root. In a committed config, write `~/…` rather than an absolute
`/home/<you>/…` path, which only works for the user who wrote it.

A corpus whose `path` isn't a directory (a typo, another machine's path, not checked out yet) is
an error in `ctx lint`, naming the corpus and the path. `ctx index` prints the same thing as a
warning and still indexes every other corpus.

### What to commit

`.ctx/config.yaml` and `.ctx/ROUTING.md` are what your agent instructions point at, so they must
exist in a fresh clone: both are **tracked**. Everything else under `.ctx/` is local. `ctx init`
writes these `.gitignore` lines (narrower than a blanket `.ctx/`, so it never ignores the two
tracked files):

```gitignore
.ctx/index.db*
.ctx/ctx.db*
.ctx/checkpoints/
```

| Path | Tracked? | Why |
|---|---|---|
| `.ctx/config.yaml` | yes | corpus config; must survive a fresh clone |
| `.ctx/ROUTING.md` | yes | what the agent instructions point at |
| `.ctx/index.db*` | no | disposable; `ctx index` rebuilds it |
| `.ctx/ctx.db*` | no | durable but local: query and miss logs |
| `.ctx/checkpoints/` | no | session notes, local to whoever is working |

`ctx init` never overwrites an existing `config.yaml` or `ROUTING.md` ("exists, left alone"), so
it's safe to run in a clone that already has them. `--preset` writes a config shaped for that kind
of corpus; see `_PRESET_CORPUS` in `src/ctx_cli/corpus.py`.

### Frontmatter

Every corpus: `id`, `title`, `summary` (hand-written and required; never generate it), `tags`,
`terms`, `date`. Use `terms:` for search vocabulary, not `aliases:`, which Hugo uses for URL
redirects. Explainers can also list the code they explain under `covers:` and carry
`last-verified:`, so `ctx lint` can report coverage and staleness.

`tags:` and `terms:` accept a YAML list or a comma-separated string. `date:` and `last-verified:`
are normalised to `YYYY-MM-DD` whether written as a date, a datetime (its calendar date as
written, never shifted for time zone) or a string; anything else is a lint error.

### Notes that already have someone else's frontmatter (`type: hugo`, `map:`)

A directory whose frontmatter serves another tool doesn't need rewriting. A per-corpus `map:`
(ctx's key → the file's key) tells ctx to read the file's key as its own, in memory only:

```yaml
corpora:
  docs:
    type: hugo          # preset: map {summary: description, tags: keywords}, derive_id: true
    path: docs
```

```yaml
corpora:
  docs:
    type: shared
    path: docs
    map: {summary: description, tags: keywords}
    derive_id: true     # id defaults to the file name
```

- A key the author wrote by hand always wins; `map:` only fills a key that is absent.
- `map:` is checked when loaded: an unknown target (`summry:`), the same target twice, or mapping
  `aliases:` into `tags:`/`terms:` is a config error, not a silently empty field.
- `type: hugo` skips documents with a truthy `draft:` (as Hugo does); `ctx lint` reports how many.
  `include_drafts: true` indexes them anyway.

### `type: work`: work items as markdown

For a repo that keeps its tasks as one markdown file per item. Extra frontmatter: `status`
(`open`, `in_progress`, `blocked`, `closed`), `priority`, `type`, `deps`, `closed-reason`,
`created`, `closed`. `ctx lint` requires `status`, `priority`, `type` and `created`; a closed item
needs `closed` and `closed-reason`; every `deps` id must exist in the corpus (an id with another
repo's prefix is a warning, not an error). `ctx get` leaves closed items out unless you pass
`--history`.

### `exclude:`

Every `*.md` under a corpus's `path` is treated as a note, except `README.md` by default:

```yaml
corpora:
  decisions:
    type: decisions
    path: docs/decisions
    exclude: ["README.md", "drafts/*.md"]   # default is ["README.md"]; [] includes READMEs
```

Globs are relative to the corpus's own `path`. Indexing, lint and the change detection all read
the same file list.

### Decisions: supersession and `--history`

Decision records link with `supersedes:` / `superseded-by:`. **The links, not the `status:`
field, decide what's current**: `ctx get` leaves a decision out if its status says it isn't
current *or* anything supersedes it, so a record with a missing or stale `status:` can't slip
through. Excluded decisions are filtered before ranking and never use up budget.

With `--history`, superseded decisions are included, each annotated with every current head of
its chain (a decision can be superseded by more than one other; a fork is valid data):

```
$ ctx history adr-001
adr-001 — superseded — 2026-09-01 — Two database files, not one
adr-002 — accepted — 2026-09-05 — Two database files, built with SEA  <- current
```

```
$ ctx history split-0
split-0 — superseded — 2026-01-01 — Split point
  split-x — accepted — 2026-02-01 — One branch  <- current
  split-y — accepted — 2026-02-02 — The other branch  <- current
```

**Cycles are reported, never silent.** `ctx history` prints what it found, then
`cycle detected: a -> b -> a — index is corrupt, run ctx lint` and exits 2; `ctx get --history`
marks the hit `superseded-by: (cycle)`. `ctx lint` rejects cycles and one-sided links at the
source. `ctx index` runs those structural checks on every build: by default it warns and still
publishes; `--strict` leaves the old index untouched and exits 1.

## Tests

```bash
uv run pytest
```

171 tests, covering frontmatter parsing, the index build, budgeted output, the relevance floor
and `weak:` leads, lint rules, checkpoints, the supersession walker and cycle handling, `map:`
validation, Hugo drafts, date normalisation, `exclude:`, `type: work`, and byte-identical output
across an index rebuild.

## License

MIT; see [LICENSE](LICENSE). Provided as is: issues and pull requests are welcome, support is
not promised.
