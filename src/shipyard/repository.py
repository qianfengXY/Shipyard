"""Repository path helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepositoryPaths:
    root: Path

    @property
    def docs_dir(self) -> Path:
        return self.root / "docs"

    @property
    def tasks_file(self) -> Path:
        return self.docs_dir / "TASKS.md"

    @property
    def prd_file(self) -> Path:
        return self.docs_dir / "PRD.md"

    @property
    def dev_spec_file(self) -> Path:
        return self.docs_dir / "DEV_SPEC.md"

    @property
    def acceptance_spec_file(self) -> Path:
        return self.docs_dir / "ACCEPTANCE_SPEC.md"

    @property
    def shipyard_dir(self) -> Path:
        return self.root / ".shipyard"

    @property
    def config_file(self) -> Path:
        return self.shipyard_dir / "config.json"

    @property
    def state_file(self) -> Path:
        return self.shipyard_dir / "state.json"

    @property
    def run_log_file(self) -> Path:
        return self.shipyard_dir / "run.log"

    @property
    def claude_log_file(self) -> Path:
        return self.shipyard_dir / "claude.log"

    @property
    def codex_log_file(self) -> Path:
        return self.shipyard_dir / "codex.log"

    @property
    def artifacts_dir(self) -> Path:
        return self.shipyard_dir / "artifacts"

    @property
    def builder_artifacts_dir(self) -> Path:
        return self.artifacts_dir / "builder"

    @property
    def verifier_artifacts_dir(self) -> Path:
        return self.artifacts_dir / "verifier"

    def ensure_runtime_dirs(self) -> None:
        self.shipyard_dir.mkdir(parents=True, exist_ok=True)
        self.builder_artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.verifier_artifacts_dir.mkdir(parents=True, exist_ok=True)

    def relative_to_root(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()
