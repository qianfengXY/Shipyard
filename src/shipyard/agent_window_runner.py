"""Run an agent CLI in a Terminal window with human-readable progress output."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 1:
        print("Usage: python -m shipyard.agent_window_runner <config.json>", file=sys.stderr)
        return 2

    config_path = Path(argv[0])
    config = json.loads(config_path.read_text(encoding="utf-8"))

    source = config["source"]
    args = list(config["args"])
    cwd = Path(config["cwd"])
    prompt_file = Path(config["prompt_file"])
    status_file = Path(config["status_file"])
    result_file = Path(config["result_file"])
    pretty_log_file = Path(config["pretty_log_file"])
    codex_output_file = Path(config["codex_output_file"]) if config.get("codex_output_file") else None

    prompt = prompt_file.read_text(encoding="utf-8")
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

    raw_lines: list[str] = []
    last_pretty = ""
    with pretty_log_file.open("a", encoding="utf-8") as pretty_handle:
        for raw_line in process.stdout:
            raw_lines.append(raw_line)
            pretty = summarize_stream_line(source, raw_line.rstrip("\n"))
            if pretty and pretty != last_pretty:
                print(pretty, flush=True)
                pretty_handle.write(pretty + "\n")
                pretty_handle.flush()
                last_pretty = pretty

    return_code = process.wait()
    status_file.write_text(str(return_code), encoding="utf-8")
    if return_code != 0:
        return return_code

    if source == "claude":
        envelope = extract_last_json_object("".join(raw_lines))
        payload = envelope.get("structured_output")
        if not isinstance(payload, dict):
            payload = parse_json_loose(envelope.get("result", ""), source_name="Claude CLI")
        summary = str(payload.get("summary", "")).strip()
        if summary:
            print(f"Claude summary: {summary}", flush=True)
            pretty_handle = pretty_log_file.open("a", encoding="utf-8")
            pretty_handle.write(f"Claude summary: {summary}\n")
            pretty_handle.close()
        result_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0

    payload = None
    if codex_output_file is not None and codex_output_file.exists():
        payload = parse_json_loose(codex_output_file.read_text(encoding="utf-8"), source_name="Codex CLI")
    else:
        payload = extract_codex_payload_from_stream(raw_lines)
    if payload is None:
        print("Codex did not produce the expected output file.", file=sys.stderr)
        return 1
    summary = str(payload.get("summary", "")).strip()
    if summary:
        print(f"Codex summary: {summary}", flush=True)
        pretty_handle = pretty_log_file.open("a", encoding="utf-8")
        pretty_handle.write(f"Codex summary: {summary}\n")
        pretty_handle.close()
    result_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def extract_last_json_object(stdout: str) -> dict:
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
    raise ValueError("No JSON payload found.")


def parse_json_loose(text: str, source_name: str) -> dict:
    candidate = str(text).strip()
    if not candidate:
        raise ValueError(f"{source_name} returned empty output.")
    try:
        payload = json.loads(candidate)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(candidate[start : end + 1])
    raise ValueError(f"{source_name} did not return a JSON object.")


def extract_codex_payload_from_stream(raw_lines: list[str]) -> dict | None:
    for raw_line in reversed(raw_lines):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidate = search_for_json_object(payload)
        if candidate is not None:
            return candidate
    return None


def search_for_json_object(value: object) -> dict | None:
    if isinstance(value, dict):
        if "status" in value and ("summary" in value or "findings" in value):
            return value
        for nested in value.values():
            candidate = search_for_json_object(nested)
            if candidate is not None:
                return candidate
        return None
    if isinstance(value, list):
        for item in value:
            candidate = search_for_json_object(item)
            if candidate is not None:
                return candidate
        return None
    if isinstance(value, str):
        try:
            return parse_json_loose(value, source_name="Codex CLI")
        except ValueError:
            return None
    return None


def summarize_stream_line(source_name: str, line: str) -> str:
    if not line.strip():
        return ""
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
                block_type = block.get("type")
                if block_type == "tool_use":
                    return f"Claude tool: {block.get('name')}"
                if block_type == "thinking":
                    return "Claude thinking..."
                if block_type == "text":
                    return "Claude responding..."
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


if __name__ == "__main__":
    raise SystemExit(main())
