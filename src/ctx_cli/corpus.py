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

VALID_CORPUS_TYPES = ("explainers", "decisions", "shared", "hugo")

# Preset for `type: hugo` (research's docs/ corpus): Hugo's own frontmatter
# keys are read as ctx's contract keys without touching the source files —
# Hugo still reads `description`/`keywords` itself, so the public build is
# unaffected. `aliases:` is deliberately absent from this map: it is Hugo's
# URL-redirect list, not search vocabulary, and must never become `terms:`
# (plan §Decisions — research already uses `aliases:` for redirects).
HUGO_FIELD_MAP = {"summary": "description", "tags": "keywords"}


class CtxNotFoundError(RuntimeError):
    pass


@dataclasses.dataclass
class CorpusSpec:
    name: str
    type: str
    path: Path  # resolved, absolute
    # Target-field -> source-frontmatter-key. Applied read-only, in memory,
    # when building the index/lint view of a document; the file on disk is
    # never rewritten. `type: hugo` seeds this with HUGO_FIELD_MAP; an
    # explicit `map:` in config.yaml may add to or override it for any type.
    field_map: dict[str, str] = dataclasses.field(default_factory=dict)
    # If true and a document has no `id` after `field_map` is applied,
    # derive one from the file's path relative to the corpus root (stem,
    # POSIX separators, no extension). Hugo docs don't carry an `id:` key.
    derive_id: bool = False


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

        field_map = dict(HUGO_FIELD_MAP) if ctype == "hugo" else {}
        explicit_map = spec.get("map") or {}
        field_map.update({str(k): str(v) for k, v in explicit_map.items()})
        derive_id = bool(spec.get("derive_id", ctype == "hugo"))

        corpora.append(
            CorpusSpec(
                name=name,
                type=ctype,
                path=path.resolve(),
                field_map=field_map,
                derive_id=derive_id,
            )
        )
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


def apply_field_map(raw: dict, field_map: dict[str, str]) -> dict:
    """Return the in-memory view ctx indexes/lints from a document's raw
    frontmatter: for each `target: source` pair in `field_map`, if `source`
    is present (and non-empty) in `raw`, copy its value onto `target`.

    Additive, not destructive: `raw` itself (and the file it came from) is
    never mutated, and `source` keys are left in place — so e.g. a Hugo
    file's `description:`/`keywords:` stay exactly as Hugo reads them; only
    the copy ctx builds its index from gains `summary:`/`tags:`. A key not
    named in `field_map` (Hugo's `aliases:`, for one) is never touched."""
    if not field_map or not raw:
        return raw
    mapped = dict(raw)
    for target, source in field_map.items():
        value = raw.get(source)
        if value not in (None, "", []):
            mapped[target] = value
    return mapped


def derive_id_from_path(spec: CorpusSpec, path: Path) -> str:
    """`id` for a document that doesn't set one: its path relative to the
    corpus root, stem only (no `.md`), POSIX separators — stable, and
    globally unique as long as no two corpora share a relative path."""
    rel = path.resolve().relative_to(spec.path.resolve())
    return rel.with_suffix("").as_posix()


def resolved_frontmatter(spec: CorpusSpec, path: Path, raw: dict) -> dict:
    """The frontmatter view ctx actually indexes and lints for one document:
    `spec.field_map` applied, then `id` derived from the path if the corpus
    opts into it (`derive_id`) and the document didn't set one. `raw` is
    returned unchanged (including the `{}` "no frontmatter" case) when
    neither applies."""
    doc = apply_field_map(raw, spec.field_map)
    if spec.derive_id and doc and not doc.get("id"):
        doc = dict(doc)
        doc["id"] = derive_id_from_path(spec, path)
    return doc
