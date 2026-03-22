"""Artifact persistence helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from shipyard.repository import RepositoryPaths


class HandoffService:
    def __init__(self, paths: RepositoryPaths) -> None:
        self.paths = paths

    def save_builder_result(self, result: dict) -> tuple[Path, str]:
        return self._save_json(
            self.paths.builder_artifacts_dir / f"{result['task_id']}-result.json",
            result,
        )

    def save_verifier_result(self, result: dict) -> tuple[Path, str]:
        return self._save_json(
            self.paths.verifier_artifacts_dir / f"{result['task_id']}-review.json",
            result,
        )

    def load_builder_result(self, task_id: str) -> dict | None:
        return self._load_json(self.paths.builder_artifacts_dir / f"{task_id}-result.json")

    def load_verifier_result(self, task_id: str) -> dict | None:
        return self._load_json(self.paths.verifier_artifacts_dir / f"{task_id}-review.json")

    def _save_json(self, path: Path, payload: dict) -> tuple[Path, str]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
        return path, self.paths.relative_to_root(path)

    @staticmethod
    def _load_json(path: Path) -> dict | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
