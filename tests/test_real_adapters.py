from __future__ import annotations

import json
import sys
from pathlib import Path

from shipyard.adapters.claude_builder import ClaudeBuilder
from shipyard.adapters.codex_verifier import CodexVerifier
from shipyard.adapters.cli_utils import _codex_disable_mcp_args, _extract_codex_payload_from_stdout


def test_claude_builder_parses_cli_result(monkeypatch, tmp_path):
    class FakeProcess:
        def __init__(self, stdout_text: str):
            self._stdout_lines = stdout_text.splitlines(True)
            self.stdout = iter(self._stdout_lines)
            self.stdin = self
            self._returncode = 0

        def write(self, _: str) -> None:
            return None

        def close(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            return self._returncode

    def fake_popen(args, cwd, stdin, stdout, stderr, text, bufsize):
        payload = {
            "status": "SELF_TEST_PASSED",
            "summary": "Claude completed the task.",
            "files_changed": ["src/example.py"],
            "self_test_commands": ["python -m pytest"],
            "self_test_results": [
                {"command": "python -m pytest", "exit_code": 0, "passed": True}
            ],
            "claimed_acceptance": ["Feature works as requested."],
        }
        stdout = json.dumps({"type": "result", "result": json.dumps(payload)})
        return FakeProcess(stdout)

    monkeypatch.setattr("shipyard.adapters.cli_utils.subprocess.Popen", fake_popen)
    builder = ClaudeBuilder(root=tmp_path, command=sys.executable)

    result = builder.run(
        task_id="task-001",
        task_title="Implement feature",
        docs_context={"current_task": {"task_id": "task-001", "title": "Implement feature"}},
        prior_review=None,
        state={"builder_attempt": 1},
    )

    assert result["task_id"] == "task-001"
    assert result["status"] == "SELF_TEST_PASSED"
    assert result["next_handoff"] == "VERIFIER"
    assert result["files_changed"] == ["src/example.py"]
    assert result["claimed_acceptance"] == ["task-001"]


def test_codex_verifier_parses_output_file(monkeypatch, tmp_path):
    class FakeProcess:
        def __init__(self):
            self.stdout = iter([])
            self.stdin = self
            self._returncode = 0

        def write(self, _: str) -> None:
            return None

        def close(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            return self._returncode

    def fake_popen(args, cwd, stdin, stdout, stderr, text, bufsize):
        output_path = args[args.index("-o") + 1]
        payload = {
            "status": "FAIL",
            "summary": "Codex found a regression.",
            "findings": [
                {
                    "severity": "high",
                    "title": "Regression",
                    "evidence": "A test is failing.",
                    "expected": "All tests should pass.",
                    "suggested_fix": "Fix the regression.",
                }
            ],
            "verification_commands": ["python -m pytest"],
        }
        with Path(output_path).open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return FakeProcess()

    monkeypatch.setattr("shipyard.adapters.cli_utils.subprocess.Popen", fake_popen)
    verifier = CodexVerifier(root=tmp_path, command=sys.executable)

    result = verifier.run(
        task_id="task-002",
        task_title="Verify feature",
        docs_context={"current_task": {"task_id": "task-002", "title": "Verify feature"}},
        builder_result={"task_id": "task-002", "status": "SELF_TEST_PASSED"},
        state={"verifier_attempt": 1},
    )

    assert result["task_id"] == "task-002"
    assert result["status"] == "FAIL"
    assert result["decision"] == "REWORK_REQUIRED"
    assert result["findings"][0]["title"] == "Regression"


def test_codex_disable_mcp_args_from_config(monkeypatch, tmp_path):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        """
[mcp_servers.playwright]
command = "npx"

[mcp_servers.linear]
url = "https://mcp.linear.app/mcp"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    args = _codex_disable_mcp_args()

    assert args == [
        "-c",
        "mcp_servers.playwright.enabled=false",
        "-c",
        "mcp_servers.linear.enabled=false",
    ]


def test_extract_codex_payload_from_stdout_falls_back_to_agent_message():
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "abc"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "status": "PASS",
                                        "summary": "Codex verified the task.",
                                        "findings": [],
                                        "verification_commands": ["pytest"],
                                    }
                                ),
                            }
                        ],
                    },
                }
            ),
            json.dumps({"type": "turn.completed"}),
        ]
    )

    payload = _extract_codex_payload_from_stdout(stdout)

    assert payload is not None
    assert payload["status"] == "PASS"
    assert payload["summary"] == "Codex verified the task."
