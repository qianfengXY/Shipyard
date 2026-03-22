from __future__ import annotations

import pytest

from shipyard.exceptions import TaskParseError
from shipyard.task_parser import mark_task_done, parse_tasks


def test_parse_tasks_with_done_and_undone_items(repo_factory):
    repo = repo_factory(
        "# TASKS\n\n- [ ] task-001 First task\n- [x] task-002 Second task\n"
    )

    tasks = parse_tasks(repo / "docs" / "TASKS.md")

    assert [task.task_id for task in tasks] == ["task-001", "task-002"]
    assert [task.done for task in tasks] == [False, True]


def test_parse_tasks_raises_on_duplicate_task_id(repo_factory):
    repo = repo_factory(
        "# TASKS\n\n- [ ] task-001 First task\n- [x] task-001 Duplicate task\n"
    )

    with pytest.raises(TaskParseError, match="Duplicate task id"):
        parse_tasks(repo / "docs" / "TASKS.md")


def test_mark_task_done_updates_markdown(repo_factory):
    repo = repo_factory("# TASKS\n\n- [ ] task-001 First task\n")
    tasks_file = repo / "docs" / "TASKS.md"

    mark_task_done(tasks_file, "task-001")
    tasks = parse_tasks(tasks_file)

    assert tasks[0].done is True
    assert "- [x] task-001 First task" in tasks_file.read_text(encoding="utf-8")
