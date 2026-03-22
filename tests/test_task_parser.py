from __future__ import annotations

import pytest

from shipyard.exceptions import TaskParseError
from shipyard.task_parser import mark_task_done, mark_task_pending, parse_tasks


def test_parse_tasks_with_done_and_undone_items(repo_factory):
    repo = repo_factory(
        "# TASKS\n\n- [ ] task-001 First task\n- [x] task-002 Second task\n"
    )

    tasks = parse_tasks(repo / "docs" / "TASKS.md")

    assert [task.task_id for task in tasks] == ["task-001", "task-002"]
    assert [task.done for task in tasks] == [False, True]
    assert [task.module_id for task in tasks] == ["module-general", "module-general"]


def test_parse_tasks_with_module_sections(repo_factory):
    repo = repo_factory(
        "\n".join(
            [
                "# TASKS",
                "",
                "## module-auth 账号与认证模块",
                "",
                "- [ ] auth-001 实现登录接口",
                "- [x] auth-002 补充登录页测试",
                "",
                "## module-workspace 工作台模块",
                "",
                "- [ ] workspace-001 实现工作台首页",
            ]
        )
        + "\n"
    )

    tasks = parse_tasks(repo / "docs" / "TASKS.md")

    assert [task.task_id for task in tasks] == ["auth-001", "auth-002", "workspace-001"]
    assert [task.module_id for task in tasks] == [
        "module-auth",
        "module-auth",
        "module-workspace",
    ]
    assert [task.module_title for task in tasks] == [
        "账号与认证模块",
        "账号与认证模块",
        "工作台模块",
    ]


def test_parse_tasks_with_module_dependencies(repo_factory):
    repo = repo_factory(
        "\n".join(
            [
                "# TASKS",
                "",
                "## module-auth 账号与认证模块",
                "depends_on: none",
                "- [ ] auth-001 实现登录接口",
                "",
                "## module-workspace 工作台模块",
                "depends_on: module-auth, module-billing",
                "- [ ] workspace-001 实现工作台首页",
            ]
        )
        + "\n"
    )

    tasks = parse_tasks(repo / "docs" / "TASKS.md")

    assert tasks[0].module_dependencies == []
    assert tasks[1].module_dependencies == ["module-auth", "module-billing"]


def test_parse_tasks_raises_on_duplicate_module_id(repo_factory):
    repo = repo_factory(
        "\n".join(
            [
                "# TASKS",
                "",
                "## module-auth 账号与认证模块",
                "- [ ] auth-001 实现登录接口",
                "",
                "## module-auth 重复模块",
                "- [ ] auth-002 补充测试",
            ]
        )
        + "\n"
    )

    with pytest.raises(TaskParseError, match="Duplicate module id"):
        parse_tasks(repo / "docs" / "TASKS.md")


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


def test_mark_task_pending_reverts_markdown(repo_factory):
    repo = repo_factory("# TASKS\n\n- [x] task-001 First task\n")
    tasks_file = repo / "docs" / "TASKS.md"

    mark_task_pending(tasks_file, "task-001")
    tasks = parse_tasks(tasks_file)

    assert tasks[0].done is False
    assert "- [ ] task-001 First task" in tasks_file.read_text(encoding="utf-8")
