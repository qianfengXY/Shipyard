from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def repo_factory(tmp_path: Path):
    def create_repo(
        tasks_text: str,
        *,
        config: dict | None = None,
    ) -> Path:
        (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".shipyard").mkdir(parents=True, exist_ok=True)
        (tmp_path / "docs" / "TASKS.md").write_text(tasks_text, encoding="utf-8")
        (tmp_path / "docs" / "PRD.md").write_text("# PRD\n", encoding="utf-8")
        (tmp_path / "docs" / "DEV_SPEC.md").write_text("# DEV\n", encoding="utf-8")
        (tmp_path / "docs" / "ACCEPTANCE_SPEC.md").write_text(
            "# ACCEPTANCE\n",
            encoding="utf-8",
        )
        if config is not None:
            (tmp_path / ".shipyard" / "config.json").write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return tmp_path

    return create_repo
