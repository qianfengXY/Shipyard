from __future__ import annotations

from shipyard.engine import ShipyardEngine


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
    assert payload["tasks"][0]["builder_status"] == "SELF_TEST_PASSED"
    assert payload["tasks"][0]["verifier_status"] is None
