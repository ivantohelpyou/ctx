"""Templates `ctx init` writes into a fresh `.ctx/`."""

ROUTING_TEMPLATE = """\
# Routing

Before grepping around this repo, run:

    ctx get "<what you're looking for>" [--corpus explainers|decisions|shared] [--budget N]

It searches hand-written summaries (not file bodies) and returns ranked hits as
`id — title — path#slug — summary`, plus up to five `dropped:` lines for what
the budget excluded. A hit must match at least two of the query's words (or
the only one). If nothing does, you get up to three `weak:` lines instead —
one-word matches, leads not answers; open one only if its title fits
(`--loose` makes them ordinary results). Read the one or two files it names;
don't scan the corpus.
A superseded decision is never returned this way — add `--history` to see it,
annotated with `superseded-by: <id> — <title>` (the current head).

    ctx history <id>            a decision's supersession chain, oldest to
                                 newest, marking the current head

Corpora configured for this repo are in `.ctx/config.yaml`. A `shared`
corpus, if there is one, holds conventions and lessons shared across repos.

A miss you then resolve becomes a note: write it before you finish, in its
corpus, with the frontmatter `ctx lint` expects (`id`, `title`, a
one-sentence `summary`). The next question hits.

Session start: read `.ctx/checkpoints/current.md` if it exists. End a
session (or before `/clear`) with:

    ctx checkpoint "<what's done, what's next, open questions>"

Other commands: `ctx index` (rebuild; rarely needed, `ctx get` reindexes
itself), `ctx lint` (frontmatter + supersession integrity, run after editing
corpus docs).
"""
