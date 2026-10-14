# The durable half of ctx, built with SEA

The plan (`docs/plans/ctx-context-retrieval.md`) says to build `ctx.db`'s
`queries` and `misses` tables through the `build-with-sea` skill, so NAVE
gives an admin surface and the ship is visible to Pilot Station.

**What actually ran** (using the installed `sea` CLI; the SEA project's own files were
never touched, only its CLI was invoked):

1. `models.py` was hand-written first (a prior pass misread "don't touch
   the SEA checkout" as "don't run `sea` at all" — it means don't touch the sea
   rig's own files, not the tool). `src/ctx_cli/ctxdb/models.py` already
   matched the shape `sea create-table` produces (`DeclarativeBase`,
   `Mapped`/`mapped_column`), so brownfield adoption fit as-is.
2. `sea init <this repo>` — discovered the two tables
   (`queries`, `misses`), kept the `ship_id` already in `.sea/project.json`
   (brownfield init keeps an existing id rather than minting a new one).
3. `sea nave generate --project <this repo> -o <this repo>/frontend` —
   14 pages (list/form/detail for `queries` and `misses`, schema, takeout,
   etc.), gitignored (generated, never hand-edited).
4. Verified end to end: built a real `ctx.db` (two `ctx get` calls, one a
   miss), ran `sea nave serve --frontend ./frontend --models
   src/ctx_cli/ctxdb/models.py --db sqlite:///.ctx/ctx.db`, and confirmed
   `GET /api/data/queries/` and `/api/data/misses/` return the logged rows.
5. `~/pilot-station/bin/station adopt <this repo> --owner Ivan --origin
   studio` — the ship is now in `station ls`, alongside pilot-station and
   faigin.

**Registered:** the ship is registered with the SEA station by explicit path (its id is in `.sea/project.json`).

**Still true:** FTS5 (`index.db`) is correctly outside this SEA ship — it's
the disposable half, rebuilt from markdown on every `ctx index`, with no
admin surface and nothing for SEA to model.

Nothing about the durable half is left undone.
