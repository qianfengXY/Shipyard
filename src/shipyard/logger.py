"""Simple text logger for Shipyard runs."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class RunLogger:
    def __init__(
        self,
        log_file: Path,
        echo: bool = False,
        sidecar_logs: dict[str, Path] | None = None,
    ) -> None:
        self.log_file = log_file
        self.echo = echo
        self.sidecar_logs = sidecar_logs or {}
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        for path in self.sidecar_logs.values():
            path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, message: str) -> None:
        with self.log_file.open("a", encoding="utf-8") as handle:
            handle.write(f"{now_iso()} {message}\n")
        if self.echo:
            print(message, file=sys.stderr)

    def stream(self, source: str, line: str) -> None:
        message = f"[{source}] {line}"
        self.log(message)
        sidecar = self.sidecar_logs.get(source)
        if sidecar is not None:
            with sidecar.open("a", encoding="utf-8") as handle:
                handle.write(f"{now_iso()} {line}\n")
