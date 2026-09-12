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
    assert ".ctx/" in gitignore


def test_init_is_idempotent(tmp_path):
    runner = CliRunner()
    runner.invoke(main, ["init", "--dir", str(tmp_path)])
    config_before = (tmp_path / ".ctx" / "config.yaml").read_text()
    result = runner.invoke(main, ["init", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / ".ctx" / "config.yaml").read_text() == config_before


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
