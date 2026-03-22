"""Task selection helpers."""

from __future__ import annotations

from shipyard.models import TaskItem


def select_next_task(tasks: list[TaskItem]) -> TaskItem | None:
    for task in tasks:
        if not task.done:
            return task
    return None
