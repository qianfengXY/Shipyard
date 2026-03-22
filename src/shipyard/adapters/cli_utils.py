"""Utilities for calling external agent CLIs with structured JSON output."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

from shipyard.exceptions import AdapterError
from shipyard.logger import RunLogger


def split_command(command: str) -> list[str]:
    parts = shlex.split(command)
    if not parts:
        raise AdapterError("Adapter command cannot be empty.")
    return parts


def ensure_command_exists(command: str) -> None:
    executable = split_command(command)[0]
    if Path(executable).exists():
        return
    if shutil.which(executable) is not None:
        return
    raise AdapterError(f"Required command is not available: {executable}")


def run_claude_json(
    *,
    command: str,
    cwd: Path,
    prompt: str,
    schema: dict,
    logger: RunLogger | None = None,
    use_terminal_window: bool = False,
    timeout_seconds: int = 1800,
) -> dict:
    ensure_command_exists(command)
    if use_terminal_window:
        result_text = _run_agent_ui_window_command(
            source_name="claude",
            args=[
                *split_command(command),
                "--dangerously-skip-permissions",
                "--add-dir",
                str(cwd),
                "--name",
                "Shipyard Builder",
            ],
            cwd=cwd,
            prompt=prompt,
            schema=schema,
            logger=logger,
            timeout_seconds=timeout_seconds,
        )
        return _parse_json_loose(result_text, source_name="Claude CLI")

    args = [
        *split_command(command),
        "-p",
        "--verbose",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--permission-mode",
        "bypassPermissions",
        "--add-dir",
        str(cwd),
        "--json-schema",
        json.dumps(schema, ensure_ascii=False),
    ]
    stdout = _run_streaming_command(
        args=args,
        cwd=cwd,
        prompt=prompt,
        source_name="claude",
        logger=logger,
        timeout_seconds=timeout_seconds,
    )
    envelope = _extract_last_json_object(stdout)
    structured_output = envelope.get("structured_output")
    if isinstance(structured_output, dict):
        return structured_output
    result_text = envelope.get("result")
    if not isinstance(result_text, str):
        raise AdapterError("Claude CLI did not return a JSON result payload.")
    return _parse_json_loose(result_text, source_name="Claude CLI")


def run_codex_json(
    *,
    command: str,
    cwd: Path,
    prompt: str,
    schema: dict,
    logger: RunLogger | None = None,
    use_terminal_window: bool = False,
    timeout_seconds: int = 1800,
) -> dict:
    ensure_command_exists(command)
    with tempfile.TemporaryDirectory(prefix="shipyard-codex-") as temp_dir:
        temp_root = Path(temp_dir)
        schema_file = temp_root / "schema.json"
        output_file = temp_root / "last_message.json"
        schema_file.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
        window_args = [
            *split_command(command),
            *_codex_disable_mcp_args(),
            "-C",
            str(cwd),
            "-s",
            "workspace-write",
            "-a",
            "never",
            "--add-dir",
            str(temp_root),
        ]
        args = [
            *split_command(command),
            "exec",
            *_codex_disable_mcp_args(),
            "-C",
            str(cwd),
            "--json",
            "--sandbox",
            "read-only",
            "--output-schema",
            str(schema_file),
            "-o",
            str(output_file),
        ]
        if use_terminal_window:
            result_text = _run_agent_ui_window_command(
                source_name="codex",
                args=window_args,
                cwd=cwd,
                prompt=prompt,
                schema=schema,
                logger=logger,
                timeout_seconds=timeout_seconds,
            )
            return _parse_json_loose(result_text, source_name="Codex CLI")

        stdout = _run_streaming_command(
                args=args,
                cwd=cwd,
                prompt=prompt,
                source_name="codex",
                logger=logger,
                timeout_seconds=timeout_seconds,
            )
        if not output_file.exists():
            payload = _extract_codex_payload_from_stdout(stdout)
            if payload is not None:
                return payload
            raise AdapterError("Codex CLI did not produce the expected output file.")
        return _parse_json_loose(
            output_file.read_text(encoding="utf-8").strip(),
            source_name="Codex CLI",
        )


def _extract_last_json_object(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise AdapterError("No JSON payload was found in CLI output.")


def _extract_codex_payload_from_stdout(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidate = _search_for_json_object(payload)
        if candidate is not None:
            return candidate
    return None


def _search_for_json_object(value: object) -> dict | None:
    if isinstance(value, dict):
        if "status" in value and ("summary" in value or "findings" in value):
            return value
        for nested in value.values():
            candidate = _search_for_json_object(nested)
            if candidate is not None:
                return candidate
        return None
    if isinstance(value, list):
        for item in value:
            candidate = _search_for_json_object(item)
            if candidate is not None:
                return candidate
        return None
    if isinstance(value, str):
        try:
            parsed = _parse_json_loose(value, source_name="Codex CLI")
        except AdapterError:
            return None
        return parsed
    return None


def _codex_disable_mcp_args() -> list[str]:
    config_root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    config_file = config_root / "config.toml"
    if not config_file.exists():
        return []

    try:
        payload = tomllib.loads(config_file.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return []

    servers = payload.get("mcp_servers")
    if not isinstance(servers, dict):
        return []

    args: list[str] = []
    for server_name in servers:
        if isinstance(server_name, str) and server_name:
            args.extend(["-c", f"mcp_servers.{server_name}.enabled=false"])
    return args


def _parse_json_loose(text: str, source_name: str) -> dict:
    candidate = text.strip()
    if not candidate:
        raise AdapterError(f"{source_name} returned empty output.")
    try:
        payload = json.loads(candidate)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    code_block_start = candidate.find("```")
    if code_block_start != -1:
        code_block_end = candidate.rfind("```")
        if code_block_end > code_block_start:
            fenced = candidate[code_block_start + 3 : code_block_end].strip()
            if fenced.startswith("json"):
                fenced = fenced[4:].strip()
            try:
                payload = json.loads(fenced)
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = candidate[start : end + 1]
        try:
            payload = json.loads(snippet)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError as exc:
            raise AdapterError(f"{source_name} returned invalid JSON: {exc}") from exc

    raise AdapterError(f"{source_name} did not return a JSON object.")


def _run_streaming_command(
    *,
    args: list[str],
    cwd: Path,
    prompt: str,
    source_name: str,
    logger: RunLogger | None,
    timeout_seconds: int,
) -> str:
    process = subprocess.Popen(
        args,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(prompt)
    process.stdin.close()

    output_lines: list[str] = []
    try:
        for raw_line in process.stdout:
            line = raw_line.rstrip("\n")
            output_lines.append(raw_line)
            if logger and line.strip():
                logger.stream(source_name, _summarize_stream_line(source_name, line))
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        raise AdapterError(f"{source_name} CLI timed out after {timeout_seconds}s.") from exc

    stdout = "".join(output_lines)
    if return_code != 0:
        details = stdout.strip()
        raise AdapterError(f"{source_name} CLI failed (exit_code={return_code}): {details}")
    return stdout


def _run_agent_ui_window_command(
    *,
    source_name: str,
    args: list[str],
    cwd: Path,
    prompt: str,
    schema: dict,
    logger: RunLogger | None,
    timeout_seconds: int,
) -> str:
    if shutil.which("osascript") is None:
        raise AdapterError("Terminal window mode requires osascript on macOS.")

    with tempfile.TemporaryDirectory(prefix=f"shipyard-{source_name}-ui-") as temp_dir:
        temp_root = Path(temp_dir)
        prompt_file = temp_root / "prompt.txt"
        result_file = temp_root / "result.json"
        status_file = temp_root / "status.txt"
        config_file = temp_root / "config.json"
        script_file = temp_root / "run.zsh"

        prompt_file.write_text(
            _build_agent_ui_prompt(
                source_name=source_name,
                prompt=prompt,
                schema=schema,
                result_file=result_file,
            ),
            encoding="utf-8",
        )
        config_file.write_text(
            json.dumps(
                {
                    "source": source_name,
                    "args": args,
                    "cwd": str(cwd),
                    "prompt_file": str(prompt_file),
                    "status_file": str(status_file),
                    "result_file": str(result_file),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        script_file.write_text(
            _build_agent_ui_script(config_file=config_file),
            encoding="utf-8",
        )
        os.chmod(script_file, 0o755)

        if logger:
            logger.stream(source_name, f"{source_name.capitalize()} window opened")
        window_id = _open_terminal_window(script_file)
        try:
            start = time.monotonic()
            summary_logged = False
            payload_text: str | None = None
            while True:
                if result_file.exists():
                    payload_text = result_file.read_text(encoding="utf-8")
                    if logger and not summary_logged:
                        try:
                            payload = _parse_json_loose(
                                payload_text,
                                source_name=f"{source_name.capitalize()} CLI",
                            )
                        except AdapterError:
                            payload = None
                        if isinstance(payload, dict):
                            logger.stream(source_name, f"{source_name.capitalize()} completed")
                            summary = str(payload.get("summary", "")).strip()
                            if summary:
                                logger.stream(
                                    source_name,
                                    f"{source_name.capitalize()} summary: {summary}",
                                )
                        summary_logged = True
                if status_file.exists():
                    exit_code = int(status_file.read_text(encoding="utf-8").strip() or "0")
                    if payload_text is not None:
                        return payload_text
                    if exit_code != 0:
                        raise AdapterError(
                            f"{source_name} CLI failed (exit_code={exit_code}) before producing a result file."
                        )
                if time.monotonic() - start > timeout_seconds:
                    raise AdapterError(f"{source_name} CLI timed out after {timeout_seconds}s.")
                time.sleep(1.0)
        finally:
            if window_id is not None:
                _close_terminal_window(window_id)


def _build_agent_ui_prompt(
    *,
    source_name: str,
    prompt: str,
    schema: dict,
    result_file: Path,
) -> str:
    return f"""{prompt}

Delivery protocol:
- Work in the repository normally using {source_name.capitalize()}'s standard interactive tools.
- Do not modify Shipyard runtime files except for the single result file path below.
- When the task is complete, write exactly one UTF-8 JSON object matching this schema to:
  {result_file}
- Do not wrap the JSON in markdown or any extra text.
- After writing the JSON file, stop. Do not wait for more instructions. Shipyard will collect the result automatically.

Required schema:
{json.dumps(schema, ensure_ascii=False, indent=2)}

Recommended write pattern:
cat > {shlex.quote(str(result_file))} <<'EOF'
{{ ...valid JSON matching the schema... }}
EOF
"""


def _build_agent_ui_script(*, config_file: Path) -> str:
    return f"""#!/bin/zsh
cd {shlex.quote(str(config_file.parent))} || exit 1
{shlex.quote(sys.executable)} -m shipyard.agent_ui_runner {shlex.quote(str(config_file))}
status=$?
exit "$status"
"""


def _run_terminal_window_command(
    *,
    args: list[str],
    cwd: Path,
    prompt: str,
    source_name: str,
    logger: RunLogger | None,
    timeout_seconds: int,
) -> str:
    if shutil.which("osascript") is None:
        raise AdapterError("Terminal window mode requires osascript on macOS.")

    with tempfile.TemporaryDirectory(prefix=f"shipyard-{source_name}-window-") as temp_dir:
        temp_root = Path(temp_dir)
        prompt_file = temp_root / "prompt.txt"
        pretty_log_file = temp_root / "pretty.log"
        status_file = temp_root / "status.txt"
        result_file = temp_root / "result.json"
        codex_output_file = temp_root / "codex-last-message.json" if source_name == "codex" else None
        config_file = temp_root / "config.json"
        script_file = temp_root / "run.zsh"

        prompt_file.write_text(prompt, encoding="utf-8")
        runner_args = list(args)
        if source_name == "codex" and codex_output_file is not None:
            output_flag_index = runner_args.index("-o") + 1
            runner_args[output_flag_index] = str(codex_output_file)
        config_file.write_text(
            json.dumps(
                {
                    "source": source_name,
                    "args": runner_args,
                    "cwd": str(cwd),
                    "prompt_file": str(prompt_file),
                    "status_file": str(status_file),
                    "result_file": str(result_file),
                    "pretty_log_file": str(pretty_log_file),
                    "codex_output_file": str(codex_output_file) if codex_output_file else None,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        script_file.write_text(
            _build_terminal_script(config_file=config_file),
            encoding="utf-8",
        )
        os.chmod(script_file, 0o755)

        window_id = _open_terminal_window(script_file)
        try:
            return _poll_terminal_output(
                pretty_log_file=pretty_log_file,
                status_file=status_file,
                result_file=result_file,
                source_name=source_name,
                logger=logger,
                timeout_seconds=timeout_seconds,
            )
        finally:
            if window_id is not None:
                _close_terminal_window(window_id)


def _build_terminal_script(*, config_file: Path) -> str:
    return f"""#!/bin/zsh
cd {shlex.quote(str(config_file.parent))} || exit 1
{shlex.quote(sys.executable)} -m shipyard.agent_window_runner {shlex.quote(str(config_file))}
status=$?
exit "$status"
"""


def _open_terminal_window(script_file: Path) -> int | None:
    command = f"zsh {shlex.quote(str(script_file))}"
    script = f'tell application "Terminal" to do script "{_escape_applescript_string(command)}"'
    completed = subprocess.run(
        ["osascript", "-e", 'tell application "Terminal" to activate', "-e", script],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AdapterError(f"Failed to open Terminal window: {completed.stderr.strip()}")
    match = re.search(r"window id (\d+)", completed.stdout)
    if match is None:
        return None
    return int(match.group(1))


def _close_terminal_window(window_id: int) -> None:
    subprocess.run(
        [
            "osascript",
            "-e",
            f'tell application "Terminal" to close (every window whose id is {window_id}) saving no',
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _poll_terminal_output(
    *,
    pretty_log_file: Path,
    status_file: Path,
    result_file: Path,
    source_name: str,
    logger: RunLogger | None,
    timeout_seconds: int,
) -> str:
    start = time.monotonic()
    offset = 0
    output_lines: list[str] = []
    while True:
        if pretty_log_file.exists():
            with pretty_log_file.open("r", encoding="utf-8") as handle:
                handle.seek(offset)
                chunk = handle.read()
                offset = handle.tell()
            if chunk:
                output_lines.append(chunk)
                if logger:
                    for raw_line in chunk.splitlines():
                        if raw_line.strip():
                            logger.stream(source_name, raw_line)
        if status_file.exists():
            break
        if time.monotonic() - start > timeout_seconds:
            raise AdapterError(f"{source_name} CLI timed out after {timeout_seconds}s.")
        time.sleep(0.5)

    try:
        exit_code = int(status_file.read_text(encoding="utf-8").strip() or "0")
    except ValueError as exc:
        raise AdapterError(f"{source_name} CLI returned an invalid exit status file.") from exc

    stdout = "".join(output_lines)
    if exit_code != 0:
        raise AdapterError(f"{source_name} CLI failed (exit_code={exit_code}): {stdout.strip()}")
    if not result_file.exists():
        raise AdapterError(f"{source_name} CLI did not produce a result file.")
    return result_file.read_text(encoding="utf-8")


def _escape_applescript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _summarize_stream_line(source_name: str, line: str) -> str:
    line = line.strip()
    if not line:
        return line

    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return line

    if source_name == "claude":
        payload_type = payload.get("type")
        if payload_type == "system":
            return "Claude started"
        if payload_type == "stream_event":
            event = payload.get("event", {})
            event_type = event.get("type")
            if event_type == "content_block_start":
                block = event.get("content_block", {})
                if block.get("type") == "tool_use":
                    return f"Claude tool: {block.get('name')}"
                if block.get("type") == "thinking":
                    return "Claude thinking..."
                if block.get("type") == "text":
                    return "Claude responding..."
                return ""
            if event_type == "message_delta":
                stop_reason = event.get("delta", {}).get("stop_reason")
                if stop_reason:
                    return f"Claude stop: {stop_reason}"
            return ""
        if payload_type == "result":
            return f"Claude completed. cost_usd={payload.get('total_cost_usd')}"
        return ""

    if source_name == "codex":
        payload_type = payload.get("type")
        if payload_type == "thread.started":
            return "Codex started"
        if payload_type == "turn.started":
            return "Codex verifying..."
        if payload_type == "turn.completed":
            return "Codex completed"
        if payload_type == "item.completed":
            item = payload.get("item", {})
            item_type = item.get("type", "")
            if item_type == "command_execution":
                return "Codex finished a check"
            if item_type == "agent_message":
                return "Codex prepared a verdict"
            return ""
        if payload_type == "error":
            message = payload.get("message")
            if isinstance(message, str) and message:
                return f"Codex warning: {message}"
            return ""
        return ""

    return line
