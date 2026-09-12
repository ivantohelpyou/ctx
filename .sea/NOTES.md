# What the build-with-sea skill produced here, and what it did not

The plan (`docs/plans/ctx-context-retrieval.md`) says to build the durable half
of ctx — `ctx.db`'s `queries` and `misses` tables — through the `build-with-sea`
skill, so NAVE gives an admin surface and the ship is visible to Pilot Station.

**This pass did not run the actual `sea` CLI.** The tool lives at
`~/gt/sea/crew/ivan` (a shared working copy), and the coordinator's brief for
this bead was explicit: do not touch `~/gt/sea/` at all in this pass — a
sibling migration bead (sea-vfpf) was running there concurrently, and `uv run
--directory ~/gt/sea/crew/ivan sea` would invoke that rig's own environment.

What was done instead, per the bead's own fallback instruction ("If the
skill's entry point cannot run non-interactively, do the SQLAlchemy models.py
by hand, write the ship identity file the skill describes, and report exactly
what you skipped"):

- `src/ctx_cli/ctxdb/models.py` hand-written in the shape `sea create-table`
  would emit (`DeclarativeBase`, `Mapped`/`mapped_column`, SQLAlchemy 2.0),
  covering exactly the two tables the plan specifies: `queries`, `misses`.
- `.sea/project.json` hand-written with a minted `ship_id`, matching the shape
  read from `~/pilot-station/.sea/project.json` (the one real example
  reachable without touching sea's own rig).

**Skipped, and why it matters:**
- The ship is **not registered** with Pilot Station (`station adopt` was never
  run) — `station scan` will not find it until someone runs that from a
  session that can touch the sea rig.
- **No NAVE frontend was generated.** There is no browsable admin surface over
  `queries`/`misses` yet; reading them today means `sqlite3 .ctx/ctx.db`.
- FTS5 (`index.db`) is correctly outside this SEA ship regardless — it is the
  disposable half and was never in scope for SEA.

Follow-up: once no concurrent work is touching `~/gt/sea/`, run
`$SEA init ~/projects/ctx` (brownfield: models.py already exists), then
`nave generate --project ~/projects/ctx -o ~/projects/ctx/frontend`, then
`station adopt ~/projects/ctx --owner Ivan --origin studio`.
