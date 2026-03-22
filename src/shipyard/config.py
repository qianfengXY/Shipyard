"""Runtime configuration handling."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from shipyard.exceptions import ConfigError
from shipyard.repository import RepositoryPaths


@dataclass
class AppConfig:
    builder_adapter: str = "mock_builder"
    verifier_adapter: str = "mock_verifier"
    max_builder_retries: int = 3
    max_verifier_retries: int = 3
    max_parallel_modules: int = 2
    final_review_commands: list[str] = field(default_factory=lambda: ["python -m pytest"])
    claude_command: str = "claude"
    codex_command: str = "codex"
    mock_builder_failures: dict[str, int] = field(default_factory=dict)
    mock_verifier_failures: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def default_config() -> AppConfig:
    return AppConfig()


def load_config(paths: RepositoryPaths) -> AppConfig:
    paths.ensure_runtime_dirs()
    if not paths.config_file.exists():
        config = default_config()
        _atomic_write_json(paths.config_file, config.to_dict())
        return config

    try:
        payload = json.loads(paths.config_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid config JSON: {exc}") from exc

    defaults = default_config()
    payload.setdefault("claude_command", defaults.claude_command)
    payload.setdefault("codex_command", defaults.codex_command)
    payload.setdefault("max_parallel_modules", defaults.max_parallel_modules)

    required_str_fields = ["builder_adapter", "verifier_adapter", "claude_command", "codex_command"]
    for field_name in required_str_fields:
        if not isinstance(payload.get(field_name), str) or not payload[field_name]:
            raise ConfigError(f"Config field '{field_name}' must be a non-empty string.")

    required_int_fields = ["max_builder_retries", "max_verifier_retries", "max_parallel_modules"]
    for field_name in required_int_fields:
        if not isinstance(payload.get(field_name), int) or payload[field_name] < 1:
            raise ConfigError(f"Config field '{field_name}' must be an integer >= 1.")

    final_review_commands = payload.get("final_review_commands", [])
    if not isinstance(final_review_commands, list) or not all(
        isinstance(item, str) for item in final_review_commands
    ):
        raise ConfigError("Config field 'final_review_commands' must be a list of strings.")

    mock_builder_failures = payload.get("mock_builder_failures", {})
    mock_verifier_failures = payload.get("mock_verifier_failures", {})
    for name, value in {
        "mock_builder_failures": mock_builder_failures,
        "mock_verifier_failures": mock_verifier_failures,
    }.items():
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, int) and item >= 0
            for key, item in value.items()
        ):
            raise ConfigError(f"Config field '{name}' must be a mapping of task_id to int.")

    return AppConfig(
        builder_adapter=payload["builder_adapter"],
        verifier_adapter=payload["verifier_adapter"],
        max_builder_retries=payload["max_builder_retries"],
        max_verifier_retries=payload["max_verifier_retries"],
        max_parallel_modules=payload["max_parallel_modules"],
        final_review_commands=final_review_commands,
        claude_command=payload["claude_command"],
        codex_command=payload["codex_command"],
        mock_builder_failures=mock_builder_failures,
        mock_verifier_failures=mock_verifier_failures,
    )
