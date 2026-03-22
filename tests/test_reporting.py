from __future__ import annotations

from shipyard.engine import ShipyardEngine
from shipyard.main import _render_coordinator_timeline


def test_report_payload_includes_task_and_artifact_statuses(repo_factory):
    repo = repo_factory(
        "# TASKS\n\n- [ ] task-001 First task\n",
        config={
            "builder_adapter": "mock_builder",
            "verifier_adapter": "mock_verifier",
            "max_builder_retries": 3,
            "max_verifier_retries": 3,
            "final_review_commands": ["python -c \"print('ok')\""],
            "claude_command": "claude",
            "codex_command": "codex",
            "mock_builder_failures": {},
            "mock_verifier_failures": {},
        },
    )
    engine = ShipyardEngine(repo)

    engine.step()
    engine.step()
    engine.step()

    payload = engine.report_payload()

    assert payload["run"]["phase"] == "READY_FOR_VERIFICATION"
    assert payload["agents"]["builder"]["status"] == "done"
    assert payload["agents"]["verifier"]["status"] == "queued"
    assert payload["recent_events"]
    assert "claude" in payload["recent_events_by_agent"]
    assert "codex" in payload["recent_events_by_agent"]
    assert payload["tasks"][0]["task_id"] == "task-001"
    assert payload["task_window"][0]["task_id"] == "task-001"
    assert payload["task_records_dir"] == ".shipyard/task_records"
    assert payload["tasks"][0]["module_id"] == "module-general"
    assert payload["tasks"][0]["module_title"] == "General"
    assert payload["progress"]["total_modules"] == 1
    assert payload["progress"]["total_tasks"] == 1
    assert payload["progress"]["completed_tasks"] == 0
    assert payload["progress"]["failed_tasks"] == 0
    assert payload["progress"]["current_module_id"] == "module-general"
    assert payload["tasks"][0]["builder_status"] == "SELF_TEST_PASSED"
    assert payload["tasks"][0]["verifier_status"] is None
    assert payload["queue"]["active"][0]["task_id"] == "task-001"
    assert payload["scheduler"]["max_parallel_modules"] == 2
    assert payload["scheduler"]["lanes"][0]["status"] == "running"
    assert payload["scheduler"]["lanes"][0]["task_id"] == "task-001"
    assert (repo / ".shipyard" / "task_records" / "task-001.json").exists()

    builder_artifact = repo / ".shipyard" / "artifacts" / "builder" / "task-001-result.json"
    builder_artifact.unlink()
    fallback_payload = engine.report_payload()
    assert fallback_payload["tasks"][0]["builder_status"] == "SELF_TEST_PASSED"


def test_report_payload_limits_task_window_to_previous_current_and_next_three(repo_factory):
    repo = repo_factory(
        "\n".join(
            [
                "# TASKS",
                "",
                "- [x] task-001 First task",
                "- [x] task-002 Second task",
                "- [ ] task-003 Third task",
                "- [ ] task-004 Fourth task",
                "- [ ] task-005 Fifth task",
                "- [ ] task-006 Sixth task",
                "- [ ] task-007 Seventh task",
            ]
        )
        + "\n",
        config={
            "builder_adapter": "mock_builder",
            "verifier_adapter": "mock_verifier",
            "max_builder_retries": 3,
            "max_verifier_retries": 3,
            "final_review_commands": ["python -c \"print('ok')\""],
            "claude_command": "claude",
            "codex_command": "codex",
            "mock_builder_failures": {},
            "mock_verifier_failures": {},
        },
    )
    engine = ShipyardEngine(repo)
    engine.step()
    engine.step()

    payload = engine.report_payload()

    assert [task["task_id"] for task in payload["task_window"]] == [
        "task-002",
        "task-003",
        "task-004",
        "task-005",
        "task-006",
    ]


def test_coordinator_timeline_formats_verifier_running_state():
    payload = {
        "run": {
            "phase": "VERIFIER_RUNNING",
            "current_task_id": "task-003",
            "last_error": None,
        },
        "task_window": [
            {
                "task_id": "task-002",
                "task_status": "done",
            },
            {
                "task_id": "task-003",
                "task_status": "active",
            },
            {
                "task_id": "task-004",
                "task_status": "pending",
            },
        ],
        "recent_shipyard_events": [
            "phase=READY_FOR_VERIFICATION -> VERIFIER_RUNNING task_id=task-003",
            "dispatch verifier=Codex task_id=task-003 builder=idle",
        ],
    }

    lines = _render_coordinator_timeline(payload, use_color=False)

    assert any("State Machine" in line for line in lines)
    assert any("VERIFIER_RUNNING  Codex verifies task-003" in line for line in lines)
    assert any("Transition Trace" in line for line in lines)
    assert any("Task          Select" in line for line in lines)
    assert any(line.startswith("task-003") and "[>]" in line for line in lines)
    assert any("Recent Flow" in line for line in lines)
    assert any("READY_FOR_VERIFICATION -> VERIFIER_RUNNING (task-003)" in line for line in lines)
    assert any("Dispatch Codex verifier for task-003." in line for line in lines)


def test_coordinator_timeline_formats_completed_state():
    payload = {
        "run": {
            "phase": "COMPLETED",
            "current_task_id": None,
            "last_error": None,
        },
        "task_window": [
            {
                "task_id": "task-001",
                "task_status": "done",
            },
        ],
        "recent_shipyard_events": [
            "Final review command='python -m pytest' exit_code=0",
            "phase=FINAL_REVIEW -> COMPLETED task_id=None",
        ],
    }

    lines = _render_coordinator_timeline(payload, use_color=False)

    assert any("COMPLETED  Finish the run successfully" in line for line in lines)
    assert any("FINAL_REVIEW -> COMPLETED" in line for line in lines)
    assert any("Final review: command='python -m pytest' exit_code=0" in line for line in lines)


def test_report_payload_includes_failed_task_summary(repo_factory):
    repo = repo_factory(
        "# TASKS\n\n- [ ] task-001 First task\n",
        config={
            "builder_adapter": "mock_builder",
            "verifier_adapter": "mock_verifier",
            "max_builder_retries": 1,
            "max_verifier_retries": 3,
            "final_review_commands": ["python -c \"print('ok')\""],
            "claude_command": "claude",
            "codex_command": "codex",
            "mock_builder_failures": {"task-001": 1},
            "mock_verifier_failures": {},
        },
    )
    engine = ShipyardEngine(repo)

    final_state = engine.run()
    assert final_state.phase == "ABORTED"

    payload = engine.report_payload()

    assert payload["progress"]["failed_tasks"] == 1
    assert payload["progress"]["failed_task_ids"] == ["task-001"]
    assert payload["failed_tasks"][0]["task_id"] == "task-001"


def test_report_payload_exposes_ready_queue_and_blocked_modules(repo_factory):
    repo = repo_factory(
        "\n".join(
            [
                "# TASKS",
                "",
                "## module-dashboard Dashboard",
                "- [ ] dashboard-001 First dashboard task",
                "",
                "## module-visibility Visibility",
                "depends_on: module-dashboard",
                "- [ ] visibility-001 Visibility task",
                "",
                "## module-progress Progress",
                "- [ ] progress-001 Progress task",
            ]
        )
        + "\n",
        config={
            "builder_adapter": "mock_builder",
            "verifier_adapter": "mock_verifier",
            "max_builder_retries": 3,
            "max_verifier_retries": 3,
            "max_parallel_modules": 3,
            "final_review_commands": ["python -c \"print('ok')\""],
            "claude_command": "claude",
            "codex_command": "codex",
            "mock_builder_failures": {},
            "mock_verifier_failures": {},
        },
    )
    engine = ShipyardEngine(repo)

    engine.step()
    engine.step()
    payload = engine.report_payload()

    assert payload["run"]["phase"] == "BUILDER_RUNNING"
    assert payload["queue"]["active"][0]["task_id"] == "dashboard-001"
    assert [task["task_id"] for task in payload["queue"]["ready"]] == ["progress-001"]
    assert payload["queue"]["blocked_modules"] == [
        {
            "module_id": "module-visibility",
            "blocked_by": ["module-dashboard"],
        }
    ]
    assert payload["scheduler"]["max_parallel_modules"] == 3
    assert payload["scheduler"]["ready_count"] == 1
    assert payload["scheduler"]["blocked_count"] == 1
    assert payload["scheduler"]["lanes"][1]["status"] == "ready"
    assert payload["scheduler"]["lanes"][1]["task_id"] == "progress-001"
