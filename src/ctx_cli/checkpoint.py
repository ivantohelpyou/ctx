"""`ctx checkpoint` — append session state to `.ctx/checkpoints/current.md`.

Plain file, on purpose (plan: "Checkpoints stay a plain file... a file costs
nothing and `gt mail` costs a Dolt commit per message"). 200-300 tokens is the
target size for one entry; nothing here enforces that, it is on the caller.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .corpus import RigConfig


def append_checkpoint(config: RigConfig, note: str) -> Path:
    config.checkpoints_dir.mkdir(parents=True, exist_ok=True)
    path = config.checkpoints_dir / "current.md"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = f"## {ts}\n{note.strip()}\n\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)
    return path
