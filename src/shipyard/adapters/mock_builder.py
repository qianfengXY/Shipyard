"""Mock builder adapter."""

from __future__ import annotations

from datetime import datetime


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class MockBuilder:
    def __init__(self, failure_plan: dict[str, int] | None = None) -> None:
        self.failure_plan = failure_plan or {}

    def run(
        self,
        task_id: str,
        task_title: str,
        docs_context: dict,
        prior_review: dict | None,
        state: dict,
    ) -> dict:
        attempt = int(state.get("builder_attempt", 1))
        planned_failures = self.failure_plan.get(task_id, 0)
        should_fail = attempt <= planned_failures
        status = "SELF_TEST_FAILED" if should_fail else "SELF_TEST_PASSED"
        summary = f"Mock builder {'failed' if should_fail else 'passed'} self-test for {task_id}."
        if prior_review and prior_review.get("summary"):
            summary = f"{summary} Prior review: {prior_review['summary']}"

        passed = status == "SELF_TEST_PASSED"
        return {
            "task_id": task_id,
            "status": status,
            "summary": summary,
            "files_changed": [f"mock/{task_id}.txt"],
            "self_test_commands": ["mock:lint", "mock:test"],
            "self_test_results": [
                {"command": "mock:lint", "exit_code": 0 if passed else 1, "passed": passed},
                {"command": "mock:test", "exit_code": 0 if passed else 1, "passed": passed},
            ],
            "claimed_acceptance": [f"Task {task_title} handled by mock builder."],
            "next_handoff": "VERIFIER" if passed else "BUILDER",
            "generated_at": _now_iso(),
        }
