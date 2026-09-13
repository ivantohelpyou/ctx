"""Corpus `path:` resolution: `~`/env-var expansion at load time, and what
happens when a resolved path isn't a directory at all (hq-synj review
finding 1 — a committed config with a literal `/home/<user>/...` path
otherwise fails silently, forever, on any other machine/user)."""

from __future__ import annotations

from ctx_cli.corpus import CONFIG_FILENAME, load_config
from ctx_cli.indexer import build_index, warn_missing_corpus_dirs
from ctx_cli.lint import lint


def _write_config(rig_dir, text):
    ctx_dir = rig_dir / ".ctx"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    (ctx_dir / CONFIG_FILENAME).write_text(text, encoding="utf-8")
    return ctx_dir


def test_load_config_expands_tilde(tmp_path, monkeypatch):
    home = tmp_path / "homedir"
    (home / "corpus").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    rig_dir = tmp_path / "rig"
    ctx_dir = _write_config(
        rig_dir,
        "corpora:\n  shared:\n    type: shared\n    path: ~/corpus\n",
    )
    config = load_config(ctx_dir)
    assert config.corpora[0].path == (home / "corpus").resolve()


def test_load_config_expands_env_var(tmp_path, monkeypatch):
    target = tmp_path / "somewhere" / "corpus"
    target.mkdir(parents=True)
    monkeypatch.setenv("MY_CORPUS_ROOT", str(tmp_path / "somewhere"))

    rig_dir = tmp_path / "rig"
    ctx_dir = _write_config(
        rig_dir,
        "corpora:\n  shared:\n    type: shared\n    path: $MY_CORPUS_ROOT/corpus\n",
    )
    config = load_config(ctx_dir)
    assert config.corpora[0].path == target.resolve()


def test_missing_corpus_dir_is_a_lint_error(tmp_path):
    rig_dir = tmp_path / "rig"
    ctx_dir = _write_config(
        rig_dir,
        "corpora:\n  shared:\n    type: shared\n    path: does-not-exist\n",
    )
    config = load_config(ctx_dir)
    report = lint(config)
    assert not report.ok
    assert any("not a directory" in e and "shared" in e for e in report.errors)


def test_missing_corpus_dir_warns_on_index_but_still_builds(tmp_path, capsys):
    rig_dir = tmp_path / "rig"
    real_corpus = rig_dir / "docs"
    real_corpus.mkdir(parents=True)
    (real_corpus / "a.md").write_text(
        "---\nid: a\ntitle: A\nsummary: a doc\n---\nbody\n", encoding="utf-8"
    )
    ctx_dir = _write_config(
        rig_dir,
        "corpora:\n"
        "  explainers:\n"
        "    type: explainers\n"
        "    path: docs\n"
        "  shared:\n"
        "    type: shared\n"
        "    path: nowhere\n",
    )
    config = load_config(ctx_dir)

    warnings = warn_missing_corpus_dirs(config)
    assert any("shared" in w and "not a directory" in w for w in warnings)
    assert not any("explainers" in w for w in warnings)

    count = build_index(config)
    assert count == 1  # the real corpus still indexed
    captured = capsys.readouterr()
    assert "shared" in captured.err
    assert "not a directory" in captured.err
