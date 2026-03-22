"""Shipyard orchestration engine."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from shipyard.adapters.claude_builder import ClaudeBuilder
from shipyard.adapters.codex_verifier import CodexVerifier
from shipyard.adapters.mock_builder import MockBuilder
from shipyard.adapters.mock_verifier import MockVerifier
from shipyard.config import AppConfig, load_config
from shipyard.exceptions import AdapterError, ConfigError, FinalReviewError, ShipyardError
from shipyard.logger import RunLogger
from shipyard.models import OrchestratorState, Phase, TaskItem
from shipyard.repository import RepositoryPaths
from shipyard.services.completion_service import run_final_review
from shipyard.services.handoff_service import HandoffService
from shipyard.services.task_selector import select_next_task
from shipyard.services.transition_service import (
    validate_builder_result,
    validate_verifier_result,
)
from shipyard.state_store import StateStore
from shipyard.task_parser import mark_task_done, parse_tasks


class ShipyardEngine:
    def __init__(self, root: Path | str, echo: bool = False, agent_windows: bool = False) -> None:
        self.paths = RepositoryPaths(Path(root).resolve())
        self.paths.ensure_runtime_dirs()
        self.config = load_config(self.paths)
        self.store = StateStore(self.paths)
        self.logger = RunLogger(
            self.paths.run_log_file,
            echo=echo,
            sidecar_logs={
                "claude": self.paths.claude_log_file,
                "codex": self.paths.codex_log_file,
            },
        )
        self.handoff = HandoffService(self.paths)
        self.builder = self._build_builder(
            self.config,
            self.paths.root,
            self.logger,
            agent_windows=agent_windows,
        )
        self.verifier = self._build_verifier(
            self.config,
            self.paths.root,
            self.logger,
            agent_windows=agent_windows,
        )

    def run(self) -> OrchestratorState:
        state = self.store.load_or_init()
        while state.phase not in {Phase.COMPLETED.value, Phase.ABORTED.value}:
            state = self.step()
        return state

    def step(self) -> OrchestratorState:
        try:
            state = self.store.load_or_init()
            previous_phase = state.phase
            next_state = self._step_state_machine(state)
            self.logger.log(
                f"phase={previous_phase} -> {next_state.phase} task_id={next_state.current_task_id}"
            )
            return next_state
        except ShipyardError as exc:
            self.logger.log(f"error={exc}")
            state.phase = Phase.ABORTED.value
            state.last_error = str(exc)
            if state.current_task_id and state.current_task_id not in state.failed_task_ids:
                state.failed_task_ids.append(state.current_task_id)
            self.store.save(state)
            raise

    def reset(self) -> None:
        self.store.reset()

    def status_payload(self) -> dict:
        state = self.store.load_or_init()
        tasks = parse_tasks(self.paths.tasks_file)
        return {
            "phase": state.phase,
            "current_task_id": state.current_task_id,
            "current_task_title": state.current_task_title,
            "builder_attempt": state.builder_attempt,
            "verifier_attempt": state.verifier_attempt,
            "completed_count": len([task for task in tasks if task.done]),
            "total_count": len(tasks),
            "last_error": state.last_error,
        }

    def report_payload(self) -> dict:
        state = self.store.load_or_init()
        tasks = parse_tasks(self.paths.tasks_file)
        task_reports: list[dict] = []
        for task in tasks:
            builder_result = self.handoff.load_builder_result(task.task_id)
            verifier_result = self.handoff.load_verifier_result(task.task_id)
            lifecycle_status = (
                "done"
                if task.done
                else "active"
                if state.current_task_id == task.task_id
                else "pending"
            )
            task_reports.append(
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "task_status": lifecycle_status,
                    "builder_status": builder_result.get("status") if builder_result else None,
                    "builder_summary": builder_result.get("summary") if builder_result else None,
                    "verifier_status": verifier_result.get("status") if verifier_result else None,
                    "verifier_summary": verifier_result.get("summary") if verifier_result else None,
                }
            )

        return {
            "run": {
                "run_id": state.run_id,
                "phase": state.phase,
                "current_task_id": state.current_task_id,
                "current_task_title": state.current_task_title,
                "builder_attempt": state.builder_attempt,
                "verifier_attempt": state.verifier_attempt,
                "final_review_attempt": state.final_review_attempt,
                "last_error": state.last_error,
                "updated_at": state.updated_at,
            },
            "agents": self._agent_status_payload(state.phase),
            "tasks": task_reports,
            "recent_events": self._recent_events(limit=12),
            "recent_events_by_agent": {
                "claude": self._recent_agent_events(self.paths.claude_log_file, limit=10),
                "codex": self._recent_agent_events(self.paths.codex_log_file, limit=10),
            },
            "recent_shipyard_events": self._recent_shipyard_events(limit=8),
        }

    def _step_state_machine(self, state: OrchestratorState) -> OrchestratorState:
        phase = Phase(state.phase)
        if phase is Phase.INIT:
            state.phase = Phase.SELECT_TASK.value
            state.last_error = None
            self.store.save(state)
            return state
        if phase is Phase.SELECT_TASK:
            return self._select_task(state)
        if phase is Phase.BUILDER_RUNNING:
            return self._run_builder(state)
        if phase is Phase.READY_FOR_VERIFICATION:
            state.phase = Phase.VERIFIER_RUNNING.value
            state.last_error = None
            self.store.save(state)
            return state
        if phase is Phase.VERIFIER_RUNNING:
            return self._run_verifier(state)
        if phase is Phase.TASK_DONE:
            state.phase = Phase.SELECT_TASK.value
            state.last_error = None
            self.store.save(state)
            return state
        if phase is Phase.FINAL_REVIEW:
            return self._run_final_review(state)
        return state

    def _select_task(self, state: OrchestratorState) -> OrchestratorState:
        tasks = parse_tasks(self.paths.tasks_file)
        task = select_next_task(tasks)
        if task is None:
            state.phase = Phase.FINAL_REVIEW.value
            state.current_task_id = None
            state.current_task_title = None
            state.builder_attempt = 0
            state.verifier_attempt = 0
            state.last_error = None
            self.store.save(state)
            return state

        state.phase = Phase.BUILDER_RUNNING.value
        state.current_task_id = task.task_id
        state.current_task_title = task.title
        state.builder_attempt = 0
        state.verifier_attempt = 0
        state.last_error = None
        self.store.save(state)
        return state

    def _run_builder(self, state: OrchestratorState) -> OrchestratorState:
        task = self._require_current_task(state)
        state.builder_attempt += 1
        self.store.save(state)
        self.logger.log(f"dispatch builder=Claude task_id={task.task_id} verifier=waiting")

        prior_review = self.handoff.load_verifier_result(task.task_id)
        result = self.builder.run(
            task_id=task.task_id,
            task_title=task.title,
            docs_context=self._builder_docs_context(task),
            prior_review=prior_review,
            state=asdict(state),
        )
        status = validate_builder_result(result, task.task_id)
        _, relative_path = self.handoff.save_builder_result(result)
        state.last_builder_result_path = relative_path

        if status == "SELF_TEST_PASSED":
            state.phase = Phase.READY_FOR_VERIFICATION.value
            state.last_error = None
        elif status == "SELF_TEST_FAILED":
            state.last_error = result.get("summary")
            if state.builder_attempt >= self.config.max_builder_retries:
                state.phase = Phase.ABORTED.value
                if task.task_id not in state.failed_task_ids:
                    state.failed_task_ids.append(task.task_id)
            else:
                state.phase = Phase.BUILDER_RUNNING.value
        else:
            state.phase = Phase.ABORTED.value
            state.last_error = result.get("summary", "Builder is blocked.")
            if task.task_id not in state.failed_task_ids:
                state.failed_task_ids.append(task.task_id)

        self.store.save(state)
        return state

    def _run_verifier(self, state: OrchestratorState) -> OrchestratorState:
        task = self._require_current_task(state)
        builder_result = self.handoff.load_builder_result(task.task_id)
        if builder_result is None:
            raise AdapterError("Verifier cannot run without a builder artifact.")

        state.verifier_attempt += 1
        self.store.save(state)
        self.logger.log(f"dispatch verifier=Codex task_id={task.task_id} builder=idle")

        result = self.verifier.run(
            task_id=task.task_id,
            task_title=task.title,
            docs_context=self._verifier_docs_context(task),
            builder_result=builder_result,
            state=asdict(state),
        )
        status = validate_verifier_result(result, task.task_id)
        _, relative_path = self.handoff.save_verifier_result(result)
        state.last_verifier_result_path = relative_path

        if status == "PASS":
            mark_task_done(self.paths.tasks_file, task.task_id)
            if task.task_id not in state.completed_task_ids:
                state.completed_task_ids.append(task.task_id)
            state.phase = Phase.TASK_DONE.value
            state.current_task_id = None
            state.current_task_title = None
            state.builder_attempt = 0
            state.verifier_attempt = 0
            state.last_error = None
        elif status == "FAIL":
            state.last_error = result.get("summary")
            if state.verifier_attempt >= self.config.max_verifier_retries:
                state.phase = Phase.ABORTED.value
                if task.task_id not in state.failed_task_ids:
                    state.failed_task_ids.append(task.task_id)
            else:
                state.phase = Phase.BUILDER_RUNNING.value
        else:
            state.phase = Phase.ABORTED.value
            state.last_error = result.get("summary", "Verifier is blocked.")
            if task.task_id not in state.failed_task_ids:
                state.failed_task_ids.append(task.task_id)

        self.store.save(state)
        return state

    def _run_final_review(self, state: OrchestratorState) -> OrchestratorState:
        state.final_review_attempt += 1
        self.store.save(state)
        tasks = parse_tasks(self.paths.tasks_file)
        outcome = run_final_review(
            root=self.paths.root,
            config=self.config,
            tasks=tasks,
            current_task_id=state.current_task_id,
            logger=self.logger,
        )
        state.phase = Phase.COMPLETED.value if outcome.passed else Phase.ABORTED.value
        state.last_error = None if outcome.passed else outcome.summary
        self.store.save(state)
        return state

    def _builder_docs_context(self, task: TaskItem) -> dict:
        return {
            "prd_text": self._read_optional(self.paths.prd_file),
            "tasks_text": self.paths.tasks_file.read_text(encoding="utf-8"),
            "dev_spec_text": self._read_optional(self.paths.dev_spec_file),
            "acceptance_spec_text": "",
            "current_task": {"task_id": task.task_id, "title": task.title},
        }

    def _recent_events(self, limit: int = 10) -> list[str]:
        if not self.paths.run_log_file.exists():
            return []
        lines = self.paths.run_log_file.read_text(encoding="utf-8").splitlines()
        recent = lines[-limit:]
        normalized: list[str] = []
        for line in recent:
            parts = line.split(" ", 1)
            normalized.append(parts[1] if len(parts) == 2 else line)
        return normalized

    @staticmethod
    def _recent_agent_events(path: Path, limit: int = 10) -> list[str]:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        recent = lines[-limit:]
        normalized: list[str] = []
        for line in recent:
            parts = line.split(" ", 1)
            normalized.append(parts[1] if len(parts) == 2 else line)
        return normalized

    def _recent_shipyard_events(self, limit: int = 8) -> list[str]:
        events = [event for event in self._recent_events(limit=50) if not event.startswith("[")]
        return events[-limit:]

    @staticmethod
    def _agent_status_payload(phase: str) -> dict:
        if phase == Phase.BUILDER_RUNNING.value:
            return {
                "builder": {"name": "Claude", "status": "running"},
                "verifier": {"name": "Codex", "status": "waiting"},
            }
        if phase == Phase.READY_FOR_VERIFICATION.value:
            return {
                "builder": {"name": "Claude", "status": "done"},
                "verifier": {"name": "Codex", "status": "queued"},
            }
        if phase == Phase.VERIFIER_RUNNING.value:
            return {
                "builder": {"name": "Claude", "status": "idle"},
                "verifier": {"name": "Codex", "status": "running"},
            }
        if phase in {Phase.COMPLETED.value, Phase.ABORTED.value}:
            return {
                "builder": {"name": "Claude", "status": "stopped"},
                "verifier": {"name": "Codex", "status": "stopped"},
            }
        return {
            "builder": {"name": "Claude", "status": "waiting"},
            "verifier": {"name": "Codex", "status": "waiting"},
        }

    def _verifier_docs_context(self, task: TaskItem) -> dict:
        return {
            "prd_text": self._read_optional(self.paths.prd_file),
            "tasks_text": self.paths.tasks_file.read_text(encoding="utf-8"),
            "dev_spec_text": "",
            "acceptance_spec_text": self._read_optional(self.paths.acceptance_spec_file),
            "current_task": {"task_id": task.task_id, "title": task.title},
        }

    @staticmethod
    def _read_optional(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _require_current_task(state: OrchestratorState) -> TaskItem:
        if not state.current_task_id or not state.current_task_title:
            raise AdapterError("Current task is missing from state.")
        return TaskItem(
            task_id=state.current_task_id,
            title=state.current_task_title,
            done=False,
        )

    @staticmethod
    def _build_builder(
        config: AppConfig,
        root: Path | None = None,
        logger: RunLogger | None = None,
        agent_windows: bool = False,
    ):
        if config.builder_adapter == "mock_builder":
            return MockBuilder(config.mock_builder_failures)
        if config.builder_adapter == "claude_builder":
            if root is None:
                raise ConfigError("Repository root is required for claude_builder.")
            return ClaudeBuilder(
                root=root,
                command=config.claude_command,
                logger=logger,
                use_terminal_window=agent_windows,
            )
        raise ConfigError(f"Unsupported builder adapter: {config.builder_adapter}")

    @staticmethod
    def _build_verifier(
        config: AppConfig,
        root: Path | None = None,
        logger: RunLogger | None = None,
        agent_windows: bool = False,
    ):
        if config.verifier_adapter == "mock_verifier":
            return MockVerifier(config.mock_verifier_failures)
        if config.verifier_adapter == "codex_verifier":
            if root is None:
                raise ConfigError("Repository root is required for codex_verifier.")
            return CodexVerifier(
                root=root,
                command=config.codex_command,
                logger=logger,
                use_terminal_window=agent_windows,
            )
        raise ConfigError(f"Unsupported verifier adapter: {config.verifier_adapter}")
