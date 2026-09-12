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

# Every target key `map:` is allowed to write to — the frontmatter contract
# (README, plan §Schema) plus the decision-specific supersession keys. An
# unknown target (a typo like `summry:`, or a made-up key) is a config error,
# not a silently-ignored no-op (re-i1n review, findings 1a/4).
VALID_MAP_TARGETS = frozenset(
    {
        "id",
        "title",
        "summary",
        "tags",
        "terms",
        "date",
        "status",
        "last-verified",
        "covers",
        "supersedes",
        "superseded-by",
    }
)

# `aliases:` is Hugo's URL-redirect list, never search vocabulary. Mapping it
# into either of ctx's vocabulary fields is a config error, not just an
# omission from the hugo preset (re-i1n review, finding 1b).
_ALIASES_FORBIDDEN_TARGETS = ("tags", "terms")


class _DuplicateKeyLoader(yaml.SafeLoader):
    """A YAML loader that rejects duplicate keys in any mapping, instead of
    PyYAML's default of silently keeping only the last one. Used for
    `config.yaml` itself (not the per-document frontmatter files, which are
    unrelated and still use `yaml.safe_load`): a `map:` block with the same
    target key twice — e.g. a copy-paste that meant to write two different
    targets — must fail loudly, not silently pick whichever line came last
    (re-i1n review, finding 1c)."""


def _no_duplicate_keys(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=False)
        if key in mapping:
            raise ValueError(f"config.yaml: duplicate key {key!r} in the same mapping")
        mapping[key] = loader.construct_object(value_node, deep=False)
    return mapping


_DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_keys
)


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
    # derive one from the file's path relative to the corpus root (every
    # directory segment kept, POSIX separators, no `.md` extension). Hugo
    # docs don't carry an `id:` key.
    derive_id: bool = False
    # If true, a document with a truthy `draft:` is excluded from indexing,
    # lint's contract checks, and duplicate-id detection — ctx must not
    # surface what the public site hides. Defaults to true for `type: hugo`;
    # `include_drafts: true` in config.yaml overrides it back to false for
    # any corpus (re-i1n review, finding 5).
    skip_drafts: bool = False


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


def _validate_field_map(corpus_name: str, field_map: dict[str, str]) -> None:
    for target, source in field_map.items():
        if target not in VALID_MAP_TARGETS:
            raise ValueError(
                f"corpus {corpus_name!r}: map target {target!r} is not a valid ctx "
                f"frontmatter key (allowed: {sorted(VALID_MAP_TARGETS)})"
            )
        if source == "aliases" and target in _ALIASES_FORBIDDEN_TARGETS:
            raise ValueError(
                f"corpus {corpus_name!r}: 'aliases' cannot be mapped into {target!r} — "
                "Hugo aliases are URL redirects, not search terms"
            )


def load_config(ctx_dir: Path | None = None) -> RigConfig:
    ctx_dir = ctx_dir or find_ctx_dir()
    rig_root = ctx_dir.parent
    text = (ctx_dir / CONFIG_FILENAME).read_text(encoding="utf-8")
    raw = yaml.load(text, Loader=_DuplicateKeyLoader) or {}
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
        _validate_field_map(name, field_map)

        derive_id = bool(spec.get("derive_id", ctype == "hugo"))
        skip_drafts = ctype == "hugo"
        if spec.get("include_drafts") is not None:
            skip_drafts = not bool(spec["include_drafts"])

        corpora.append(
            CorpusSpec(
                name=name,
                type=ctype,
                path=path.resolve(),
                field_map=field_map,
                derive_id=derive_id,
                skip_drafts=skip_drafts,
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
    frontmatter: for each `target: source` pair in `field_map`, if `target`
    is absent or empty in `raw`, fill it from `source` (when `source` is
    itself present and non-empty).

    The hand-authored key always wins: an explicit `summary:` already in the
    file is the product — never generated, per the memo — so the map only
    fills what the author didn't set, and never clobbers it with the mapped
    source (re-i1n review, finding 2). A Hugo doc that hand-writes its own
    `summary:` alongside `description:` keeps the hand-written one.

    Additive, not destructive: `raw` itself (and the file it came from) is
    never mutated, and `source` keys are left in place — so e.g. a Hugo
    file's `description:`/`keywords:` stay exactly as Hugo reads them; only
    the copy ctx builds its index from gains `summary:`/`tags:`. A key not
    named in `field_map` (Hugo's `aliases:`, for one) is never touched."""
    if not field_map or not raw:
        return raw
    mapped = dict(raw)
    for target, source in field_map.items():
        if raw.get(target) not in (None, "", []):
            continue  # hand-authored: wins, never overwritten by the map
        value = raw.get(source)
        if value not in (None, "", []):
            mapped[target] = value
    return mapped


def derive_id_from_path(spec: CorpusSpec, path: Path) -> str:
    """`id` for a document that doesn't set one: its path relative to the
    corpus root with the `.md` suffix removed — every directory segment
    kept (e.g. `survey/1-241`, not just `1-241`), POSIX separators. Directory
    segments are kept on purpose: `a/index.md` and `b/index.md` must not
    collide on a bare stem (re-i1n review, finding 7). Stable, and globally
    unique as long as no two corpora share a relative path."""
    rel = path.resolve().relative_to(spec.path.resolve())
    return rel.with_suffix("").as_posix()


def is_draft_skipped(spec: CorpusSpec, doc: dict) -> bool:
    """True if `doc` is a draft this corpus is configured to hide — ctx must
    not surface what the public site hides (re-i1n review, finding 5)."""
    return bool(spec.skip_drafts) and bool(doc.get("draft"))


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
