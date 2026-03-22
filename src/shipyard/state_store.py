"""State persistence with atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from shipyard.exceptions import StateStoreError
from shipyard.models import OrchestratorState, Phase
from shipyard.repository import RepositoryPaths


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_run_id() -> str:
    return datetime.now().astimezone().strftime("run_%Y_%m_%d_%H%M%S")


class StateStore:
    def __init__(self, paths: RepositoryPaths) -> None:
        self.paths = paths

    def load_or_init(self) -> OrchestratorState:
        self.paths.ensure_runtime_dirs()
        if self.paths.state_file.exists():
            return self.load()

        now = _now_iso()
        state = OrchestratorState(
            run_id=_new_run_id(),
            phase=Phase.INIT.value,
            current_task_id=None,
            current_task_title=None,
            builder_attempt=0,
            verifier_attempt=0,
            final_review_attempt=0,
            completed_task_ids=[],
            failed_task_ids=[],
            last_builder_result_path=None,
            last_verifier_result_path=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
        self.save(state)
        return state

    def load(self) -> OrchestratorState:
        try:
            payload = json.loads(self.paths.state_file.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise StateStoreError("State file does not exist.") from exc
        except json.JSONDecodeError as exc:
            raise StateStoreError(f"State file is corrupted: {exc}") from exc

        try:
            return OrchestratorState.from_dict(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise StateStoreError(f"State file is invalid: {exc}") from exc

    def save(self, state: OrchestratorState) -> None:
        self.paths.ensure_runtime_dirs()
        state.updated_at = _now_iso()
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.paths.state_file.parent,
            prefix=f"{self.paths.state_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temp_path = Path(handle.name)
        os.replace(temp_path, self.paths.state_file)

    def reset(self) -> None:
        self.paths.ensure_runtime_dirs()
        if self.paths.state_file.exists():
            self.paths.state_file.unlink()
        if self.paths.run_log_file.exists():
            self.paths.run_log_file.unlink()
        if self.paths.claude_log_file.exists():
            self.paths.claude_log_file.unlink()
        if self.paths.codex_log_file.exists():
            self.paths.codex_log_file.unlink()
        if self.paths.artifacts_dir.exists():
            for path in sorted(self.paths.artifacts_dir.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
        self.paths.builder_artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.paths.verifier_artifacts_dir.mkdir(parents=True, exist_ok=True)
