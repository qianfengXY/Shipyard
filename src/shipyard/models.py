"""Core dataclasses for Shipyard."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum


class Phase(StrEnum):
    INIT = "INIT"
    SELECT_TASK = "SELECT_TASK"
    BUILDER_RUNNING = "BUILDER_RUNNING"
    READY_FOR_VERIFICATION = "READY_FOR_VERIFICATION"
    VERIFIER_RUNNING = "VERIFIER_RUNNING"
    TASK_DONE = "TASK_DONE"
    FINAL_REVIEW = "FINAL_REVIEW"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"


@dataclass
class TaskItem:
    task_id: str
    title: str
    done: bool


@dataclass
class OrchestratorState:
    run_id: str
    phase: str
    current_task_id: str | None
    current_task_title: str | None
    builder_attempt: int
    verifier_attempt: int
    final_review_attempt: int
    completed_task_ids: list[str] = field(default_factory=list)
    failed_task_ids: list[str] = field(default_factory=list)
    last_builder_result_path: str | None = None
    last_verifier_result_path: str | None = None
    last_error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "OrchestratorState":
        return cls(
            run_id=payload["run_id"],
            phase=payload["phase"],
            current_task_id=payload.get("current_task_id"),
            current_task_title=payload.get("current_task_title"),
            builder_attempt=int(payload.get("builder_attempt", 0)),
            verifier_attempt=int(payload.get("verifier_attempt", 0)),
            final_review_attempt=int(payload.get("final_review_attempt", 0)),
            completed_task_ids=list(payload.get("completed_task_ids", [])),
            failed_task_ids=list(payload.get("failed_task_ids", [])),
            last_builder_result_path=payload.get("last_builder_result_path"),
            last_verifier_result_path=payload.get("last_verifier_result_path"),
            last_error=payload.get("last_error"),
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
        )
