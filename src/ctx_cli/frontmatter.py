"""Parse the YAML frontmatter block at the top of a markdown file.

No auto-generation, ever (memo: "do not auto-generate it"). This module only reads
what a human wrote. A document with no frontmatter block returns an empty dict;
callers decide whether that is a lint error.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
from pathlib import Path

import yaml

FRONTMATTER_DELIM = "---"
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ]")


class DateParseError(ValueError):
    """Raised by `normalize_date` for a value that isn't a recognizable date."""


@dataclasses.dataclass
class ParsedDoc:
    path: Path
    frontmatter: dict
    body: str


def parse_file(path: Path) -> ParsedDoc:
    text = path.read_text(encoding="utf-8")
    return ParsedDoc(path=path, frontmatter=parse_frontmatter(text), body=strip_frontmatter(text))


def parse_frontmatter(text: str) -> dict:
    """Return the parsed frontmatter dict, or {} if there is none / it fails to parse."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_DELIM:
        return {}
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == FRONTMATTER_DELIM:
            end = i
            break
    if end is None:
        return {}
    block = "\n".join(lines[1:end])
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def strip_frontmatter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_DELIM:
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() == FRONTMATTER_DELIM:
            return "\n".join(lines[i + 1 :])
    return text


def as_list(value) -> list[str]:
    """Frontmatter fields like covers/supersedes/superseded-by may be a YAML
    list or a bare string. A bare string is kept as one value — a comma could
    legitimately be part of a single glob or id — unlike `as_tag_list`."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def as_tag_list(value) -> list[str]:
    """Like `as_list`, for the two fields (`tags`, `terms`) that are written
    either as a YAML list or as a comma-separated string — Hugo's
    `keywords: "a, b, c"` and `keywords: ["a", "b", "c"]` must index to the
    same tags (re-i1n review, finding 3). A YAML list is taken as-is (each
    item already one value, never itself re-split); a bare string is split
    on commas, each piece stripped, empties dropped."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    text = str(value)
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return [text] if text.strip() else []


def normalize_date(value) -> str:
    """Normalize a frontmatter date-shaped value to plain `YYYY-MM-DD`:

    - a YAML `date` (PyYAML gives `datetime.date` for `date: 2026-01-01`)
    - a YAML `datetime` (PyYAML gives `datetime.datetime`, tz-aware or not,
      for `date: 2026-01-01T10:00:00-07:00`) — its *local* date as written,
      the calendar date in the string, never shifted by converting the
      timezone
    - a plain string in either of the above shapes, or already `YYYY-MM-DD`

    Raises `DateParseError` for anything else — callers decide whether that's
    an indexing fallback or a lint error (re-i1n review, finding 6)."""
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value).strip()
    if _DATE_ONLY_RE.match(text):
        return text
    m = _DATE_PREFIX_RE.match(text)
    if m:
        return m.group(1)
    raise DateParseError(f"unparseable date: {value!r}")
