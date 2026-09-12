import subprocess

from ctx_cli.corpus import load_config
from ctx_cli.lint import SHARED_BYTE_CEILING, lint


def _git(rig_dir, *args):
    subprocess.run(["git", *args], cwd=rig_dir, check=True, capture_output=True)


def _init_git_repo(rig_dir):
    _git(rig_dir, "init", "-q")
    _git(rig_dir, "config", "user.email", "test@example.com")
    _git(rig_dir, "config", "user.name", "Test")
    _git(rig_dir, "add", "-A")
    _git(rig_dir, "commit", "-q", "-m", "initial")


def test_clean_fixture_passes_lint(config):
    report = lint(config)
    assert report.ok, report.errors


def test_explainer_coverage_percentage_reported(config):
    report = lint(config)
    assert report.coverage["explainers"] == 100.0


def test_missing_covers_path_is_an_error(rig_dir):
    explainer = rig_dir / "docs" / "explainers" / "001-fts5-choice.md"
    text = explainer.read_text().replace("src/searcher.py", "src/does-not-exist.py")
    explainer.write_text(text)
    config = load_config(rig_dir / ".ctx")
    report = lint(config)
    assert not report.ok
    assert any("does not exist" in e for e in report.errors)


def test_missing_required_field_is_an_error(rig_dir):
    shared_doc = rig_dir / "docs" / "shared" / "lesson-001-worktrees.md"
    text = shared_doc.read_text().replace("summary: ", "not-summary: ")
    shared_doc.write_text(text)
    config = load_config(rig_dir / ".ctx")
    report = lint(config)
    assert not report.ok
    assert any("missing required frontmatter field 'summary'" in e for e in report.errors)


def test_reciprocal_supersession_violation(rig_dir):
    adr1 = rig_dir / "docs" / "decisions" / "adr-001-two-databases.md"
    adr1.write_text(adr1.read_text().replace("superseded-by: [adr-002]", "superseded-by: []"))
    config = load_config(rig_dir / ".ctx")
    report = lint(config)
    assert not report.ok
    assert any("missing reciprocal superseded-by" in e for e in report.errors)


def test_accepted_doc_superseded_is_an_error(rig_dir):
    adr1 = rig_dir / "docs" / "decisions" / "adr-001-two-databases.md"
    adr1.write_text(adr1.read_text().replace("status: superseded", "status: accepted"))
    config = load_config(rig_dir / ".ctx")
    report = lint(config)
    assert not report.ok
    assert any("status 'accepted' but superseded by" in e for e in report.errors)


def test_supersession_cycle_detected(rig_dir):
    adr1 = rig_dir / "docs" / "decisions" / "adr-001-two-databases.md"
    adr1.write_text(
        adr1.read_text()
        .replace("supersedes: []", "supersedes: [adr-002]")
        .replace("status: superseded", "status: retired")
    )
    config = load_config(rig_dir / ".ctx")
    report = lint(config)
    assert not report.ok
    assert any("supersession cycle" in e for e in report.errors)


def test_shared_corpus_over_byte_ceiling_is_an_error(rig_dir):
    big = rig_dir / "docs" / "shared" / "huge.md"
    padding = "x" * (SHARED_BYTE_CEILING + 1)
    big.write_text(f"---\nid: shared-huge\ntitle: Huge\nsummary: padding\n---\n{padding}")
    config = load_config(rig_dir / ".ctx")
    report = lint(config)
    assert not report.ok
    assert any("over the" in e and "byte ceiling" in e for e in report.errors)


def test_explainer_staleness_against_git_history(rig_dir):
    _init_git_repo(rig_dir)
    # Now edit the covered source file and commit again, after last-verified.
    src = rig_dir / "src" / "searcher.py"
    src.write_text(src.read_text() + "\n# changed\n")
    _git(rig_dir, "add", "-A")
    _git(rig_dir, "commit", "-q", "-m", "change covered source")

    explainer = rig_dir / "docs" / "explainers" / "001-fts5-choice.md"
    text = explainer.read_text().replace("last-verified: 2026-09-12", "last-verified: 2020-01-01")
    explainer.write_text(text)

    config = load_config(rig_dir / ".ctx")
    report = lint(config)
    assert not report.ok
    assert any("changed" in e and "after last-verified" in e for e in report.errors)
