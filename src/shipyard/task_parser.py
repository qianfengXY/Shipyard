"""TASKS.md parsing helpers."""

from __future__ import annotations

import os
import re
from pathlib import Path
import tempfile

from shipyard.exceptions import TaskParseError
from shipyard.models import TaskItem

MODULE_PATTERN = re.compile(r"^##\s+(\S+)\s+(.+)$")
MODULE_DEPENDS_PATTERN = re.compile(r"^depends_on:\s*(.+)$", re.IGNORECASE)
TASK_PATTERN = re.compile(r"^- \[( |x)\] (\S+) (.+)$")


def parse_tasks(tasks_file: Path) -> list[TaskItem]:
    if not tasks_file.exists():
        raise TaskParseError("docs/TASKS.md does not exist.")

    lines = tasks_file.read_text(encoding="utf-8").splitlines()
    tasks: list[TaskItem] = []
    seen_ids: set[str] = set()
    seen_modules: set[str] = set()
    current_module_id = "module-general"
    current_module_title = "General"
    current_module_dependencies: list[str] = []

    for line in lines:
        stripped = line.strip()
        module_match = MODULE_PATTERN.match(stripped)
        if module_match:
            current_module_id, current_module_title = module_match.groups()
            current_module_dependencies = []
            if current_module_id in seen_modules:
                raise TaskParseError(f"Duplicate module id found: {current_module_id}")
            seen_modules.add(current_module_id)
            continue

        depends_match = MODULE_DEPENDS_PATTERN.match(stripped)
        if depends_match:
            raw_dependencies = depends_match.group(1).strip()
            if raw_dependencies.lower() in {"", "none", "-"}:
                current_module_dependencies = []
            else:
                current_module_dependencies = [
                    item.strip()
                    for item in raw_dependencies.split(",")
                    if item.strip()
                ]
            continue

        match = TASK_PATTERN.match(stripped)
        if not match:
            continue
        marker, task_id, title = match.groups()
        if task_id in seen_ids:
            raise TaskParseError(f"Duplicate task id found: {task_id}")
        seen_ids.add(task_id)
        tasks.append(
            TaskItem(
                task_id=task_id,
                title=title.strip(),
                done=marker == "x",
                module_id=current_module_id,
                module_title=current_module_title,
                module_dependencies=list(current_module_dependencies),
            )
        )

    if not tasks:
        raise TaskParseError("docs/TASKS.md is empty or does not contain valid task items.")

    return tasks


def mark_task_done(tasks_file: Path, task_id: str) -> None:
    _rewrite_task_marker(tasks_file, task_id, target_marker="x")


def mark_task_pending(tasks_file: Path, task_id: str) -> None:
    _rewrite_task_marker(tasks_file, task_id, target_marker=" ")


def _rewrite_task_marker(tasks_file: Path, task_id: str, *, target_marker: str) -> None:
    if not tasks_file.exists():
        raise TaskParseError("docs/TASKS.md does not exist.")

    lines = tasks_file.read_text(encoding="utf-8").splitlines()
    updated = False
    rewritten: list[str] = []

    for line in lines:
        match = TASK_PATTERN.match(line.strip())
        if not match:
            rewritten.append(line)
            continue
        marker, found_id, title = match.groups()
        if found_id == task_id and marker != target_marker:
            rewritten.append(f"- [{target_marker}] {found_id} {title}")
            updated = True
        else:
            rewritten.append(line)

    if not updated and task_id not in {task.task_id for task in parse_tasks(tasks_file)}:
        raise TaskParseError(f"Task id not found: {task_id}")

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=tasks_file.parent,
        prefix=f"{tasks_file.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write("\n".join(rewritten) + "\n")
        temp_path = Path(handle.name)
    os.replace(temp_path, tasks_file)
