"""`ctx` — token-lean context retrieval over explainer, decision, and shared
corpora.

\b
ctx init [--dir PATH] [--preset P]  scaffold .ctx/ (config, ROUTING.md,
                                 checkpoints/); --preset explainers|hugo|work|
                                 mayor writes a config shaped for that corpus
ctx index                       rebuild index.db from frontmatter
ctx get "<query>" [...]         search, budgeted, deterministic output
ctx history <id>                a decision's supersession chain, oldest to newest
ctx lint                        frontmatter + supersession graph integrity
ctx checkpoint "<note>"         append session state to checkpoints/current.md
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import click

from . import checkpoint as checkpoint_mod
from .corpus import (
    CHECKPOINTS_DIRNAME,
    CONFIG_FILENAME,
    CTX_DIRNAME,
    ROUTING_FILENAME,
    VALID_INIT_PRESETS,
    CtxNotFoundError,
    default_config_yaml,
    find_ctx_dir,
    load_config,
)
from .ctxdb.db import log_query
from .history import history_view
from .indexer import DuplicateIdError, build_index, ensure_fresh_index
from .lint import decisions_structural_errors
from .lint import lint as run_lint
from .query import DEFAULT_BUDGET, DEFAULT_LIMIT, apply_budget, render, search
from .templates import ROUTING_TEMPLATE

# Errors that mean "the index/corpus is broken in a known way" — reported as a
# clean one-line, non-zero-exit message, never a raw traceback.
INDEX_ERRORS = (DuplicateIdError, sqlite3.Error, OSError)

# Written literally into config.yaml — NOT resolved via Path.home() — so a
# committed config (sea/research/speakeasy all commit `shared`'s path) reads
# the same on any machine/user; `~` is expanded at load time (corpus.py,
# hq-synj review finding 1), not at `ctx init` time.
SHARED_CORPUS_DEFAULT = "~/gt/docs/shared"


@click.group(help=__doc__)
def main():
    pass


# `.ctx/config.yaml` and `.ctx/ROUTING.md` are tracked (hq-synj): they're the
# routing surface a rig's CLAUDE.md points at and must exist in a fresh
# clone with no `ctx init` run. Only the generated/durable state underneath
# is gitignored — README "What to commit" has the full rationale.
GITIGNORE_LINES = (
    f"{CTX_DIRNAME}/index.db*",
    f"{CTX_DIRNAME}/ctx.db*",
    f"{CTX_DIRNAME}/{CHECKPOINTS_DIRNAME}/",
)


@main.command("init")
@click.option("--dir", "target_dir", type=click.Path(path_type=Path), default=None)
@click.option(
    "--preset",
    type=click.Choice(VALID_INIT_PRESETS),
    default="explainers",
    help="starter config.yaml shaped for a known corpus type, instead of the "
    "generic explainers guess (explainers/hugo/work/mayor).",
)
def init_cmd(target_dir: Path | None, preset: str):
    """Scaffold `.ctx/` at TARGET_DIR (default: cwd)."""
    rig_root = (target_dir or Path.cwd()).resolve()
    ctx_dir = rig_root / CTX_DIRNAME
    ctx_dir.mkdir(parents=True, exist_ok=True)
    (ctx_dir / CHECKPOINTS_DIRNAME).mkdir(exist_ok=True)

    config_path = ctx_dir / CONFIG_FILENAME
    if not config_path.exists():
        config_path.write_text(
            default_config_yaml(SHARED_CORPUS_DEFAULT, preset=preset), encoding="utf-8"
        )
        click.echo(f"wrote {config_path}")
    else:
        click.echo(f"{config_path} exists, left alone")

    routing_path = ctx_dir / ROUTING_FILENAME
    if not routing_path.exists():
        routing_path.write_text(ROUTING_TEMPLATE, encoding="utf-8")
        click.echo(f"wrote {routing_path}")
    else:
        click.echo(f"{routing_path} exists, left alone")

    gitignore_path = rig_root / ".gitignore"
    existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    existing_lines = existing.splitlines()
    to_add = [line for line in GITIGNORE_LINES if line not in existing_lines]
    if to_add:
        with gitignore_path.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            for line in to_add:
                f.write(f"{line}\n")
        click.echo(f"added {', '.join(to_add)} to {gitignore_path}")

    click.echo(f"ctx initialized at {ctx_dir}. Edit {config_path} to point at real corpora.")


@main.command("index")
@click.option("--dir", "start_dir", type=click.Path(path_type=Path), default=None)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="refuse to publish (exit 1, index.db left as it was) if the decisions "
    "corpus fails its structural checks (reciprocity, cycles, accepted-but-superseded)",
)
def index_cmd(start_dir: Path | None, strict: bool):
    """Rebuild index.db from frontmatter (full rebuild, not incremental)."""
    try:
        ctx_dir = find_ctx_dir(start_dir)
        config = load_config(ctx_dir)
    except (CtxNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    # Read straight from frontmatter, not the index — this must be knowable
    # before deciding whether to build at all. `--strict` never publishes: the
    # atomic build in indexer.py already writes to a temp file and renames
    # only on success, so simply not calling build_index() here achieves
    # "without publishing" with no change to that machinery.
    decisions_errors = decisions_structural_errors(config)
    if strict and decisions_errors:
        for err in decisions_errors:
            click.echo(f"ERROR: {err}", err=True)
        click.echo(
            f"{len(decisions_errors)} decisions lint error(s) — refusing to publish "
            f"(--strict); index.db left unchanged",
            err=True,
        )
        raise click.exceptions.Exit(1)

    try:
        count = build_index(config)
    except INDEX_ERRORS as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"indexed {count} documents into {config.index_db_path}")
    if decisions_errors:
        click.echo(f"{len(decisions_errors)} decisions lint errors — run ctx lint", err=True)


@main.command("get")
@click.argument("query")
@click.option(
    "--corpus", default=None, help="Limit to one corpus (explainers|decisions|shared|work)."
)
@click.option(
    "--budget", default=DEFAULT_BUDGET, show_default=True, help="Budget in tokens (chars/4)."
)
@click.option(
    "--limit",
    default=DEFAULT_LIMIT,
    show_default=True,
    help="Max entries returned, regardless of budget.",
)
@click.option(
    "--history",
    is_flag=True,
    default=False,
    help="Include superseded/non-accepted decisions (annotated with what "
    "replaced them; '(cycle)' if the supersession graph loops) and closed "
    "work-corpus docs (no annotation — a closed bead has no successor).",
)
@click.option("--dir", "start_dir", type=click.Path(path_type=Path), default=None)
def get_cmd(
    query: str,
    corpus: str | None,
    budget: int,
    limit: int,
    history: bool,
    start_dir: Path | None,
):
    """Search the index; print a budgeted, deterministic result set. Warns
    once on stderr (exit 0) if the supersession graph has a cycle."""
    try:
        ctx_dir = find_ctx_dir(start_dir)
        config = load_config(ctx_dir)
    except (CtxNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    decisions_corpora = [c.name for c in config.corpora if c.type == "decisions"]
    work_corpora = [c.name for c in config.corpora if c.type == "work"]
    try:
        ensure_fresh_index(config)
        hits = search(
            config.index_db_path,
            query,
            corpus=corpus,
            history=history,
            decisions_corpora=decisions_corpora,
            work_corpora=work_corpora,
        )
    except INDEX_ERRORS as e:
        raise click.ClickException(str(e)) from e

    kept, dropped = apply_budget(hits, budget, limit=limit)
    click.echo(render(kept, dropped), nl=False)
    if any(h.cycle for h in kept):
        click.echo(
            "warning: supersession graph has a cycle — index is corrupt, run ctx lint", err=True
        )

    # A failure to log is never a reason to fail a query that already
    # succeeded — log_query() degrades to a stderr warning on its own.
    log_query(config.ctx_db_path, query=query, budget=budget, returned=len(kept), corpus=corpus)


@main.command("history")
@click.argument("doc_id")
@click.option("--dir", "start_dir", type=click.Path(path_type=Path), default=None)
def history_cmd(doc_id: str, start_dir: Path | None):
    """Print DOC_ID's supersession chain, oldest to newest, indenting a fork
    and marking every current head. If the graph loops, prints what it found,
    then exits 2 with a 'cycle detected' message on stderr."""
    try:
        ctx_dir = find_ctx_dir(start_dir)
        config = load_config(ctx_dir)
    except (CtxNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    try:
        ensure_fresh_index(config)
        uri = config.index_db_path.resolve().as_uri() + "?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True)
        try:
            lines, cycle_path = history_view(conn, doc_id)
        finally:
            conn.close()
    except INDEX_ERRORS as e:
        raise click.ClickException(str(e)) from e

    if not lines:
        raise click.ClickException(f"no document {doc_id!r} in the index")

    for ln in lines:
        indent = "  " * ln.depth
        marker = "  <- current" if ln.is_head else ""
        click.echo(f"{indent}{ln.id} — {ln.status or ''} — {ln.date or ''} — {ln.title}{marker}")

    if cycle_path is not None:
        click.echo(f"cycle detected: {cycle_path} — index is corrupt, run ctx lint", err=True)
        raise click.exceptions.Exit(2)


@main.command("lint")
@click.option("--dir", "start_dir", type=click.Path(path_type=Path), default=None)
def lint_cmd(start_dir: Path | None):
    """Check frontmatter contract + supersession graph integrity."""
    try:
        ctx_dir = find_ctx_dir(start_dir)
        config = load_config(ctx_dir)
    except (CtxNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    report = run_lint(config)

    for name, pct in report.coverage.items():
        click.echo(f"{name}: covers coverage {pct:.1f}%")
    for name, n in report.skipped_drafts.items():
        if n:
            click.echo(f"{name}: {n} draft(s) skipped")
    for warn in report.warnings:
        click.echo(f"WARNING: {warn}")
    for err in report.errors:
        click.echo(f"ERROR: {err}")

    if not report.ok:
        raise click.exceptions.Exit(1)
    click.echo("ctx lint: clean")


@main.command("checkpoint")
@click.argument("note")
@click.option("--dir", "start_dir", type=click.Path(path_type=Path), default=None)
def checkpoint_cmd(note: str, start_dir: Path | None):
    """Append NOTE to .ctx/checkpoints/current.md."""
    try:
        ctx_dir = find_ctx_dir(start_dir)
        config = load_config(ctx_dir)
    except (CtxNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    path = checkpoint_mod.append_checkpoint(config, note)
    click.echo(f"appended to {path}")


if __name__ == "__main__":
    main()
