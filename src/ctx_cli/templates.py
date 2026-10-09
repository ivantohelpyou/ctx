"""Templates `ctx init` writes into a fresh `.ctx/`."""

ROUTING_TEMPLATE = """\
# Routing

Before grepping around this rig, run:

    ctx get "<what you're looking for>" [--corpus explainers|decisions|shared] [--budget N]

It searches hand-written summaries (not file bodies) and returns ranked hits as
`id — title — path#slug — summary`, plus up to five `dropped:` lines for what
the budget excluded. A hit must match at least two of the query's words (or
the only one), so no output means nothing in the corpus answers it — add
`--loose` to see one-word matches. Read the one or two files it names; don't
scan the corpus.
A superseded decision is never returned this way — add `--history` to see it,
annotated with `superseded-by: <id> — <title>` (the current head, not just
its direct successor).

    ctx history <id>            a decision's supersession chain, oldest to
                                 newest, marking the current head

Corpora configured for this rig are in `.ctx/config.yaml`. `shared` is
cross-rig, at `~/gt/docs/shared/` — conventions and lessons, nothing local.

Session start: read `.ctx/checkpoints/current.md` if it exists — that's where
the last session left off. End a session (or before `/clear`) with:

    ctx checkpoint "<what's done, what's next, open questions>"

Other commands: `ctx index` (rebuild, rarely needed — `ctx get` reindexes
itself when a source file changed), `ctx lint` (frontmatter + supersession
integrity, run after editing corpus docs).
"""
