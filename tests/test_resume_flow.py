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
