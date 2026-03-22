"""CLI entrypoint for Shipyard."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
import time
import textwrap
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shipyard.engine import ShipyardEngine
from shipyard.exceptions import ShipyardError


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

    def runner() -> None:
        try:
            outcome["state"] = engine.run()
        except Exception as exc:  # pragma: no cover
            outcome["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()

    use_tty = sys.stdout.isatty()
    if use_tty:
        sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    try:
        first_frame = True
        while True:
            payload = engine.report_payload()
            if use_tty:
                sys.stdout.write("\033[H\033[2J" if not first_frame else "\033[2J\033[H")
            sys.stdout.write(_render_dashboard(payload))
            sys.stdout.write("\n")
            sys.stdout.flush()
            first_frame = False

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
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()

    if outcome["error"] is not None:
        raise outcome["error"]  # type: ignore[misc]
    state = outcome["state"]
    if state is None:
        return 1
    sys.stdout.write(
        f"\nDashboard finished with phase {getattr(state, 'phase', 'UNKNOWN')}.\n"
    )
    sys.stdout.flush()
    return 0 if getattr(state, "phase", "") == "COMPLETED" else 1


def _render_report(payload: dict) -> str:
    run = payload["run"]
    agents = payload["agents"]
    lines = [
        "Shipyard Monitor",
        "=" * 72,
        f"run_id: {run['run_id']}",
        f"phase: {run['phase']}",
        f"current_task: {run['current_task_id']} {run['current_task_title'] or ''}".strip(),
        f"builder_attempt: {run['builder_attempt']}",
        f"verifier_attempt: {run['verifier_attempt']}",
        f"final_review_attempt: {run['final_review_attempt']}",
        f"updated_at: {run['updated_at']}",
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
    for task in payload["tasks"]:
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
    use_color = sys.stdout.isatty()
    task_lines: list[str] = []
    for task in payload["tasks"]:
        task_lines.append(f"{task['task_id']} [{task['task_status']}] {task['title']}")
        if task["builder_status"] or task["builder_summary"]:
            task_lines.append(
                f"  Claude: {task['builder_status'] or '-'} | {task['builder_summary'] or ''}".rstrip()
            )
        if task["verifier_status"] or task["verifier_summary"]:
            task_lines.append(
                f"  Codex:  {task['verifier_status'] or '-'} | {task['verifier_summary'] or ''}".rstrip()
            )

    shipyard_events = payload["recent_shipyard_events"] or ["no coordinator events yet"]
    claude_events = payload["recent_events_by_agent"]["claude"] or ["no Claude events yet"]
    codex_events = payload["recent_events_by_agent"]["codex"] or ["no Codex events yet"]
    lines = [
        "Shipyard Dashboard",
        "=" * 80,
        f"Phase        : {_colorize_status(run['phase'], use_color)}",
        f"Current Task : {run['current_task_id'] or '-'} {run['current_task_title'] or ''}".rstrip(),
        f"Claude       : {_colorize_status(agents['builder']['status'], use_color)}",
        f"Codex        : {_colorize_status(agents['verifier']['status'], use_color)}",
        f"Attempts     : builder={run['builder_attempt']} verifier={run['verifier_attempt']} final={run['final_review_attempt']}",
        f"Updated At   : {run['updated_at']}",
    ]
    if run["last_error"]:
        lines.append(f"Last Error   : {_colorize_text(run['last_error'], '31', use_color)}")
    lines.extend(
        [
            "",
            "Tasks",
            "-" * 80,
            *task_lines,
            "",
            "Coordinator Events",
            "-" * 80,
            *shipyard_events,
            "",
            *_render_event_columns(claude_events, codex_events),
            "",
            "Press Ctrl+C to exit the dashboard.",
        ]
    )
    return "\n".join(lines)


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


def _fit_column(title: str, items: list[str], width: int) -> list[str]:
    lines = [title, "-" * min(width, 24)]
    for item in items:
        wrapped = textwrap.wrap(item, width=width) or [""]
        lines.extend(wrapped)
    return [line[:width] for line in lines]


def _colorize_status(status: str, use_color: bool) -> str:
    mapping = {
        "running": "32",
        "done": "36",
        "completed": "36",
        "waiting": "33",
        "queued": "33",
        "idle": "34",
        "stopped": "90",
        "aborted": "31",
        "ABORTED": "31",
        "COMPLETED": "36",
        "BUILDER_RUNNING": "32",
        "VERIFIER_RUNNING": "32",
        "READY_FOR_VERIFICATION": "33",
        "SELECT_TASK": "33",
        "FINAL_REVIEW": "35",
    }
    code = mapping.get(status, "37")
    return _colorize_text(status, code, use_color)


def _colorize_text(text: str, code: str, use_color: bool) -> str:
    if not use_color:
        return text
    return f"\033[{code}m{text}\033[0m"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shipyard")
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=["run", "step", "status", "reset", "watch", "report", "dashboard"],
    )
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--agent-windows", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    echo = args.command in {"run", "step"}
    engine = ShipyardEngine(Path.cwd(), echo=echo, agent_windows=args.agent_windows)

    try:
        if args.command == "run":
            state = engine.run()
            print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
            return 0 if state.phase == "COMPLETED" else 1
        if args.command == "step":
            state = engine.step()
            print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
            return 0 if state.phase != "ABORTED" else 1
        if args.command == "status":
            print(json.dumps(engine.status_payload(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "report":
            print(_render_report(engine.report_payload()))
            return 0
        if args.command == "watch":
            return _watch(
                ShipyardEngine(Path.cwd(), echo=False, agent_windows=args.agent_windows),
                interval_seconds=max(args.interval, 0.5),
            )
        if args.command == "dashboard":
            return _dashboard(
                Path.cwd(),
                interval_seconds=max(args.interval, 0.2),
                agent_windows=args.agent_windows,
            )
        engine.reset()
        print("Shipyard runtime state reset.")
        return 0
    except ShipyardError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
