"""Result validation and transition helpers."""

from __future__ import annotations

from shipyard.exceptions import AdapterError

ALLOWED_BUILDER_STATUSES = {"SELF_TEST_PASSED", "SELF_TEST_FAILED", "BLOCKED"}
ALLOWED_VERIFIER_STATUSES = {"PASS", "FAIL", "BLOCKED"}


def validate_builder_result(result: dict, task_id: str) -> str:
    status = result.get("status")
    if result.get("task_id") != task_id:
        raise AdapterError("Builder result task_id does not match the current task.")
    if status not in ALLOWED_BUILDER_STATUSES:
        raise AdapterError(f"Builder returned invalid status: {status}")
    return status


def validate_verifier_result(result: dict, task_id: str) -> str:
    status = result.get("status")
    if result.get("task_id") != task_id:
        raise AdapterError("Verifier result task_id does not match the current task.")
    if status not in ALLOWED_VERIFIER_STATUSES:
        raise AdapterError(f"Verifier returned invalid status: {status}")
    if status == "FAIL" and not result.get("findings"):
        raise AdapterError("Verifier FAIL result must include at least one finding.")
    return status
