"""Claude-backed builder adapter."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from shipyard.adapters.cli_utils import run_claude_json


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ClaudeBuilder:
    def __init__(
        self,
        root: Path,
        command: str = "claude",
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
        prior_review: dict | None,
        state: dict,
    ) -> dict:
        payload = run_claude_json(
            command=self.command,
            cwd=self.root,
            prompt=self._build_prompt(
                task_id=task_id,
                task_title=task_title,
                docs_context=docs_context,
                prior_review=prior_review,
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
            "files_changed": [str(item) for item in payload.get("files_changed", [])],
            "self_test_commands": [str(item) for item in payload.get("self_test_commands", [])],
            "self_test_results": [
                {
                    "command": str(item["command"]),
                    "exit_code": int(item["exit_code"]),
                    "passed": bool(item["passed"]),
                }
                for item in payload.get("self_test_results", [])
            ],
            # Keep the artifact protocol deterministic: the builder claims only the active task id.
            "claimed_acceptance": [task_id],
            "next_handoff": "VERIFIER" if status == "SELF_TEST_PASSED" else "BUILDER",
            "generated_at": _now_iso(),
        }

    def _build_prompt(
        self,
        *,
        task_id: str,
        task_title: str,
        docs_context: dict,
        prior_review: dict | None,
        state: dict,
    ) -> str:
        return f"""You are Shipyard's Builder agent.

Repository root: {self.root}
Current task:
- task_id: {task_id}
- title: {task_title}

Requirements:
- Implement the task in the current repository.
- You may edit files and run local checks as needed.
- Do not modify docs/TASKS.md directly.
- Do not modify .shipyard/state.json directly.
- Never run Shipyard itself. Do not run `python -m shipyard.main`, `src/shipyard/main.py`, or any command that triggers the Shipyard engine.
- Never invoke `claude`, `codex`, or any other agent CLI from inside this task.
- Use the prior verifier review if one is provided.
- Return only JSON matching the provided schema.

Important:
- This repository may already contain part or all of the requested implementation. If so, inspect the existing code, make only the necessary changes, and report the real self-test outcome.
- Prefer a fast verification path for broad tasks: inspect the repository first, then run only the minimum checks needed to support your conclusion.
- Do not spend time re-implementing large completed features if the repository already satisfies the task.
- Limit yourself to a small number of focused shell commands and avoid long-running exploratory loops.

Builder status rules:
- SELF_TEST_PASSED: implementation is ready for verification and your self-tests passed.
- SELF_TEST_FAILED: you attempted the task but your self-tests failed.
- BLOCKED: you cannot continue because of a missing dependency, credential, or hard blocker.

Field rule:
- claimed_acceptance must contain exactly one item: the current task_id.

docs_context:
{json.dumps(docs_context, ensure_ascii=False, indent=2)}

prior_review:
{json.dumps(prior_review, ensure_ascii=False, indent=2)}

state:
{json.dumps(state, ensure_ascii=False, indent=2)}
"""

    @staticmethod
    def _response_schema() -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["SELF_TEST_PASSED", "SELF_TEST_FAILED", "BLOCKED"],
                },
                "summary": {"type": "string"},
                "files_changed": {"type": "array", "items": {"type": "string"}},
                "self_test_commands": {"type": "array", "items": {"type": "string"}},
                "self_test_results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "command": {"type": "string"},
                            "exit_code": {"type": "integer"},
                            "passed": {"type": "boolean"},
                        },
                        "required": ["command", "exit_code", "passed"],
                    },
                },
                "claimed_acceptance": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "status",
                "summary",
                "files_changed",
                "self_test_commands",
                "self_test_results",
                "claimed_acceptance",
            ],
        }
