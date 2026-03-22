"""Run an interactive agent UI in a PTY and stop it after the result file appears."""

from __future__ import annotations

import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import termios
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 1:
        print("Usage: python -m shipyard.agent_ui_runner <config.json>", file=sys.stderr)
        return 2

    config_path = Path(argv[0])
    config = json.loads(config_path.read_text(encoding="utf-8"))

    source = str(config.get("source", "agent"))
    args = list(config["args"])
    cwd = Path(config["cwd"])
    prompt = Path(config["prompt_file"]).read_text(encoding="utf-8")
    status_file = Path(config["status_file"])
    result_file = Path(config["result_file"])

    master_fd, slave_fd = pty.openpty()
    _copy_window_size(sys.stdin.fileno(), slave_fd)
    process = subprocess.Popen(
        [*args, prompt],
        cwd=cwd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        text=False,
        start_new_session=True,
    )
    os.close(slave_fd)

    result_seen = False
    trusted_workspace_confirmed = False
    recent_output = ""
    started_at = time.monotonic()
    try:
        while True:
            ready, _, _ = select.select([master_fd], [], [], 0.2)
            if master_fd in ready:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    chunk = b""
                if chunk:
                    os.write(sys.stdout.fileno(), chunk)
                    recent_output = (recent_output + chunk.decode("utf-8", errors="ignore"))[-2000:]

            if source == "codex" and not trusted_workspace_confirmed:
                normalized = _normalize_terminal_text(recent_output)
                if (
                    "do you trust the contents of this directory?" in normalized
                    or (
                        time.monotonic() - started_at >= 1.5
                        and "press enter to continue" in normalized
                    )
                ):
                    os.write(master_fd, b"1\n\n")
                    trusted_workspace_confirmed = True

            if result_file.exists() and not result_seen:
                result_seen = True
                _terminate_agent(process.pid)

            return_code = process.poll()
            if return_code is not None:
                if result_seen and result_file.exists():
                    status_file.write_text("0", encoding="utf-8")
                    return 0
                status_file.write_text(str(return_code), encoding="utf-8")
                return return_code
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass


def _copy_window_size(source_fd: int, target_fd: int) -> None:
    try:
        winsize = termios.tcgetwinsize(source_fd)
        termios.tcsetwinsize(target_fd, winsize)
    except OSError:
        return


def _normalize_terminal_text(value: str) -> str:
    stripped = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", value)
    stripped = re.sub(r"\s+", " ", stripped)
    return stripped.lower()


def _terminate_agent(pid: int) -> None:
    signals = [signal.SIGINT, signal.SIGINT, signal.SIGTERM, signal.SIGKILL]
    delays = [0.5, 0.5, 1.0, 0.0]
    for sig, delay in zip(signals, delays):
        try:
            os.killpg(pid, sig)
        except OSError:
            return
        if delay:
            time.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(main())
