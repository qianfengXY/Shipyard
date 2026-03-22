"""CLI entrypoint for Shipyard."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import sys
import threading
import time
import unicodedata
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shipyard.engine import ShipyardEngine
from shipyard.exceptions import ShipyardError
from shipyard.repository import RepositoryPaths


ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def _watch(engine: ShipyardEngine, interval_seconds: float) -> int:
    try:
        while True:
            payload = engine.report_payload()
            print("\033[2J\033[H", end="")
            print(_render_report(payload))
            phase = payload["run"]["phase"]
            if phase in {"COMPLETED", "ABORTED"}:
                return 0 if phase == "COMPLETED" else 1
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("\nShipyard watch stopped.")
        return 0

def _dashboard(
    root: Path,
    interval_seconds: float,
    *,
    agent_windows: bool,
) -> int:
    engine = ShipyardEngine(root, echo=False, agent_windows=agent_windows)
    outcome: dict[str, object] = {"state": None, "error": None}
    final_frame = ""

    def runner() -> None:
        try:
            outcome["state"] = engine.run()
        except Exception as exc:  # pragma: no cover
            outcome["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()

    use_tty = sys.stdout.isatty()
    if use_tty:
        sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()
    try:
        while True:
            payload = engine.report_payload()
            final_frame = _render_dashboard(payload)
            if use_tty:
                sys.stdout.write("\033[2J\033[H")
            sys.stdout.write(final_frame)
            sys.stdout.write("\n")
            sys.stdout.flush()

            phase = payload["run"]["phase"]
            if not thread.is_alive() and phase in {"COMPLETED", "ABORTED"}:
                break
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        sys.stdout.write("\nDashboard stopped.\n")
        sys.stdout.flush()
        return 130
    finally:
        if use_tty:
            sys.stdout.write("\033[?1049l\033[?25h")
            sys.stdout.flush()

    if outcome["error"] is not None:
        raise outcome["error"]  # type: ignore[misc]
    state = outcome["state"]
    if state is None:
        return 1
    if final_frame:
        if use_tty:
            sys.stdout.write("\033[2J\033[H")
        sys.stdout.write(final_frame)
        sys.stdout.write("\n")
    sys.stdout.write(
        f"\nDashboard finished with phase {getattr(state, 'phase', 'UNKNOWN')}.\n"
    )
    sys.stdout.flush()
    return 0 if getattr(state, "phase", "") == "COMPLETED" else 1


def _write_run_pid(paths: RepositoryPaths) -> None:
    paths.run_pid_file.write_text(str(os.getpid()), encoding="utf-8")


def _clear_run_pid(paths: RepositoryPaths) -> None:
    if paths.run_pid_file.exists():
        paths.run_pid_file.unlink()


def _load_json_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _interrupt_active_processes(paths: RepositoryPaths) -> None:
    active_agent = _load_json_file(paths.active_agent_file) or {}
    pid_file = active_agent.get("pid_file")
    interrupted_agent = False
    if pid_file:
        pid_path = Path(pid_file)
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip() or "0")
            except ValueError:
                pid = 0
            if pid > 0:
                try:
                    os.killpg(pid, signal.SIGINT)
                    interrupted_agent = True
                except OSError:
                    pass
    if not interrupted_agent and paths.run_pid_file.exists():
        try:
            run_pid = int(paths.run_pid_file.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            run_pid = 0
        if run_pid > 0 and run_pid != os.getpid():
            try:
                os.kill(run_pid, signal.SIGINT)
            except OSError:
                pass


def _render_report(payload: dict) -> str:
    run = payload["run"]
    agents = payload["agents"]
    current_task = _current_task_report(payload)
    lines = [
        "Shipyard Monitor",
        "=" * 72,
        f"run_id: {run['run_id']}",
        f"phase: {run['phase']}",
        (
            f"current_task: {run['current_task_id']} "
            f"{current_task['module_title']} / {run['current_task_title']}"
            if current_task is not None and run["current_task_id"]
            else f"current_task: {run['current_task_id']} {run['current_task_title'] or ''}".strip()
        ),
        f"builder_attempt: {run['builder_attempt']}",
        f"verifier_attempt: {run['verifier_attempt']}",
        f"final_review_attempt: {run['final_review_attempt']}",
        f"updated_at: {run['updated_at']}",
        f"task_records_dir: {payload['task_records_dir']}",
    ]
    if run["last_error"]:
        lines.append(f"last_error: {run['last_error']}")
    lines.append("")
    lines.append("Agents:")
    lines.append(
        f"- {agents['builder']['name']}: {agents['builder']['status']}"
    )
    lines.append(
        f"- {agents['verifier']['name']}: {agents['verifier']['status']}"
    )
    lines.append("")
    lines.append("Tasks:")
    for task in payload["task_window"]:
        lines.append(f"- {task['task_id']} [{task['task_status']}] {task['title']}")
        if task["builder_status"] or task["builder_summary"]:
            lines.append(
                f"  builder: {task['builder_status'] or '-'} | {task['builder_summary'] or ''}".rstrip()
            )
        if task["verifier_status"] or task["verifier_summary"]:
            lines.append(
                f"  verifier: {task['verifier_status'] or '-'} | {task['verifier_summary'] or ''}".rstrip()
            )
    lines.append("")
    lines.append("Recent Events:")
    if payload["recent_events"]:
        for event in payload["recent_events"]:
            lines.append(f"- {event}")
    else:
        lines.append("- no events yet")
    return "\n".join(lines)


def _render_dashboard(payload: dict) -> str:
    run = payload["run"]
    agents = payload["agents"]
    progress = payload["progress"]
    scheduler = payload["scheduler"]
    queue = payload["queue"]
    use_color = sys.stdout.isatty()
    current_task = _current_task_report(payload)
    width = max(shutil.get_terminal_size((120, 40)).columns, 100)
    gap = 4
    left_width = max(int((width - gap) * 0.54), 52)
    right_width = max(width - gap - left_width, 30)
    task_lines = _render_task_window(payload["task_window"], use_color, width=left_width)
    ready_queue_lines = _render_ready_queue_panel(payload, use_color, width=right_width)

    shipyard_events = _render_coordinator_timeline(payload, use_color)
    claude_events = payload["recent_events_by_agent"]["claude"] or ["no Claude events yet"]
    codex_events = payload["recent_events_by_agent"]["codex"] or ["no Codex events yet"]
    lines = [
        "Shipyard Dashboard",
        "=" * 80,
        f"Phase        : {_colorize_status(run['phase'], use_color)}",
        (
            f"Current Task : {run['current_task_id']}  {current_task['module_title']} / {run['current_task_title']}"
            if current_task is not None and run["current_task_id"]
            else f"Current Task : {run['current_task_id'] or '-'} {run['current_task_title'] or ''}".rstrip()
        ),
        f"Claude       : {_colorize_status(agents['builder']['status'], use_color)}",
        f"Codex        : {_colorize_status(agents['verifier']['status'], use_color)}",
        f"Attempts     : builder={run['builder_attempt']} verifier={run['verifier_attempt']} final={run['final_review_attempt']}",
        f"Updated At   : {run['updated_at']}",
        f"Overview     : modules {progress['completed_modules']} / {progress['total_modules']}  |  tasks {progress['completed_tasks']} / {progress['total_tasks']}  |  failed {progress['failed_tasks']}  |  progress {progress['progress_percent']:.1f}%",
        (
            f"Current Work : module M{progress['current_module_index']:02d} {progress['current_module_id']} "
            f"| subtasks {progress['current_module_done_count']} / {progress['current_module_total']} "
            f"| elapsed {_format_duration(progress['current_task_elapsed_seconds'])}"
            if progress["current_module_id"]
            else "Current Work : idle"
        ),
        (
            f"Scheduler    : lanes {scheduler['active_lanes']} / {scheduler['max_parallel_modules']} active  "
            f"| ready {scheduler['ready_count']}  "
            f"| blocked {scheduler['blocked_count']}  "
            f"| mode {scheduler['mode']}"
        ),
        f"Task Records : {payload['task_records_dir']}",
    ]
    if run["last_error"]:
        lines.append(f"Last Error   : {_colorize_text(run['last_error'], '31', use_color)}")
    lines.extend(
        [
            "",
            *_render_panel_columns("Tasks", task_lines, "Ready Queue", ready_queue_lines, left_width, right_width),
            "",
            "Coordinator Timeline",
            "-" * 80,
            *shipyard_events,
            "",
            *_render_event_columns(claude_events, codex_events),
            "",
            "Press Ctrl+C to exit the dashboard.",
        ]
    )
    return "\n".join(_fit_dashboard_lines(lines, use_color))


def _render_event_columns(left: list[str], right: list[str]) -> list[str]:
    width = max(shutil.get_terminal_size((120, 40)).columns, 80)
    gap = 3
    column_width = max((width - gap) // 2, 20)
    max_rows = max(len(left), len(right))
    left_lines = _fit_column("Claude Events", left, column_width)
    right_lines = _fit_column("Codex Events", right, column_width)
    rows = max(max_rows + 2, len(left_lines), len(right_lines))
    left_lines.extend([""] * (rows - len(left_lines)))
    right_lines.extend([""] * (rows - len(right_lines)))
    rendered: list[str] = []
    for left_line, right_line in zip(left_lines, right_lines):
        rendered.append(f"{left_line:<{column_width}}{' ' * gap}{right_line:<{column_width}}")
    return rendered


def _render_panel_columns(
    left_title: str,
    left_lines: list[str],
    right_title: str,
    right_lines: list[str],
    left_width: int,
    right_width: int,
) -> list[str]:
    gap = 4
    left_panel = _panel_lines(left_title, left_lines, left_width)
    right_panel = _panel_lines(right_title, right_lines, right_width)
    rows = max(len(left_panel), len(right_panel))
    left_panel.extend([""] * (rows - len(left_panel)))
    right_panel.extend([""] * (rows - len(right_panel)))
    rendered: list[str] = []
    for left_line, right_line in zip(left_panel, right_panel):
        rendered.append(
            f"{_pad_visible(left_line, left_width)}{' ' * gap}{_pad_visible(right_line, right_width)}"
        )
    return rendered


def _panel_lines(title: str, lines: list[str], width: int) -> list[str]:
    rendered = [title, "-" * min(width, 32)]
    for line in lines:
        rendered.append(_truncate_visible(line, width))
    return rendered


def _render_coordinator_timeline(payload: dict, use_color: bool) -> list[str]:
    run = payload["run"]
    phase = run["phase"]
    task_id = run["current_task_id"] or _last_task_id_from_events(payload["recent_shipyard_events"])
    task_label = task_id or "next task"
    last_error = run.get("last_error")
    rendered = [
        "State Machine",
        *_render_state_machine_flow(phase, task_label, task_id is not None, last_error, use_color),
        "",
        "Transition Trace",
    ]
    trace_lines = _render_transition_trace(payload, use_color)
    if trace_lines:
        rendered.extend(trace_lines)
    else:
        rendered.append(_colorize_text("[info]", "37", use_color) + " no coordinator events yet")
    return rendered


def _format_timeline_step(kind: str, text: str, use_color: bool) -> str:
    labels = {
        "done": ("[done]", "36"),
        "now": ("[now ]", "32"),
        "next": ("[next]", "90"),
        "stop": ("[stop]", "31"),
    }
    label, code = labels.get(kind, ("[info]", "37"))
    return f"{_colorize_text(label, code, use_color)} {text}"


def _render_state_machine_flow(
    phase: str,
    task_label: str,
    has_task: bool,
    last_error: str | None,
    use_color: bool,
) -> list[str]:
    state_steps = [
        ("INIT", "Boot runtime and load persisted state"),
        ("SELECT_TASK", "Choose the next unchecked task from TASKS.md"),
        ("BUILDER_RUNNING", f"Claude builds {task_label}" if has_task else "Claude builds the selected task"),
        (
            "READY_FOR_VERIFICATION",
            f"Prepare Codex handoff for {task_label}" if has_task else "Prepare the verifier handoff",
        ),
        (
            "VERIFIER_RUNNING",
            f"Codex verifies {task_label}" if has_task else "Codex verifies the builder result",
        ),
        ("TASK_DONE", "Persist results, mark the task done, and return to selection"),
        ("FINAL_REVIEW", "Run final review checks after all tasks are complete"),
        ("COMPLETED", "Finish the run successfully"),
    ]
    phase_order = [name for name, _ in state_steps]
    current_index = phase_order.index(phase) if phase in phase_order else None

    rendered: list[str] = []
    for index, (step_phase, description) in enumerate(state_steps):
        if phase == "ABORTED":
            kind = "done" if current_index is not None and index < current_index else "stop"
        elif current_index is None:
            kind = "now" if index == 0 else "next"
        elif step_phase == phase:
            kind = "now"
        elif index < current_index:
            kind = "done"
        else:
            kind = "next"
        rendered.append(_format_timeline_step(kind, f"{step_phase}  {description}", use_color))

    if phase == "ABORTED":
        rendered.append(
            _format_timeline_step(
                "stop",
                f"ABORTED  {last_error or 'The coordinator stopped because of an unrecoverable error'}",
                use_color,
            )
        )
    return rendered


def _last_task_id_from_events(events: list[str]) -> str | None:
    for event in reversed(events):
        if "task_id=" not in event:
            continue
        task_id = event.split("task_id=", 1)[1].strip()
        if task_id and task_id != "None":
            return task_id
    return None


def _latest_transition(events: list[str]) -> str | None:
    for event in reversed(events):
        if event.startswith("phase=") or event.startswith("dispatch ") or event.startswith("Final review"):
            return _format_transition_event(event)
        if event.startswith("error="):
            return event.replace("error=", "Error: ", 1)
    return None


def _render_transition_trace(payload: dict, use_color: bool) -> list[str]:
    run = payload["run"]
    tasks = payload["task_window"]
    events = payload["recent_shipyard_events"]
    if not tasks and not events:
        return []

    headers = ["Task", "Select", "Claude", "Handoff", "Codex", "Close"]
    widths = [12, 8, 8, 9, 8, 8]
    rendered = [
        _format_trace_row(headers, widths),
        _format_trace_separator(widths),
    ]
    if tasks:
        for task in tasks:
            rendered.append(
                _format_trace_row(
                    _task_trace_row(task, run),
                    widths,
                )
            )
    else:
        rendered.append("no task records yet")

    rendered.extend(
        [
            "",
            "Recent Flow",
        ]
    )
    trace = [_format_transition_event(event) for event in events if _is_coordinator_trace_event(event)]
    if trace:
        for index, item in enumerate(trace[-6:], start=1):
            rendered.append(f"{_colorize_text(f'[{index}]', '90', use_color)} {item}")
    else:
        rendered.append(f"{_colorize_text('[info]', '37', use_color)} no coordinator events yet")
    return rendered


def _task_trace_row(task: dict, run: dict) -> list[str]:
    current_task_id = run["current_task_id"]
    phase = run["phase"]
    task_id = task["task_id"]
    stages = _task_trace_stages(task, phase, current_task_id)
    return [
        task_id,
        *stages,
    ]


def _task_trace_stages(task: dict, phase: str, current_task_id: str | None) -> list[str]:
    if task["task_status"] == "done":
        return ["[x]", "[x]", "[x]", "[x]", "[x]"]

    if task["task_id"] != current_task_id:
        return ["[ ]", "[ ]", "[ ]", "[ ]", "[ ]"]

    if phase in {"INIT", "SELECT_TASK"}:
        return ["[>]", "[ ]", "[ ]", "[ ]", "[ ]"]
    if phase == "BUILDER_RUNNING":
        return ["[x]", "[>]", "[ ]", "[ ]", "[ ]"]
    if phase == "READY_FOR_VERIFICATION":
        return ["[x]", "[x]", "[>]", "[ ]", "[ ]"]
    if phase == "VERIFIER_RUNNING":
        return ["[x]", "[x]", "[x]", "[>]", "[ ]"]
    if phase == "TASK_DONE":
        return ["[x]", "[x]", "[x]", "[x]", "[>]"]
    if phase == "ABORTED":
        return _aborted_task_trace_stages(task)
    if phase in {"FINAL_REVIEW", "COMPLETED"}:
        return ["[x]", "[x]", "[x]", "[x]", "[x]"]
    return ["[ ]", "[ ]", "[ ]", "[ ]", "[ ]"]


def _aborted_task_trace_stages(task: dict) -> list[str]:
    if task["verifier_status"]:
        return ["[x]", "[x]", "[x]", "[!]", "[ ]"]
    if task["builder_status"]:
        return ["[x]", "[x]", "[!]", "[ ]", "[ ]"]
    return ["[!]", "[ ]", "[ ]", "[ ]", "[ ]"]


def _format_trace_row(columns: list[str], widths: list[int]) -> str:
    padded = [f"{value:<{width}}" for value, width in zip(columns, widths)]
    return "  ".join(padded).rstrip()


def _format_trace_separator(widths: list[int]) -> str:
    return "  ".join("-" * width for width in widths)


def _is_coordinator_trace_event(event: str) -> bool:
    return (
        event.startswith("phase=")
        or event.startswith("dispatch ")
        or event.startswith("Final review")
        or event.startswith("error=")
    )


def _format_transition_event(event: str) -> str:
    if event.startswith("dispatch builder=Claude"):
        task_id = _extract_event_field(event, "task_id") or "current task"
        return f"Dispatch Claude builder for {task_id}."
    if event.startswith("dispatch verifier=Codex"):
        task_id = _extract_event_field(event, "task_id") or "current task"
        return f"Dispatch Codex verifier for {task_id}."
    if event.startswith("Final review"):
        return event.replace("Final review ", "Final review: ", 1)
    if event.startswith("error="):
        return event.replace("error=", "Coordinator error: ", 1)
    if event.startswith("phase=") and " -> " in event:
        previous_part, rest = event.split(" -> ", 1)
        previous = previous_part.replace("phase=", "", 1)
        current, _, suffix = rest.partition(" ")
        task_id = _extract_event_field(suffix, "task_id")
        if task_id and task_id != "None":
            return f"{previous} -> {current} ({task_id})"
        return f"{previous} -> {current}"
    return event


def _extract_event_field(event: str, field_name: str) -> str | None:
    token = f"{field_name}="
    if token not in event:
        return None
    value = event.split(token, 1)[1].split(" ", 1)[0].strip()
    return value or None


def _render_task_window(tasks: list[dict], use_color: bool, *, width: int) -> list[str]:
    if not tasks:
        return ["no task records yet"]

    lines: list[str] = []
    groups = _group_tasks_by_module(tasks)
    for module_index, module in enumerate(groups):
        module_label = _colorize_status(module["module_status"].upper(), use_color)
        header = (
            f"[M{module['module_index']:02d}] {module['module_id']}  |  {module['module_title']}  |  "
            f"{module_label}  |  {module['done_count']}/{module['total_count']} subtasks"
        )
        if module["blocked_by"]:
            header += f"  deps: {', '.join(module['blocked_by'])}"
        lines.extend(_wrap_text(header, width=width))
        for task in module["tasks"]:
            badge = _colorize_status(task["task_status"].upper(), use_color)
            lines.append(f"  {task['task_id']}  {badge}")
            lines.extend(_wrap_prefixed("    Task   : ", task["title"], width=width))
            lines.append(
                f"    Scope  : module {task['module_task_index']} / {task['module_total']}  |  overall {task['task_index']} / {task['task_total']}"
            )
            lines.extend(
                _wrap_prefixed(
                    "    Status : ",
                    _compact_task_checks(task, use_color),
                    width=width,
                )
            )
            detail_lines = _compact_task_detail_lines(task, use_color, width)
            lines.extend(detail_lines)
            if task is not module["tasks"][-1]:
                lines.append("")
        if module_index != len(groups) - 1:
            lines.append("")
    return lines


def _render_ready_queue_panel(payload: dict, use_color: bool, *, width: int) -> list[str]:
    scheduler = payload["scheduler"]
    queue = payload["queue"]
    progress = payload["progress"]
    lines = [
        (
            f"Workers    : {scheduler['active_lanes']} active / {scheduler['max_parallel_modules']} lanes"
            f"  |  ready backlog {scheduler['ready_count']}"
        ),
        (
            f"Blocked    : {scheduler['blocked_count']} modules"
            f"  |  failed {progress['failed_tasks']}"
        ),
        "",
        "Scheduler Lanes",
    ]
    for lane in scheduler["lanes"]:
        lane_status = _colorize_status(lane["status"].upper(), use_color)
        if lane["task_id"]:
            lines.append(
                f"  {lane['lane_id']}  {lane_status}  {lane['task_id']}  ({lane['module_id']})"
            )
            lines.extend(_wrap_prefixed("      Task : ", lane["task_title"] or "-", width=width))
            lines.extend(_wrap_prefixed("      Note : ", lane["note"] or "-", width=width))
        else:
            lines.append(f"  {lane['lane_id']}  {lane_status}")
            lines.extend(_wrap_prefixed("      Note : ", lane["note"] or "-", width=width))
    lines.extend(["", "Ready Backlog"])
    if queue["ready"]:
        for task in queue["ready"][:6]:
            lines.append(
                f"  {_colorize_status('READY', use_color)}  {task['task_id']}  ({task['module_id']})"
            )
            lines.extend(_wrap_prefixed("      Task : ", task["title"], width=width))
    else:
        lines.append("  no ready tasks waiting")

    lines.extend(["", "Blocked Modules"])
    if queue["blocked_modules"]:
        for item in queue["blocked_modules"][:6]:
            lines.extend(
                _wrap_prefixed(
                    "  BLOCKED : ",
                    f"{item['module_id']} <- {', '.join(item['blocked_by'])}",
                    width=width,
                )
            )
    else:
        lines.append("  none")

    lines.extend(["", "Failed Tasks"])
    if payload["failed_tasks"]:
        for task in payload["failed_tasks"][:6]:
            lines.append(
                f"  {_colorize_status('FAILED', use_color)}  {task['task_id']}  ({task['module_id']})"
            )
            summary = task["verifier_summary"] or task["builder_summary"] or task["last_error"] or task["title"]
            lines.extend(_wrap_prefixed("      Note : ", summary, width=width))
    else:
        lines.append("  none")
    return lines


def _current_task_report(payload: dict) -> dict | None:
    current_task_id = payload["run"]["current_task_id"]
    if not current_task_id:
        return None
    for task in payload["tasks"]:
        if task["task_id"] == current_task_id:
            return task
    return None


def _group_tasks_by_module(tasks: list[dict]) -> list[dict]:
    groups: list[dict] = []
    current_group: dict | None = None
    for task in tasks:
        if current_group is None or current_group["module_id"] != task["module_id"]:
            current_group = {
                "module_index": task["module_index"],
                "module_id": task["module_id"],
                "module_title": task["module_title"],
                "module_status": task["module_status"],
                "done_count": task["module_done_count"],
                "total_count": task["module_total"],
                "blocked_by": task["module_blocked_by"],
                "tasks": [],
            }
            groups.append(current_group)
        current_group["tasks"].append(task)
    return groups


def _fit_column(title: str, items: list[str], width: int) -> list[str]:
    lines = [title, "-" * min(width, 24)]
    for item in items:
        wrapped = _wrap_text(item, width=width) or [""]
        lines.extend(wrapped)
    return [_truncate_visible(line, width) for line in lines]


def _wrap_prefixed(prefix: str, text: str, width: int) -> list[str]:
    available = max(width - len(prefix), 20)
    wrapped = _wrap_text(text, width=available) or [""]
    lines = [f"{prefix}{wrapped[0]}"]
    continuation = " " * len(prefix)
    for line in wrapped[1:]:
        lines.append(f"{continuation}{line}")
    return lines


def _wrap_text(text: str, width: int) -> list[str]:
    max_width = max(width, 20)
    if _visible_len(text) <= max_width:
        return [text]

    lines: list[str] = []
    remaining = text
    while remaining:
        head, tail = _split_visible(remaining, max_width)
        lines.append(head.rstrip())
        remaining = tail.lstrip()
        if not tail:
            break
    return lines or [""]


def _compact_task_checks(task: dict, use_color: bool) -> str:
    builder = _colorize_status(task["builder_status"] or "-", use_color)
    verifier = _colorize_status(task["verifier_status"] or "-", use_color)
    return f"builder {builder}  |  verifier {verifier}"


def _compact_task_detail_lines(task: dict, use_color: bool, width: int) -> list[str]:
    is_active = task["task_status"] == "active"
    is_failed = task["task_status"] == "failed"
    if not is_active and not is_failed:
        return []

    summary = (
        task["verifier_summary"]
        or task["builder_summary"]
        or task["title"]
    )
    label = "Focus  : " if is_active else "Issue  : "
    return _wrap_prefixed(f"    {label}", _truncate_summary(summary, width - 12), width=width)


def _truncate_summary(text: str, width: int) -> str:
    if _visible_len(text) <= width:
        return text
    clipped = _truncate_visible(text, max(width - 1, 20)).rstrip()
    return f"{clipped}..."


def _strip_ansi(text: str) -> str:
    return ANSI_PATTERN.sub("", text)


def _visible_len(text: str) -> int:
    visible = 0
    plain = _strip_ansi(text)
    for char in plain:
        visible += _char_width(char)
    return visible


def _pad_visible(text: str, width: int) -> str:
    visible = _visible_len(text)
    if visible >= width:
        return _truncate_visible(text, width)
    return text + (" " * (width - visible))


def _truncate_visible(text: str, width: int) -> str:
    if _visible_len(text) <= width:
        return text

    result: list[str] = []
    visible = 0
    index = 0
    while index < len(text) and visible < width:
        if text[index] == "\x1b":
            match = ANSI_PATTERN.match(text, index)
            if match:
                result.append(match.group(0))
                index = match.end()
                continue
        char = text[index]
        char_width = _char_width(char)
        if visible + char_width > width:
            break
        result.append(char)
        visible += char_width
        index += 1
    if result and not result[-1].endswith("\033[0m") and "\x1b[" in text:
        result.append("\033[0m")
    return "".join(result)


def _split_visible(text: str, width: int) -> tuple[str, str]:
    result: list[str] = []
    visible = 0
    index = 0
    last_space_index = -1
    last_space_output_len = -1
    while index < len(text):
        if text[index] == "\x1b":
            match = ANSI_PATTERN.match(text, index)
            if match:
                result.append(match.group(0))
                index = match.end()
                continue
        char = text[index]
        char_width = _char_width(char)
        if visible + char_width > width:
            break
        result.append(char)
        visible += char_width
        if char.isspace():
            last_space_index = index
            last_space_output_len = len(result)
        index += 1

    if index >= len(text):
        return "".join(result), ""

    if last_space_index != -1 and last_space_output_len != -1:
        head = "".join(result[:last_space_output_len]).rstrip()
        tail = text[last_space_index + 1 :]
    else:
        head = "".join(result).rstrip()
        tail = text[index:]

    if head and not head.endswith("\033[0m") and "\x1b[" in head:
        head += "\033[0m"
    return head, tail


def _char_width(char: str) -> int:
    if not char:
        return 0
    if unicodedata.combining(char):
        return 0
    if unicodedata.east_asian_width(char) in {"W", "F"}:
        return 2
    return 1


def _colorize_status(status: str, use_color: bool) -> str:
    mapping = {
        "active": "32",
        "ACTIVE": "32",
        "pending": "33",
        "PENDING": "33",
        "queued": "34",
        "QUEUED": "34",
        "ready": "34",
        "READY": "34",
        "blocked": "31",
        "BLOCKED": "31",
        "failed": "31",
        "FAILED": "31",
        "done": "36",
        "DONE": "36",
        "in_progress": "35",
        "IN_PROGRESS": "35",
        "running": "32",
        "done": "36",
        "completed": "36",
        "waiting": "33",
        "queued": "33",
        "idle": "34",
        "IDLE": "34",
        "stopped": "90",
        "aborted": "31",
        "ABORTED": "31",
        "COMPLETED": "36",
        "BUILDER_RUNNING": "32",
        "VERIFIER_RUNNING": "32",
        "READY_FOR_VERIFICATION": "33",
        "SELECT_TASK": "33",
        "FINAL_REVIEW": "35",
        "SELF_TEST_PASSED": "36",
        "SELF_TEST_FAILED": "31",
        "PASS": "36",
        "FAIL": "31",
        "BLOCKED": "31",
    }
    code = mapping.get(status, "37")
    return _colorize_text(status, code, use_color)


def _colorize_text(text: str, code: str, use_color: bool) -> str:
    if not use_color:
        return text
    return f"\033[{code}m{text}\033[0m"


def _format_duration(total_seconds: int) -> str:
    hours, remainder = divmod(max(total_seconds, 0), 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _fit_dashboard_lines(lines: list[str], use_color: bool) -> list[str]:
    terminal_rows = shutil.get_terminal_size((120, 40)).lines
    limit = max(terminal_rows - 1, 20)
    if len(lines) <= limit:
        return lines
    clipped = lines[: limit - 1]
    clipped.append(_colorize_text("... dashboard clipped to fit terminal height ...", "90", use_color))
    return clipped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shipyard")
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=[
            "run",
            "step",
            "status",
            "reset",
            "watch",
            "report",
            "dashboard",
            "stop",
            "resume",
            "start",
            "rerun",
            "failed",
            "rerun-failed",
        ],
    )
    parser.add_argument("task_id", nargs="?")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--agent-windows", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    echo = args.command in {"run", "step"}
    root = Path.cwd()
    paths = RepositoryPaths(root)
    paths.ensure_runtime_dirs()
    engine = ShipyardEngine(root, echo=echo, agent_windows=args.agent_windows)

    try:
        if args.command == "run":
            _write_run_pid(paths)
            try:
                state = engine.run()
            finally:
                _clear_run_pid(paths)
            print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
            return 0 if state.phase == "COMPLETED" else 1
        if args.command == "resume":
            _write_run_pid(paths)
            try:
                state = engine.run()
            finally:
                _clear_run_pid(paths)
            print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
            return 0 if state.phase == "COMPLETED" else 1
        if args.command == "step":
            state = engine.step()
            print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
            return 0 if state.phase != "ABORTED" else 1
        if args.command == "status":
            print(json.dumps(engine.status_payload(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "failed":
            print(json.dumps(engine.report_payload()["failed_tasks"], ensure_ascii=False, indent=2))
            return 0
        if args.command == "stop":
            engine.request_stop()
            _interrupt_active_processes(paths)
            print("Stop requested. Shipyard will halt at the current task boundary.")
            return 0
        if args.command == "start":
            if not args.task_id:
                raise ShipyardError("start requires a task_id.")
            state = engine.select_task(args.task_id, force_rerun=False)
            print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "rerun":
            if not args.task_id:
                raise ShipyardError("rerun requires a task_id.")
            state = engine.select_task(args.task_id, force_rerun=True)
            print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "rerun-failed":
            print(json.dumps(engine.rerun_failed_tasks(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "report":
            print(_render_report(engine.report_payload()))
            return 0
        if args.command == "watch":
            return _watch(
                ShipyardEngine(root, echo=False, agent_windows=args.agent_windows),
                interval_seconds=max(args.interval, 0.5),
            )
        if args.command == "dashboard":
            _write_run_pid(paths)
            try:
                return _dashboard(
                    root,
                    interval_seconds=max(args.interval, 0.2),
                    agent_windows=args.agent_windows,
                )
            finally:
                _clear_run_pid(paths)
        engine.reset()
        print("Shipyard runtime state reset.")
        return 0
    except ShipyardError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Shipyard interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
