from __future__ import annotations

from shipyard.models import TaskItem
from shipyard.services.task_selector import build_task_queue


def test_build_task_queue_respects_module_dependencies():
    tasks = [
        TaskItem(
            task_id="dashboard-001",
            title="First dashboard task",
            done=True,
            module_id="module-dashboard",
            module_title="Dashboard",
        ),
        TaskItem(
            task_id="dashboard-002",
            title="Second dashboard task",
            done=False,
            module_id="module-dashboard",
            module_title="Dashboard",
        ),
        TaskItem(
            task_id="visibility-001",
            title="Visibility task",
            done=False,
            module_id="module-visibility",
            module_title="Visibility",
            module_dependencies=["module-dashboard"],
        ),
        TaskItem(
            task_id="progress-001",
            title="Progress task",
            done=False,
            module_id="module-progress",
            module_title="Progress",
        ),
    ]

    queue = build_task_queue(tasks)

    assert [task.task_id for task in queue.ready] == ["dashboard-002", "progress-001"]
    assert queue.blocked_modules == {"module-visibility": ["module-dashboard"]}
    assert queue.active == []


def test_build_task_queue_excludes_active_task_from_ready_backlog():
    tasks = [
        TaskItem(
            task_id="dashboard-001",
            title="First dashboard task",
            done=False,
            module_id="module-dashboard",
            module_title="Dashboard",
        ),
        TaskItem(
            task_id="visibility-001",
            title="Visibility task",
            done=False,
            module_id="module-visibility",
            module_title="Visibility",
        ),
    ]

    queue = build_task_queue(tasks, active_task_id="dashboard-001")

    assert [task.task_id for task in queue.active] == ["dashboard-001"]
    assert [task.task_id for task in queue.ready] == ["visibility-001"]
