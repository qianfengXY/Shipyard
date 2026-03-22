"""Mock verifier adapter."""

from __future__ import annotations

from datetime import datetime


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class MockVerifier:
    def __init__(self, failure_plan: dict[str, int] | None = None) -> None:
        self.failure_plan = failure_plan or {}

    def run(
        self,
        task_id: str,
        task_title: str,
        docs_context: dict,
        builder_result: dict,
        state: dict,
    ) -> dict:
        attempt = int(state.get("verifier_attempt", 1))
        planned_failures = self.failure_plan.get(task_id, 0)
        should_fail = attempt <= planned_failures
        status = "FAIL" if should_fail else "PASS"
        return {
            "task_id": task_id,
            "status": status,
            "summary": f"Mock verifier {'rejected' if should_fail else 'accepted'} {task_id}.",
            "findings": (
                [
                    {
                        "severity": "high",
                        "title": f"{task_title} needs rework",
                        "evidence": "Mock verifier is configured to fail this attempt.",
                        "expected": "The task should satisfy the acceptance criteria.",
                        "suggested_fix": "Re-run builder with the verifier feedback.",
                    }
                ]
                if should_fail
                else []
            ),
            "verification_commands": ["mock:verify"],
            "decision": "REWORK_REQUIRED" if should_fail else "PASS",
            "generated_at": _now_iso(),
        }
