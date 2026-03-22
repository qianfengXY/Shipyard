"""Codex-backed verifier adapter."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from shipyard.adapters.cli_utils import run_codex_json


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class CodexVerifier:
    def __init__(
        self,
        root: Path,
        command: str = "codex",
        logger=None,
        use_terminal_window: bool = False,
    ) -> None:
        self.root = Path(root)
        self.command = command
        self.logger = logger
        self.use_terminal_window = use_terminal_window

    def run(
        self,
        task_id: str,
        task_title: str,
        docs_context: dict,
        builder_result: dict,
        state: dict,
    ) -> dict:
        payload = run_codex_json(
            command=self.command,
            cwd=self.root,
            prompt=self._build_prompt(
                task_id=task_id,
                task_title=task_title,
                docs_context=docs_context,
                builder_result=builder_result,
                state=state,
            ),
            schema=self._response_schema(),
            logger=self.logger,
            use_terminal_window=self.use_terminal_window,
        )
        status = payload["status"]
        return {
            "task_id": task_id,
            "status": status,
            "summary": str(payload["summary"]).strip(),
            "findings": [
                {
                    "severity": str(item["severity"]),
                    "title": str(item["title"]),
                    "evidence": str(item["evidence"]),
                    "expected": str(item["expected"]),
                    "suggested_fix": str(item["suggested_fix"]),
                }
                for item in payload.get("findings", [])
            ],
            "verification_commands": [
                str(item) for item in payload.get("verification_commands", [])
            ],
            "decision": "PASS" if status == "PASS" else "REWORK_REQUIRED" if status == "FAIL" else "BLOCKED",
            "generated_at": _now_iso(),
        }

    def _build_prompt(
        self,
        *,
        task_id: str,
        task_title: str,
        docs_context: dict,
        builder_result: dict,
        state: dict,
    ) -> str:
        return f"""You are Shipyard's Verifier agent.

Repository root: {self.root}
Current task:
- task_id: {task_id}
- title: {task_title}
- module_id: {docs_context.get("current_task", {}).get("module_id", "module-general")}
- module_title: {docs_context.get("current_task", {}).get("module_title", "General")}

Requirements:
- Independently verify the current task using the repository and local checks.
- Treat the builder artifact as input, not as proof.
- Do not modify repository files.
- Never run Shipyard itself. Do not run `python -m shipyard.main`, `src/shipyard/main.py`, or any command that triggers the Shipyard engine.
- Never invoke `claude`, `codex`, or any other agent CLI from inside this task.
- Return only JSON matching the provided schema.

Verifier status rules:
- PASS: acceptance is met.
- FAIL: the task needs rework. When you return FAIL, include at least one finding.
- BLOCKED: you cannot verify because of a hard blocker.

docs_context:
{json.dumps(docs_context, ensure_ascii=False, indent=2)}

builder_result:
{json.dumps(builder_result, ensure_ascii=False, indent=2)}

state:
{json.dumps(state, ensure_ascii=False, indent=2)}
"""

    @staticmethod
    def _response_schema() -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {"type": "string", "enum": ["PASS", "FAIL", "BLOCKED"]},
                "summary": {"type": "string"},
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "severity": {"type": "string"},
                            "title": {"type": "string"},
                            "evidence": {"type": "string"},
                            "expected": {"type": "string"},
                            "suggested_fix": {"type": "string"},
                        },
                        "required": [
                            "severity",
                            "title",
                            "evidence",
                            "expected",
                            "suggested_fix",
                        ],
                    },
                },
                "verification_commands": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["status", "summary", "findings", "verification_commands"],
        }
