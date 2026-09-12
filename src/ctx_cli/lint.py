"""`ctx lint` — frontmatter contract + supersession graph integrity.

Per-corpus rules (plan §Schema):

  explainers: every `covers` path exists; `git log -1` on any covered path is
              not newer than `last-verified`; reports covers coverage % (docs
              that named at least one covered path vs. total docs).
  decisions:  reciprocal supersession; no cycles; no `accepted` doc that
              something else claims to supersede. (Contract + lint ship now;
              the supersession walker used by `ctx get --history` is Phase 3.)
  shared:     total corpus size stays under a fixed byte ceiling.
  all:        `id`, `title`, `summary` are required and non-empty.
"""

from __future__ import annotations

import dataclasses
import subprocess
from datetime import datetime
from pathlib import Path

from . import frontmatter as fm
from .corpus import (
    CorpusSpec,
    RigConfig,
    is_draft_skipped,
    iter_corpus_files,
    resolved_frontmatter,
)

# "small enough to be cheap to load from every rig" (memo) — a placeholder
# ceiling until a real budget is set; ctx lint reports the measured size either way.
SHARED_BYTE_CEILING = 200_000


@dataclasses.dataclass
class LintReport:
    errors: list[str] = dataclasses.field(default_factory=list)
    coverage: dict[str, float] = dataclasses.field(default_factory=dict)
    # Per-corpus count of documents excluded because `draft:` is truthy and
    # the corpus is configured to skip drafts (re-i1n review, finding 5) —
    # not an error, just visibility into what ctx is not indexing.
    skipped_drafts: dict[str, int] = dataclasses.field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def _git_last_commit_date(rig_root: Path, rel_path: str) -> datetime | None:
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", rel_path],
            cwd=rig_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = out.stdout.strip()
    if not line:
        return None
    try:
        return datetime.fromisoformat(line)
    except ValueError:
        return None


def _parse_date(value) -> datetime | None:
    if value is None:
        return None
    text = str(value)
    for candidate in (text, text + "T00:00:00+00:00"):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _load_docs(spec: CorpusSpec) -> list[tuple[Path, dict]]:
    """Frontmatter as ctx sees it: `spec.field_map` applied and `id` derived
    from the path where the corpus opts in (`resolved_frontmatter`) — the
    same view `ctx index` builds from, so lint checks the contract ctx
    actually reads, not the raw file (a Hugo doc's `description:` must not
    be flagged as a missing `summary:`)."""
    docs = []
    for path in iter_corpus_files(spec):
        raw = fm.parse_frontmatter(path.read_text(encoding="utf-8"))
        docs.append((path, resolved_frontmatter(spec, path, raw)))
    return docs


def _lint_common(spec: CorpusSpec, path: Path, doc: dict, errors: list[str]) -> None:
    rel = path
    for field in ("id", "title", "summary"):
        if not doc.get(field):
            errors.append(f"{spec.name}:{rel}: missing required frontmatter field '{field}'")

    # date / last-verified must be parseable if present at all — an
    # unparseable value silently degraded to a raw string in the index
    # before this was caught anywhere (re-i1n review, finding 6).
    for field, value in (
        ("date", doc.get("date")),
        ("last-verified", doc.get("last-verified") or doc.get("last_verified")),
    ):
        if value is None:
            continue
        try:
            fm.normalize_date(value)
        except fm.DateParseError:
            errors.append(f"{spec.name}:{rel}: unparseable '{field}' value: {value!r}")


def _resolve_covers_entry(
    config: RigConfig, spec: CorpusSpec, doc_path: Path, cover: str, errors: list[str]
) -> list[Path] | None:
    """Resolve one `covers:` entry to the files it names. Returns None (having
    already appended an error) for anything that isn't a path relative to the
    rig root: absolute paths, `../` escapes, and glob patterns that raise.
    Returns a (possibly empty) list of matches otherwise."""
    if Path(cover).is_absolute():
        errors.append(f"{spec.name}:{doc_path}: covers path must be relative to rig root: {cover}")
        return None

    is_glob = any(c in cover for c in "*?[")
    try:
        if is_glob:
            matches = list(config.rig_root.glob(cover))
        else:
            candidate = config.rig_root / cover
            matches = [candidate] if candidate.exists() else []
    except (NotImplementedError, ValueError, OSError) as e:
        errors.append(f"{spec.name}:{doc_path}: invalid covers pattern '{cover}': {e}")
        return None

    if not matches:
        return []  # genuinely doesn't exist — let the caller report that

    rig_root_resolved = config.rig_root.resolve()
    safe_matches = []
    escaped = False
    for m in matches:
        try:
            m.resolve().relative_to(rig_root_resolved)
        except ValueError:
            errors.append(
                f"{spec.name}:{doc_path}: covers path resolves outside rig root: {cover}"
            )
            escaped = True
            continue
        safe_matches.append(m)
    if escaped and not safe_matches:
        return None  # already reported above; don't also report "does not exist"
    return safe_matches


def _lint_explainers(config: RigConfig, spec: CorpusSpec, docs, errors: list[str]) -> float:
    covered_count = 0
    for path, doc in docs:
        if not doc:
            continue
        covers = fm.as_list(doc.get("covers"))
        if covers:
            covered_count += 1
        last_verified = _parse_date(doc.get("last-verified") or doc.get("last_verified"))
        for cover in covers:
            matches = _resolve_covers_entry(config, spec, path, cover, errors)
            if matches is None:
                continue  # already reported: absolute, bad pattern, or escapes rig root
            if not matches:
                errors.append(f"{spec.name}:{path}: covers path does not exist: {cover}")
                continue
            if last_verified is None:
                errors.append(
                    f"{spec.name}:{path}: has 'covers' but no parseable 'last-verified'"
                )
                continue
            for m in matches:
                try:
                    rel_m = str(m.relative_to(config.rig_root))
                except ValueError:
                    rel_m = str(m)
                commit_date = _git_last_commit_date(config.rig_root, rel_m)
                if commit_date and commit_date.date() > last_verified.date():
                    errors.append(
                        f"{spec.name}:{path}: covered path '{rel_m}' changed "
                        f"({commit_date.date()}) after last-verified ({last_verified.date()})"
                    )
    total = len([d for _, d in docs if d])
    return (covered_count / total * 100.0) if total else 0.0


def _lint_decisions(spec: CorpusSpec, docs, errors: list[str]) -> None:
    by_id = {doc["id"]: doc for _, doc in docs if doc and doc.get("id")}
    for _, doc in docs:
        if not doc or not doc.get("id"):
            continue
        doc_id = doc["id"]
        for old_id in fm.as_list(doc.get("supersedes")):
            old_doc = by_id.get(old_id)
            if old_doc is None:
                errors.append(f"{spec.name}:{doc_id}: supersedes unknown id '{old_id}'")
                continue
            reciprocal = fm.as_list(old_doc.get("superseded-by") or old_doc.get("superseded_by"))
            if doc_id not in reciprocal:
                errors.append(
                    f"{spec.name}:{old_id}: missing reciprocal superseded-by '{doc_id}'"
                )
            if old_doc.get("status") == "accepted":
                errors.append(
                    f"{spec.name}:{old_id}: status 'accepted' but superseded by '{doc_id}'"
                )

    # Cycle detection over the supersedes graph.
    visiting: set[str] = set()
    visited: set[str] = set()

    def _visit(node_id: str, stack: list[str]) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            errors.append(f"{spec.name}: supersession cycle: {' -> '.join([*stack, node_id])}")
            return
        visiting.add(node_id)
        for nxt in fm.as_list((by_id.get(node_id) or {}).get("supersedes")):
            if nxt in by_id:
                _visit(nxt, [*stack, node_id])
        visiting.discard(node_id)
        visited.add(node_id)

    for doc_id in by_id:
        _visit(doc_id, [])


def decisions_structural_errors(config: RigConfig) -> list[str]:
    """Just the `decisions` graph checks (reciprocity, cycles, an `accepted`
    doc something else claims to supersede) — none of the explainers/shared
    checks, none of `_lint_common`'s required-field check, no `git log`
    calls. `ctx index` runs this on every build (warns on stderr and still
    publishes by default; `--strict` refuses to publish instead) so a
    corpus can drift out of structural shape without anyone running the
    slower, fuller `ctx lint` — this is the cheap subset that matters most
    for `ctx get --history`'s output to mean anything."""
    errors: list[str] = []
    for spec in config.corpora:
        if spec.type != "decisions":
            continue
        all_docs = _load_docs(spec)
        draft_paths = {path for path, doc in all_docs if doc and is_draft_skipped(spec, doc)}
        docs = [(path, doc) for path, doc in all_docs if path not in draft_paths]
        _lint_decisions(spec, docs, errors)
    return errors


def _lint_shared(spec: CorpusSpec, docs, errors: list[str]) -> None:
    total_bytes = sum(path.stat().st_size for path, _ in docs)
    if total_bytes > SHARED_BYTE_CEILING:
        errors.append(
            f"{spec.name}: corpus is {total_bytes} bytes, over the "
            f"{SHARED_BYTE_CEILING}-byte ceiling"
        )


def lint(config: RigConfig) -> LintReport:
    report = LintReport()
    seen_ids: dict[str, list[str]] = {}
    for spec in config.corpora:
        all_docs = _load_docs(spec)
        draft_paths = {path for path, doc in all_docs if doc and is_draft_skipped(spec, doc)}
        report.skipped_drafts[spec.name] = len(draft_paths)
        docs = [(path, doc) for path, doc in all_docs if path not in draft_paths]

        for path, doc in docs:
            if not doc:
                report.errors.append(f"{spec.name}:{path}: no parseable frontmatter")
                continue
            _lint_common(spec, path, doc, report.errors)
            if doc.get("id"):
                seen_ids.setdefault(str(doc["id"]), []).append(f"{spec.name}:{path}")

        if spec.type == "explainers":
            report.coverage[spec.name] = _lint_explainers(config, spec, docs, report.errors)
        elif spec.type == "decisions":
            _lint_decisions(spec, docs, report.errors)
        elif spec.type == "shared":
            _lint_shared(spec, docs, report.errors)

    for doc_id, locations in sorted(seen_ids.items()):
        if len(locations) > 1:
            report.errors.append(f"duplicate id {doc_id!r} used by: {', '.join(locations)}")
    return report
