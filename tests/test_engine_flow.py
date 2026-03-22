from __future__ import annotations

from shipyard.engine import ShipyardEngine


def test_engine_completes_full_mock_flow(repo_factory):
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
    state = engine.run()

    assert state.phase == "COMPLETED"
    tasks_text = (repo / "docs" / "TASKS.md").read_text(encoding="utf-8")
    assert "- [x] task-001 First task" in tasks_text
    assert "- [x] task-002 Second task" in tasks_text
    assert (repo / ".shipyard" / "artifacts" / "builder" / "task-001-result.json").exists()
    assert (repo / ".shipyard" / "artifacts" / "verifier" / "task-002-review.json").exists()
