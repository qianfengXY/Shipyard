from __future__ import annotations

from shipyard.engine import ShipyardEngine


def test_engine_resumes_from_existing_state_without_reinitializing(repo_factory):
    repo = repo_factory(
        "# TASKS\n\n- [ ] task-001 First task\n- [ ] task-002 Second task\n",
        config={
            "builder_adapter": "mock_builder",
            "verifier_adapter": "mock_verifier",
            "max_builder_retries": 3,
            "max_verifier_retries": 3,
            "final_review_commands": ["python -c \"print('ok')\""],
            "mock_builder_failures": {},
            "mock_verifier_failures": {},
        },
    )

    engine = ShipyardEngine(repo)
    initial_run_id = engine.store.load_or_init().run_id

    for _ in range(5):
        engine.step()

    paused_state = engine.store.load()
    assert paused_state.phase == "TASK_DONE"
    assert paused_state.completed_task_ids == ["task-001"]

    resumed_engine = ShipyardEngine(repo)
    final_state = resumed_engine.run()

    assert final_state.phase == "COMPLETED"
    assert final_state.run_id == initial_run_id
    assert final_state.completed_task_ids == ["task-001", "task-002"]
    tasks_text = (repo / "docs" / "TASKS.md").read_text(encoding="utf-8")
    assert tasks_text.count("- [x]") == 2


def test_engine_resumes_from_aborted_task_on_next_run(repo_factory):
    repo = repo_factory(
        "# TASKS\n\n- [ ] task-001 First task\n- [ ] task-002 Second task\n",
        config={
            "builder_adapter": "mock_builder",
            "verifier_adapter": "mock_verifier",
            "max_builder_retries": 1,
            "max_verifier_retries": 3,
            "final_review_commands": ["python -c \"print('ok')\""],
            "mock_builder_failures": {"task-001": 1},
            "mock_verifier_failures": {},
        },
    )

    engine = ShipyardEngine(repo)
    aborted_state = engine.run()

    assert aborted_state.phase == "ABORTED"
    assert aborted_state.current_task_id == "task-001"

    resumed_engine = ShipyardEngine(repo)
    resumed_engine.config.mock_builder_failures = {}
    resumed_engine.builder.failure_plan = {}
    final_state = resumed_engine.run()

    assert final_state.phase == "COMPLETED"
    assert final_state.completed_task_ids == ["task-001", "task-002"]


def test_engine_can_select_specific_pending_or_completed_task(repo_factory):
    repo = repo_factory(
        "\n".join(
            [
                "# TASKS",
                "",
                "- [x] task-001 First task",
                "- [ ] task-002 Second task",
            ]
        )
        + "\n",
        config={
            "builder_adapter": "mock_builder",
            "verifier_adapter": "mock_verifier",
            "max_builder_retries": 3,
            "max_verifier_retries": 3,
            "final_review_commands": ["python -c \"print('ok')\""],
            "mock_builder_failures": {},
            "mock_verifier_failures": {},
        },
    )

    engine = ShipyardEngine(repo)
    state = engine.select_task("task-002")

    assert state.current_task_id == "task-002"
    assert state.phase == "BUILDER_RUNNING"

    rerun_state = engine.select_task("task-001", force_rerun=True)

    assert rerun_state.current_task_id == "task-001"
    assert rerun_state.phase == "BUILDER_RUNNING"
    tasks_text = (repo / "docs" / "TASKS.md").read_text(encoding="utf-8")
    assert "- [ ] task-001 First task" in tasks_text


def test_engine_can_rerun_all_failed_tasks(repo_factory):
    repo = repo_factory(
        "# TASKS\n\n- [ ] task-001 First task\n- [ ] task-002 Second task\n",
        config={
            "builder_adapter": "mock_builder",
            "verifier_adapter": "mock_verifier",
            "max_builder_retries": 1,
            "max_verifier_retries": 3,
            "final_review_commands": ["python -c \"print('ok')\""],
            "mock_builder_failures": {"task-001": 1},
            "mock_verifier_failures": {},
        },
    )

    engine = ShipyardEngine(repo)
    aborted_state = engine.run()
    assert aborted_state.phase == "ABORTED"

    rerun_payload = engine.rerun_failed_tasks()

    assert rerun_payload["failed_task_ids"] == ["task-001"]
    assert rerun_payload["count"] == 1
    assert rerun_payload["phase"] == "SELECT_TASK"
