"""`ctx index`'s decisions structural check and `--strict` (hq-sl5k round 2).

`ctx index`/`ctx get` never ran lint, so a corpus that drifted out of
structural shape (an `accepted` doc something else claims to supersede, a
missing reciprocal link, a cycle) was silently indexed and served. This
covers the cheap subset `ctx index` now runs on every build."""

from __future__ import annotations

from click.testing import CliRunner

from ctx_cli.cli import main
from ctx_cli.corpus import load_config
from ctx_cli.lint import decisions_structural_errors


def _break_reciprocity(rig_dir):
    """adr-001 is superseded by adr-002 in the fixture; drop the reciprocal
    link on adr-001's own file — a structural error `_lint_decisions`
    catches."""
    adr1 = rig_dir / "docs" / "decisions" / "adr-001-two-databases.md"
    adr1.write_text(adr1.read_text().replace("superseded-by: [adr-002]", "superseded-by: []"))


def test_decisions_structural_errors_clean_fixture(config):
    assert decisions_structural_errors(config) == []


def test_decisions_structural_errors_catches_broken_reciprocity(rig_dir):
    _break_reciprocity(rig_dir)
    config = load_config(rig_dir / ".ctx")
    errors = decisions_structural_errors(config)
    assert any("missing reciprocal superseded-by" in e for e in errors)


def test_ctx_index_warns_on_stderr_but_still_publishes(rig_dir):
    _break_reciprocity(rig_dir)
    result = CliRunner().invoke(main, ["index", "--dir", str(rig_dir)])
    assert result.exit_code == 0, result.output
    assert "indexed" in result.output
    assert "decisions lint error" in result.output
    assert (rig_dir / ".ctx" / "index.db").exists()


def test_ctx_index_clean_fixture_no_warning(rig_dir):
    result = CliRunner().invoke(main, ["index", "--dir", str(rig_dir)])
    assert result.exit_code == 0, result.output
    assert "decisions lint error" not in result.output


def test_ctx_index_strict_refuses_to_publish(rig_dir):
    _break_reciprocity(rig_dir)
    index_path = rig_dir / ".ctx" / "index.db"
    assert not index_path.exists()

    result = CliRunner().invoke(main, ["index", "--strict", "--dir", str(rig_dir)])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert not index_path.exists()  # never published


def test_ctx_index_strict_does_not_clobber_a_prior_good_index(rig_dir):
    """--strict refuses PUBLISHING, not the command itself — a prior good
    index.db must survive a later, structurally-broken build being refused."""
    result = CliRunner().invoke(main, ["index", "--dir", str(rig_dir)])
    assert result.exit_code == 0, result.output
    before = (rig_dir / ".ctx" / "index.db").read_bytes()

    _break_reciprocity(rig_dir)
    result = CliRunner().invoke(main, ["index", "--strict", "--dir", str(rig_dir)])
    assert result.exit_code == 1

    after = (rig_dir / ".ctx" / "index.db").read_bytes()
    assert before == after


def test_ctx_index_strict_clean_fixture_exits_0(rig_dir):
    result = CliRunner().invoke(main, ["index", "--strict", "--dir", str(rig_dir)])
    assert result.exit_code == 0, result.output
    assert (rig_dir / ".ctx" / "index.db").exists()
