"""Corpus configuration: where `.ctx/` lives, and what each corpus in
`.ctx/config.yaml` points at.

A rig's `.ctx/` is found by walking up from cwd, the way git finds `.git/`.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml

CTX_DIRNAME = ".ctx"
CONFIG_FILENAME = "config.yaml"
INDEX_DB_FILENAME = "index.db"
CTX_DB_FILENAME = "ctx.db"
ROUTING_FILENAME = "ROUTING.md"
CHECKPOINTS_DIRNAME = "checkpoints"
CHECKPOINT_FILENAME = "current.md"

VALID_CORPUS_TYPES = ("explainers", "decisions", "shared")


class CtxNotFoundError(RuntimeError):
    pass


@dataclasses.dataclass
class CorpusSpec:
    name: str
    type: str
    path: Path  # resolved, absolute


@dataclasses.dataclass
class RigConfig:
    rig_root: Path
    ctx_dir: Path
    corpora: list[CorpusSpec]

    @property
    def index_db_path(self) -> Path:
        return self.ctx_dir / INDEX_DB_FILENAME

    @property
    def ctx_db_path(self) -> Path:
        return self.ctx_dir / CTX_DB_FILENAME

    @property
    def checkpoints_dir(self) -> Path:
        return self.ctx_dir / CHECKPOINTS_DIRNAME


def find_ctx_dir(start: Path | None = None) -> Path:
    """Walk upward from `start` (default cwd) looking for a `.ctx/config.yaml`."""
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        cfg = candidate / CTX_DIRNAME / CONFIG_FILENAME
        if cfg.is_file():
            return candidate / CTX_DIRNAME
    raise CtxNotFoundError(
        f"no {CTX_DIRNAME}/{CONFIG_FILENAME} found in {cur} or any parent; run `ctx init` first"
    )


def load_config(ctx_dir: Path | None = None) -> RigConfig:
    ctx_dir = ctx_dir or find_ctx_dir()
    rig_root = ctx_dir.parent
    raw = yaml.safe_load((ctx_dir / CONFIG_FILENAME).read_text(encoding="utf-8")) or {}
    corpora = []
    for name, spec in (raw.get("corpora") or {}).items():
        ctype = spec.get("type", name)
        if ctype not in VALID_CORPUS_TYPES:
            raise ValueError(
                f"corpus {name!r}: unknown type {ctype!r}, must be one of {VALID_CORPUS_TYPES}"
            )
        raw_path = Path(spec["path"])
        path = raw_path if raw_path.is_absolute() else (rig_root / raw_path)
        corpora.append(CorpusSpec(name=name, type=ctype, path=path.resolve()))
    return RigConfig(rig_root=rig_root, ctx_dir=ctx_dir, corpora=corpora)


def default_config_yaml(shared_path: Path) -> str:
    """Template written by `ctx init`. Explainers path is a guess the operator edits;
    shared always points at the one cross-rig corpus."""
    return (
        "corpora:\n"
        "  explainers:\n"
        "    type: explainers\n"
        "    path: docs/explainers\n"
        "  shared:\n"
        "    type: shared\n"
        f"    path: {shared_path}\n"
    )


def iter_corpus_files(spec: CorpusSpec):
    """Yield every markdown file under a corpus's path, recursively, sorted for
    determinism."""
    if not spec.path.is_dir():
        return
    yield from sorted(spec.path.rglob("*.md"))
