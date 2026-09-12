from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ctx_cli.corpus import load_config

FIXTURE_RIG = Path(__file__).parent / "fixtures" / "rig"


@pytest.fixture
def rig_dir(tmp_path) -> Path:
    """A fresh copy of the fixture rig per test, so tests never write into the
    committed fixture and never depend on this repo's own git history."""
    dest = tmp_path / "rig"
    shutil.copytree(FIXTURE_RIG, dest)
    return dest


@pytest.fixture
def config(rig_dir):
    return load_config(rig_dir / ".ctx")
