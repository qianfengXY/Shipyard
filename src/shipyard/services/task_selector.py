"""Task selection helpers."""

from __future__ import annotations

from dataclasses import dataclass

from shipyard.models import TaskItem


@dataclass
class TaskQueue:
    active: list[TaskItem]
    ready: list[TaskItem]
    blocked_modules: dict[str, list[str]]


def select_next_task(tasks: list[TaskItem]) -> TaskItem | None:
    queue = build_task_queue(tasks)
    return queue.ready[0] if queue.ready else None


def build_task_queue(tasks: list[TaskItem], active_task_id: str | None = None) -> TaskQueue:
    module_tasks: dict[str, list[TaskItem]] = {}
    module_order: list[str] = []
    for task in tasks:
        if task.module_id not in module_tasks:
            module_tasks[task.module_id] = []
            module_order.append(task.module_id)
        module_tasks[task.module_id].append(task)

    completed_modules = {
        module_id
        for module_id, items in module_tasks.items()
        if all(task.done for task in items)
    }

    active: list[TaskItem] = []
    ready: list[TaskItem] = []
    blocked_modules: dict[str, list[str]] = {}
    for module_id in module_order:
        items = module_tasks[module_id]
        next_task = next((task for task in items if not task.done), None)
        if next_task is None:
            continue
        if active_task_id and next_task.task_id == active_task_id:
            active.append(next_task)
            continue
        unmet_dependencies = [
            dependency
            for dependency in next_task.module_dependencies
            if dependency not in completed_modules
        ]
        if unmet_dependencies:
            blocked_modules[module_id] = unmet_dependencies
            continue
        ready.append(next_task)

    return TaskQueue(active=active, ready=ready, blocked_modules=blocked_modules)
