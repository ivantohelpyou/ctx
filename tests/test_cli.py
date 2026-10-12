from click.testing import CliRunner

from ctx_cli.cli import main
from ctx_cli.templates import ROUTING_TEMPLATE


def test_init_writes_config_and_routing_and_gitignore(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".ctx" / "config.yaml").exists()
    assert (tmp_path / ".ctx" / "ROUTING.md").exists()
    gitignore = (tmp_path / ".gitignore").read_text()
    # config.yaml and ROUTING.md are tracked (hq-synj) — only the generated/
    # durable state is ignored, and never the whole .ctx/ directory.
    assert ".ctx/" not in gitignore.splitlines()
    assert ".ctx/index.db*" in gitignore
    assert ".ctx/ctx.db*" in gitignore
    assert ".ctx/checkpoints/" in gitignore


def test_init_is_idempotent(tmp_path):
    runner = CliRunner()
    runner.invoke(main, ["init", "--dir", str(tmp_path)])
    config_before = (tmp_path / ".ctx" / "config.yaml").read_text()
    result = runner.invoke(main, ["init", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / ".ctx" / "config.yaml").read_text() == config_before


def test_init_never_overwrites_existing_tracked_config(tmp_path):
    """A config.yaml (or ROUTING.md) already committed from a previous
    session survives `ctx init` untouched, exit 0 — this is what makes it
    safe for a rig's CLAUDE.md to say "ctx init if .ctx/ is missing" even
    though a fresh clone already has a tracked config (hq-synj)."""
    ctx_dir = tmp_path / ".ctx"
    ctx_dir.mkdir()
    (ctx_dir / "config.yaml").write_text("corpora:\n  custom:\n    type: shared\n    path: x\n")
    (ctx_dir / "ROUTING.md").write_text("# custom routing\n")

    runner = CliRunner()
    result = runner.invoke(main, ["init", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "exists, left alone" in result.output
    assert (ctx_dir / "config.yaml").read_text() == (
        "corpora:\n  custom:\n    type: shared\n    path: x\n"
    )
    assert (ctx_dir / "ROUTING.md").read_text() == "# custom routing\n"


def test_init_preset_writes_shape_specific_config(tmp_path):
    runner = CliRunner()

    result = runner.invoke(main, ["init", "--dir", str(tmp_path), "--preset", "hugo",
                                  "--shared", "~/notes/shared"])
    assert result.exit_code == 0, result.output
    config = (tmp_path / ".ctx" / "config.yaml").read_text()
    assert "type: hugo" in config
    assert "docs:" in config
    assert "shared:" in config

    work_dir = tmp_path / "work-rig"
    work_dir.mkdir()
    runner.invoke(
        main, ["init", "--dir", str(work_dir), "--preset", "work", "--shared", "~/notes/shared"]
    )
    work_config = (work_dir / ".ctx" / "config.yaml").read_text()
    assert "type: work" in work_config
    assert "path: work" in work_config
    assert "shared:" in work_config

    mayor_dir = tmp_path / "mayor-rig"
    mayor_dir.mkdir()
    runner.invoke(main, ["init", "--dir", str(mayor_dir), "--preset", "mayor"])
    mayor_config = (mayor_dir / ".ctx" / "config.yaml").read_text()
    assert "type: decisions" in mayor_config
    assert "path: docs/shared" in mayor_config
    assert "path: docs/decisions" in mayor_config


def test_routing_template_is_small(tmp_path):
    approx_tokens = len(ROUTING_TEMPLATE) / 4
    assert approx_tokens < 400, f"ROUTING.md template is ~{approx_tokens:.0f} tokens, budget is 400"


def test_get_end_to_end(rig_dir):
    runner = CliRunner()
    result = runner.invoke(main, ["get", "fts5", "--dir", str(rig_dir)])
    assert result.exit_code == 0, result.output
    assert "exp-001" in result.output


def test_lint_end_to_end_clean(rig_dir):
    runner = CliRunner()
    result = runner.invoke(main, ["lint", "--dir", str(rig_dir)])
    assert result.exit_code == 0, result.output
    assert "explainers: covers coverage 100.0%" in result.output


def test_checkpoint_end_to_end(rig_dir):
    runner = CliRunner()
    result = runner.invoke(main, ["checkpoint", "session note here", "--dir", str(rig_dir)])
    assert result.exit_code == 0, result.output
    assert (rig_dir / ".ctx" / "checkpoints" / "current.md").read_text().find(
        "session note here"
    ) >= 0


def test_get_without_ctx_dir_fails_clearly(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["get", "anything", "--dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "ctx init" in result.output


def test_index_with_invalid_map_config_exits_cleanly_no_traceback(rig_dir):
    """re-i1n round-2 review: a config-validation ValueError (unknown map
    target, here) must exit through the same clean one-line path as any
    other known error — never a raw traceback."""
    (rig_dir / ".ctx" / "config.yaml").write_text(
        "corpora:\n"
        "  docs:\n"
        "    type: hugo\n"
        "    path: docs/hugo\n"
        "    map:\n"
        "      summry: description\n"
    )
    runner = CliRunner()
    result = runner.invoke(main, ["index", "--dir", str(rig_dir)])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert result.output.count("Error:") == 1
    assert "summry" in result.output


def _init_config(tmp_path, args, env=None):
    result = CliRunner().invoke(main, ["init", "--dir", str(tmp_path), *args], env=env)
    assert result.exit_code == 0, result.output
    return (tmp_path / ".ctx" / "config.yaml").read_text()


def test_init_writes_no_shared_corpus_unless_asked(tmp_path):
    """No default location for shared notes: a stranger's `ctx init` must not
    point at a directory that only exists on the author's machine."""
    config = _init_config(tmp_path, [], env={"CTX_SHARED_DIR": None})
    assert "shared" not in config
    assert "gt/" not in config


def test_init_shared_from_flag_or_env_flag_wins(tmp_path):
    a, b, c = (tmp_path / n for n in "abc")
    for d in (a, b, c):
        d.mkdir()
    flag_wins = _init_config(a, ["--shared", "~/flag/shared"], env={"CTX_SHARED_DIR": "~/env"})
    assert "path: ~/flag/shared" in flag_wins
    assert "path: ~/env/shared" in _init_config(b, [], env={"CTX_SHARED_DIR": "~/env/shared"})
    assert "path: /srv/notes" in _init_config(c, ["--shared", "/srv/notes"])


def test_init_writes_home_paths_in_tilde_form(tmp_path):
    """The shell expands `~` before ctx sees it; the committed config must
    still say `~/...`, or it only works for this user."""
    from pathlib import Path
    config = _init_config(tmp_path, ["--shared", str(Path.home() / "notes" / "shared")])
    assert "path: ~/notes/shared" in config
    assert str(Path.home()) not in config


def test_routing_template_names_no_private_location():
    assert "~/gt" not in ROUTING_TEMPLATE
    assert " rig" not in ROUTING_TEMPLATE.lower()
