"""Parse the YAML frontmatter block at the top of a markdown file.

No auto-generation, ever (memo: "do not auto-generate it"). This module only reads
what a human wrote. A document with no frontmatter block returns an empty dict;
callers decide whether that is a lint error.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml

FRONTMATTER_DELIM = "---"


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
    """Frontmatter fields like tags/terms/covers may be a YAML list or a bare string."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]
