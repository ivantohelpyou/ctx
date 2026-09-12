"""`ctx` — token-lean context retrieval over explainer, decision, and shared
corpora.

\b
ctx init [--dir PATH]           scaffold .ctx/ (config, ROUTING.md, checkpoints/)
ctx index                       rebuild index.db from frontmatter
ctx get "<query>" [...]         search, budgeted, deterministic output
ctx lint                        frontmatter + supersession graph integrity
ctx checkpoint "<note>"         append session state to checkpoints/current.md
"""

from __future__ import annotations

from pathlib import Path

import click

from . import checkpoint as checkpoint_mod
from .corpus import (
    CONFIG_FILENAME,
    CTX_DIRNAME,
    ROUTING_FILENAME,
    CtxNotFoundError,
    default_config_yaml,
    find_ctx_dir,
    load_config,
)
from .ctxdb.db import log_query
from .indexer import build_index, ensure_fresh_index
from .lint import lint as run_lint
from .query import DEFAULT_BUDGET, apply_budget, render, search
from .templates import ROUTING_TEMPLATE

SHARED_CORPUS_DEFAULT = Path.home() / "gt" / "docs" / "shared"


@click.group(help=__doc__)
def main():
    pass


@main.command("init")
@click.option("--dir", "target_dir", type=click.Path(path_type=Path), default=None)
def init_cmd(target_dir: Path | None):
    """Scaffold `.ctx/` at TARGET_DIR (default: cwd)."""
    rig_root = (target_dir or Path.cwd()).resolve()
    ctx_dir = rig_root / CTX_DIRNAME
    ctx_dir.mkdir(parents=True, exist_ok=True)
    (ctx_dir / "checkpoints").mkdir(exist_ok=True)

    config_path = ctx_dir / CONFIG_FILENAME
    if not config_path.exists():
        config_path.write_text(default_config_yaml(SHARED_CORPUS_DEFAULT), encoding="utf-8")
        click.echo(f"wrote {config_path}")
    else:
        click.echo(f"{config_path} already exists, left untouched")

    routing_path = ctx_dir / ROUTING_FILENAME
    if not routing_path.exists():
        routing_path.write_text(ROUTING_TEMPLATE, encoding="utf-8")
        click.echo(f"wrote {routing_path}")
    else:
        click.echo(f"{routing_path} already exists, left untouched")

    gitignore_path = rig_root / ".gitignore"
    line = f"{CTX_DIRNAME}/"
    existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    if line not in existing.splitlines():
        with gitignore_path.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(f"{line}\n")
        click.echo(f"added '{line}' to {gitignore_path}")

    click.echo(f"ctx initialized at {ctx_dir}. Edit {config_path} to point at real corpora.")


@main.command("index")
@click.option("--dir", "start_dir", type=click.Path(path_type=Path), default=None)
def index_cmd(start_dir: Path | None):
    """Rebuild index.db from frontmatter (full rebuild, not incremental)."""
    try:
        ctx_dir = find_ctx_dir(start_dir)
    except CtxNotFoundError as e:
        raise click.ClickException(str(e)) from e
    config = load_config(ctx_dir)
    count = build_index(config)
    click.echo(f"indexed {count} documents into {config.index_db_path}")


@main.command("get")
@click.argument("query")
@click.option("--corpus", default=None, help="Limit to one corpus (explainers|decisions|shared).")
@click.option(
    "--budget", default=DEFAULT_BUDGET, show_default=True, help="Budget in tokens (chars/4)."
)
@click.option(
    "--history", is_flag=True, default=False, help="Include superseded/non-accepted docs."
)
@click.option("--dir", "start_dir", type=click.Path(path_type=Path), default=None)
def get_cmd(query: str, corpus: str | None, budget: int, history: bool, start_dir: Path | None):
    """Search the index; print a budgeted, deterministic result set."""
    try:
        ctx_dir = find_ctx_dir(start_dir)
    except CtxNotFoundError as e:
        raise click.ClickException(str(e)) from e
    config = load_config(ctx_dir)
    ensure_fresh_index(config)

    hits = search(config.index_db_path, query, corpus=corpus, history=history)
    kept, dropped = apply_budget(hits, budget)
    click.echo(render(kept, dropped), nl=False)

    log_query(config.ctx_db_path, query=query, budget=budget, returned=len(kept), corpus=corpus)


@main.command("lint")
@click.option("--dir", "start_dir", type=click.Path(path_type=Path), default=None)
def lint_cmd(start_dir: Path | None):
    """Check frontmatter contract + supersession graph integrity."""
    try:
        ctx_dir = find_ctx_dir(start_dir)
    except CtxNotFoundError as e:
        raise click.ClickException(str(e)) from e
    config = load_config(ctx_dir)
    report = run_lint(config)

    for name, pct in report.coverage.items():
        click.echo(f"{name}: covers coverage {pct:.1f}%")
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
    except CtxNotFoundError as e:
        raise click.ClickException(str(e)) from e
    config = load_config(ctx_dir)
    path = checkpoint_mod.append_checkpoint(config, note)
    click.echo(f"appended to {path}")


if __name__ == "__main__":
    main()
