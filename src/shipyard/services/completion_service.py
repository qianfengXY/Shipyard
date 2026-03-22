"""Final review helpers."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from shipyard.config import AppConfig
from shipyard.exceptions import FinalReviewError
from shipyard.logger import RunLogger
from shipyard.models import TaskItem


@dataclass
class FinalReviewOutcome:
    passed: bool
    summary: str
    command_results: list[dict] = field(default_factory=list)


def all_tasks_completed(tasks: list[TaskItem]) -> bool:
    return all(task.done for task in tasks)


def run_final_review(
    root: Path,
    config: AppConfig,
    tasks: list[TaskItem],
    current_task_id: str | None,
    logger: RunLogger,
) -> FinalReviewOutcome:
    if current_task_id is not None:
        raise FinalReviewError("Final review cannot start while a task is still active.")
    if not all_tasks_completed(tasks):
        raise FinalReviewError("Final review cannot pass while TASKS.md still has unchecked tasks.")

    command_results: list[dict] = []
    for command in config.final_review_commands:
        resolved_command = _resolve_command(command)
        completed = subprocess.run(
            resolved_command,
            shell=True,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        result = {
            "command": command,
            "resolved_command": resolved_command,
            "exit_code": completed.returncode,
            "passed": completed.returncode == 0,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
        command_results.append(result)
        logger.log(
            f"Final review command={resolved_command!r} exit_code={completed.returncode}"
        )
        if completed.returncode != 0:
            raise FinalReviewError(
                f"Final review command failed: {resolved_command} (exit_code={completed.returncode})"
            )

    return FinalReviewOutcome(
        passed=True,
        summary="Final review passed.",
        command_results=command_results,
    )


def _resolve_command(command: str) -> str:
    if command == "python":
        return f'"{sys.executable}"'
    if command.startswith("python "):
        return f'"{sys.executable}" {command[len("python "):]}'
    return command
