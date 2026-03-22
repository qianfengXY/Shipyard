"""Shipyard orchestration engine."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
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
from shipyard.services.task_selector import build_task_queue, select_next_task
from shipyard.services.transition_service import (
    validate_builder_result,
    validate_verifier_result,
)
from shipyard.state_store import StateStore
from shipyard.task_parser import mark_task_done, mark_task_pending, parse_tasks


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
        if state.phase == Phase.ABORTED.value:
            state = self._resume_aborted_state(state)
        while state.phase not in {Phase.COMPLETED.value, Phase.ABORTED.value}:
            state = self.step()
        return state

    def step(self) -> OrchestratorState:
        try:
            state = self.store.load_or_init()
            if state.phase == Phase.ABORTED.value:
                state = self._resume_aborted_state(state)
            self._apply_stop_request(state)
            previous_phase = state.phase
            next_state = self._step_state_machine(state)
            self._sync_task_records(next_state)
            self.logger.log(
                f"phase={previous_phase} -> {next_state.phase} task_id={next_state.current_task_id}"
            )
            return next_state
        except ShipyardError as exc:
            self.logger.log(f"error={exc}")
            stop_request = self._load_stop_request()
            state.phase = Phase.ABORTED.value
            state.last_error = stop_request.get("reason") if stop_request else str(exc)
            if state.current_task_id and state.current_task_id not in state.failed_task_ids:
                state.failed_task_ids.append(state.current_task_id)
            self.store.save(state)
            self._sync_task_records(state)
            self.clear_stop_request()
            raise

    def reset(self) -> None:
        self.store.reset()

    def request_stop(self, reason: str = "Stopped by user.") -> None:
        self.paths.control_file.write_text(
            json.dumps(
                {
                    "action": "stop",
                    "reason": reason,
                    "requested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def clear_stop_request(self) -> None:
        if self.paths.control_file.exists():
            self.paths.control_file.unlink()

    def select_task(self, task_id: str, *, force_rerun: bool = False) -> OrchestratorState:
        state = self.store.load_or_init()
        tasks = parse_tasks(self.paths.tasks_file)
        task = next((item for item in tasks if item.task_id == task_id), None)
        if task is None:
            raise AdapterError(f"Task id not found: {task_id}")
        if task.done and not force_rerun:
            raise AdapterError(f"Task {task_id} is already completed. Use rerun to force it again.")

        if force_rerun and task.done:
            mark_task_pending(self.paths.tasks_file, task_id)

        state.phase = Phase.BUILDER_RUNNING.value
        state.current_task_id = task.task_id
        state.current_task_title = task.title
        state.current_task_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        state.builder_attempt = 0
        state.verifier_attempt = 0
        state.last_error = None
        state.failed_task_ids = [item for item in state.failed_task_ids if item != task_id]
        state.completed_task_ids = [item for item in state.completed_task_ids if item != task_id]
        self._delete_task_outputs(task_id)
        self.store.save(state)
        self._sync_task_records(state)
        self.logger.log(f"select task_id={task_id} force_rerun={str(force_rerun).lower()}")
        return state

    def rerun_failed_tasks(self) -> dict:
        state = self.store.load_or_init()
        tasks = parse_tasks(self.paths.tasks_file)
        failed_task_ids = [
            task.task_id
            for task in tasks
            if task.task_id in state.failed_task_ids
        ]
        for task_id in failed_task_ids:
            task = next((item for item in tasks if item.task_id == task_id), None)
            if task and task.done:
                mark_task_pending(self.paths.tasks_file, task_id)
            self._delete_task_outputs(task_id)

        state.failed_task_ids = []
        state.current_task_id = None
        state.current_task_title = None
        state.current_task_started_at = None
        state.builder_attempt = 0
        state.verifier_attempt = 0
        state.last_error = None
        state.phase = Phase.SELECT_TASK.value if failed_task_ids else state.phase
        self.store.save(state)
        self._sync_task_records(state)
        self._sync_failed_tasks([], state)
        self.logger.log(
            "rerun_failed task_ids="
            + (",".join(failed_task_ids) if failed_task_ids else "none")
        )
        return {
            "failed_task_ids": failed_task_ids,
            "count": len(failed_task_ids),
            "phase": state.phase,
        }

    def status_payload(self) -> dict:
        state = self.store.load_or_init()
        tasks = parse_tasks(self.paths.tasks_file)
        return {
            "phase": state.phase,
            "current_task_id": state.current_task_id,
            "current_task_title": state.current_task_title,
            "current_task_started_at": state.current_task_started_at,
            "builder_attempt": state.builder_attempt,
            "verifier_attempt": state.verifier_attempt,
            "completed_count": len([task for task in tasks if task.done]),
            "total_count": len(tasks),
            "last_error": state.last_error,
        }

    def report_payload(self) -> dict:
        state = self.store.load_or_init()
        tasks = parse_tasks(self.paths.tasks_file)
        self._sync_task_records(state)
        task_queue = build_task_queue(tasks, active_task_id=state.current_task_id)
        task_reports = self._build_task_reports(tasks, state)
        failed_tasks = self._build_failed_tasks(task_reports, state)
        self._sync_failed_tasks(failed_tasks, state)
        task_window = self._select_task_window(task_reports, state)
        progress = self._build_progress_summary(task_reports, state, failed_tasks)

        return {
            "run": {
                "run_id": state.run_id,
                "phase": state.phase,
                "current_task_id": state.current_task_id,
                "current_task_title": state.current_task_title,
                "current_task_started_at": state.current_task_started_at,
                "builder_attempt": state.builder_attempt,
                "verifier_attempt": state.verifier_attempt,
                "final_review_attempt": state.final_review_attempt,
                "last_error": state.last_error,
                "updated_at": state.updated_at,
            },
            "agents": self._agent_status_payload(state.phase),
            "progress": progress,
            "failed_tasks": failed_tasks,
            "queue": {
                "active": [
                    {
                        "task_id": task.task_id,
                        "title": task.title,
                        "module_id": task.module_id,
                        "module_title": task.module_title,
                    }
                    for task in task_queue.active
                ],
                "ready": [
                    {
                        "task_id": task.task_id,
                        "title": task.title,
                        "module_id": task.module_id,
                        "module_title": task.module_title,
                    }
                    for task in task_queue.ready
                ],
                "blocked_modules": [
                    {
                        "module_id": module_id,
                        "blocked_by": dependencies,
                    }
                    for module_id, dependencies in task_queue.blocked_modules.items()
                ],
            },
            "scheduler": self._build_scheduler_payload(task_reports, task_queue, state),
            "tasks": task_reports,
            "task_window": task_window,
            "task_records_dir": self.paths.relative_to_root(self.paths.task_records_dir),
            "recent_events": self._recent_events(limit=12),
            "recent_events_by_agent": {
                "claude": self._recent_agent_events(self.paths.claude_log_file, limit=10),
                "codex": self._recent_agent_events(self.paths.codex_log_file, limit=10),
            },
            "recent_shipyard_events": self._recent_shipyard_events(limit=8),
        }

    def _build_task_reports(self, tasks: list[TaskItem], state: OrchestratorState) -> list[dict]:
        queue = build_task_queue(tasks, active_task_id=state.current_task_id)
        ready_task_ids = {task.task_id for task in queue.ready}
        blocked_modules = queue.blocked_modules
        module_groups: dict[str, list[TaskItem]] = {}
        module_order: list[str] = []
        for task in tasks:
            if task.module_id not in module_groups:
                module_groups[task.module_id] = []
                module_order.append(task.module_id)
            module_groups[task.module_id].append(task)

        module_index_lookup = {
            module_id: index for index, module_id in enumerate(module_order, start=1)
        }
        task_reports: list[dict] = []
        total = len(tasks)
        for index, task in enumerate(tasks, start=1):
            module_tasks = module_groups[task.module_id]
            module_done_count = len(
                [
                    item
                    for item in module_tasks
                    if item.done or item.task_id in state.completed_task_ids
                ]
            )
            builder_result = self.handoff.load_builder_result(task.task_id)
            verifier_result = self.handoff.load_verifier_result(task.task_id)
            task_record = self.handoff.load_task_record(task.task_id) or {}
            lifecycle_status = self._task_lifecycle_status(
                task,
                state,
                ready_task_ids=ready_task_ids,
                blocked_modules=blocked_modules,
            )
            task_report = {
                "task_id": task.task_id,
                "title": task.title,
                "task_status": lifecycle_status,
                "task_index": index,
                "task_total": total,
                "module_id": task.module_id,
                "module_title": task.module_title,
                "module_dependencies": list(task.module_dependencies),
                "module_index": module_index_lookup[task.module_id],
                "module_total": len(module_tasks),
                "module_done_count": module_done_count,
                "module_status": self._module_lifecycle_status(
                    module_tasks,
                    state,
                    blocked_modules=blocked_modules,
                ),
                "module_blocked_by": blocked_modules.get(task.module_id, []),
                "module_task_index": module_tasks.index(task) + 1,
                "builder_status": (
                    builder_result.get("status")
                    if builder_result
                    else task_record.get("builder_status")
                ),
                "builder_summary": (
                    builder_result.get("summary")
                    if builder_result
                    else task_record.get("builder_summary")
                ),
                "verifier_status": (
                    verifier_result.get("status")
                    if verifier_result
                    else task_record.get("verifier_status")
                ),
                "verifier_summary": (
                    verifier_result.get("summary")
                    if verifier_result
                    else task_record.get("verifier_summary")
                ),
                "builder_artifact_path": self.paths.relative_to_root(
                    self.paths.builder_artifacts_dir / f"{task.task_id}-result.json"
                )
                if builder_result
                else task_record.get("builder_artifact_path"),
                "verifier_artifact_path": self.paths.relative_to_root(
                    self.paths.verifier_artifacts_dir / f"{task.task_id}-review.json"
                )
                if verifier_result
                else task_record.get("verifier_artifact_path"),
                "updated_at": state.updated_at,
            }
            task_reports.append(task_report)
        return task_reports

    def _build_scheduler_payload(
        self,
        task_reports: list[dict],
        task_queue,
        state: OrchestratorState,
    ) -> dict:
        task_lookup = {task["task_id"]: task for task in task_reports}
        blocked_modules = [
            {
                "module_id": module_id,
                "module_title": next(
                    (
                        task["module_title"]
                        for task in task_reports
                        if task["module_id"] == module_id
                    ),
                    module_id,
                ),
                "blocked_by": dependencies,
            }
            for module_id, dependencies in task_queue.blocked_modules.items()
        ]

        ready_tasks = list(task_queue.ready)
        lanes: list[dict] = []
        lane_count = self.config.max_parallel_modules
        active_count = 0
        for index in range(1, lane_count + 1):
            if index == 1 and task_queue.active:
                task = task_queue.active[0]
                report = task_lookup.get(task.task_id, {})
                active_count += 1
                lanes.append(
                    {
                        "lane_id": f"L{index:02d}",
                        "status": "running",
                        "task_id": task.task_id,
                        "task_title": task.title,
                        "module_id": task.module_id,
                        "module_title": task.module_title,
                        "phase": state.phase,
                        "note": self._scheduler_running_note(state.phase, report),
                    }
                )
                continue

            if ready_tasks:
                task = ready_tasks.pop(0)
                lanes.append(
                    {
                        "lane_id": f"L{index:02d}",
                        "status": "ready",
                        "task_id": task.task_id,
                        "task_title": task.title,
                        "module_id": task.module_id,
                        "module_title": task.module_title,
                        "phase": "SELECT_TASK",
                        "note": "Ready for dispatch once a worker lane is free.",
                    }
                )
                continue

            lanes.append(
                {
                    "lane_id": f"L{index:02d}",
                    "status": "idle",
                    "task_id": None,
                    "task_title": None,
                    "module_id": None,
                    "module_title": None,
                    "phase": None,
                    "note": "Idle lane reserved for future module-level concurrency.",
                }
            )

        return {
            "mode": "single_worker_parallel_ready",
            "max_parallel_modules": lane_count,
            "active_lanes": active_count,
            "ready_count": len(task_queue.ready),
            "blocked_count": len(blocked_modules),
            "overflow_ready": max(0, len(task_queue.ready) - max(lane_count - active_count, 0)),
            "blocked_modules": blocked_modules,
            "lanes": lanes,
        }

    def _sync_task_records(self, state: OrchestratorState) -> None:
        tasks = parse_tasks(self.paths.tasks_file)
        for task_report in self._build_task_reports(tasks, state):
            self.handoff.save_task_record(task_report["task_id"], task_report)

    @staticmethod
    def _select_task_window(task_reports: list[dict], state: OrchestratorState) -> list[dict]:
        if not task_reports:
            return []

        current_index = None
        if state.current_task_id:
            for index, task in enumerate(task_reports):
                if task["task_id"] == state.current_task_id:
                    current_index = index
                    break

        if current_index is not None:
            start = max(0, current_index - 1)
            end = min(len(task_reports), current_index + 4)
            return task_reports[start:end]

        last_done_index = None
        for index, task in enumerate(task_reports):
            if task["task_status"] == "done":
                last_done_index = index

        if last_done_index is None:
            return task_reports[:4]

        if state.phase == Phase.COMPLETED.value:
            return task_reports[max(0, last_done_index - 1) : last_done_index + 1]

        start = last_done_index
        end = min(len(task_reports), start + 4)
        return task_reports[start:end]

    @staticmethod
    def _task_lifecycle_status(
        task: TaskItem,
        state: OrchestratorState,
        *,
        ready_task_ids: set[str],
        blocked_modules: dict[str, list[str]],
    ) -> str:
        if task.done or task.task_id in state.completed_task_ids:
            return "done"
        if state.current_task_id == task.task_id:
            return "active"
        if task.task_id in state.failed_task_ids:
            return "failed"
        if task.task_id in ready_task_ids:
            return "queued"
        if task.module_id in blocked_modules:
            return "blocked"
        return "pending"

    @staticmethod
    def _module_lifecycle_status(
        tasks: list[TaskItem],
        state: OrchestratorState,
        *,
        blocked_modules: dict[str, list[str]],
    ) -> str:
        task_ids = {task.task_id for task in tasks}
        module_id = tasks[0].module_id
        if all(task.done or task.task_id in state.completed_task_ids for task in tasks):
            return "done"
        if state.current_task_id in task_ids:
            return "active"
        if any(task.task_id in state.failed_task_ids for task in tasks):
            return "failed"
        if module_id in blocked_modules:
            return "blocked"
        if any(task.done or task.task_id in state.completed_task_ids for task in tasks):
            return "in_progress"
        return "pending"

    @staticmethod
    def _build_progress_summary(
        task_reports: list[dict],
        state: OrchestratorState,
        failed_tasks: list[dict],
    ) -> dict:
        total_tasks = len(task_reports)
        completed_tasks = len([task for task in task_reports if task["task_status"] == "done"])
        module_ids = {task["module_id"] for task in task_reports}
        completed_modules = len(
            {
                task["module_id"]
                for task in task_reports
                if task["module_status"] == "done"
            }
        )
        percent = round((completed_tasks / total_tasks) * 100, 1) if total_tasks else 0.0
        current_task = next(
            (task for task in task_reports if task["task_id"] == state.current_task_id),
            None,
        )
        current_task_elapsed_seconds = 0
        if state.current_task_started_at:
            try:
                started_at = datetime.fromisoformat(state.current_task_started_at)
                current_task_elapsed_seconds = max(
                    0,
                    int((datetime.now().astimezone() - started_at).total_seconds()),
                )
            except ValueError:
                current_task_elapsed_seconds = 0
        return {
            "total_modules": len(module_ids),
            "completed_modules": completed_modules,
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "failed_tasks": len(failed_tasks),
            "failed_task_ids": [task["task_id"] for task in failed_tasks],
            "failed_modules": len({task["module_id"] for task in failed_tasks}),
            "progress_percent": percent,
            "current_task_elapsed_seconds": current_task_elapsed_seconds,
            "current_module_id": current_task["module_id"] if current_task else None,
            "current_module_title": current_task["module_title"] if current_task else None,
            "current_module_index": current_task["module_index"] if current_task else None,
            "current_module_total": current_task["module_total"] if current_task else None,
            "current_module_done_count": current_task["module_done_count"] if current_task else None,
        }

    def _build_failed_tasks(self, task_reports: list[dict], state: OrchestratorState) -> list[dict]:
        failed_ids = set(state.failed_task_ids)
        failed: list[dict] = []
        for task in task_reports:
            if task["task_status"] != "failed" and task["task_id"] not in failed_ids:
                continue
            failed.append(
                {
                    "task_id": task["task_id"],
                    "title": task["title"],
                    "module_id": task["module_id"],
                    "module_title": task["module_title"],
                    "builder_status": task["builder_status"],
                    "builder_summary": task["builder_summary"],
                    "verifier_status": task["verifier_status"],
                    "verifier_summary": task["verifier_summary"],
                    "last_error": state.last_error if state.current_task_id == task["task_id"] else None,
                }
            )
        return failed

    def _sync_failed_tasks(self, failed_tasks: list[dict], state: OrchestratorState) -> None:
        payload = {
            "run_id": state.run_id,
            "updated_at": state.updated_at,
            "failed_count": len(failed_tasks),
            "task_ids": [task["task_id"] for task in failed_tasks],
            "tasks": failed_tasks,
        }
        self.handoff.save_failed_tasks(payload)

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

    def _resume_aborted_state(self, state: OrchestratorState) -> OrchestratorState:
        tasks = parse_tasks(self.paths.tasks_file)
        if state.current_task_id:
            current = next((task for task in tasks if task.task_id == state.current_task_id), None)
            if current and not current.done:
                state.phase = Phase.BUILDER_RUNNING.value
                state.builder_attempt = 0
                state.verifier_attempt = 0
                state.last_error = None
                state.current_task_title = current.title
                state.current_task_started_at = state.current_task_started_at or state.updated_at
                if state.current_task_id in state.failed_task_ids:
                    state.failed_task_ids = [
                        task_id for task_id in state.failed_task_ids if task_id != state.current_task_id
                    ]
                self.store.save(state)
                self.logger.log(f"resume task_id={state.current_task_id} from_aborted=true")
                return state

        if any(not task.done for task in tasks):
            state.phase = Phase.SELECT_TASK.value
            state.last_error = None
            self.store.save(state)
            self.logger.log("resume task_id=None from_aborted=true")
            return state
        return state

    def _apply_stop_request(self, state: OrchestratorState) -> None:
        stop_request = self._load_stop_request()
        if not stop_request:
            return
        state.phase = Phase.ABORTED.value
        state.last_error = str(stop_request.get("reason") or "Stopped by user.")
        self.store.save(state)
        self.clear_stop_request()
        raise AdapterError(state.last_error)

    def _load_stop_request(self) -> dict | None:
        if not self.paths.control_file.exists():
            return None
        try:
            return json.loads(self.paths.control_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"action": "stop", "reason": "Stopped by user."}

    def _select_task(self, state: OrchestratorState) -> OrchestratorState:
        tasks = parse_tasks(self.paths.tasks_file)
        task_queue = build_task_queue(tasks)
        task = select_next_task(tasks)
        if task is None:
            if any(not item.done for item in tasks):
                blocked = ", ".join(
                    f"{module_id} <- {', '.join(dependencies)}"
                    for module_id, dependencies in task_queue.blocked_modules.items()
                )
                raise FinalReviewError(
                    "No ready tasks are available in the queue. "
                    f"Unmet module dependencies: {blocked or 'unknown'}"
                )
            state.phase = Phase.FINAL_REVIEW.value
            state.current_task_id = None
            state.current_task_title = None
            state.current_task_started_at = None
            state.builder_attempt = 0
            state.verifier_attempt = 0
            state.last_error = None
            self.store.save(state)
            return state

        state.phase = Phase.BUILDER_RUNNING.value
        state.current_task_id = task.task_id
        state.current_task_title = task.title
        state.current_task_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
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
            state.failed_task_ids = [item for item in state.failed_task_ids if item != task.task_id]
            state.phase = Phase.TASK_DONE.value
            state.current_task_id = None
            state.current_task_title = None
            state.current_task_started_at = None
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
            "current_task": {
                "task_id": task.task_id,
                "title": task.title,
                "module_id": task.module_id,
                "module_title": task.module_title,
            },
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

    @staticmethod
    def _scheduler_running_note(phase: str, task_report: dict) -> str:
        if phase == Phase.BUILDER_RUNNING.value:
            return "Claude is executing the current module task."
        if phase == Phase.READY_FOR_VERIFICATION.value:
            return "Builder finished. Waiting to hand off to Codex."
        if phase == Phase.VERIFIER_RUNNING.value:
            return "Codex is verifying the builder output."
        if phase == Phase.TASK_DONE.value:
            return "Persisting task completion and preparing the queue."
        if phase == Phase.FINAL_REVIEW.value:
            return "Final review is running across the full task set."
        return task_report.get("title", "Task is active in the scheduler.")

    def _verifier_docs_context(self, task: TaskItem) -> dict:
        return {
            "prd_text": self._read_optional(self.paths.prd_file),
            "tasks_text": self.paths.tasks_file.read_text(encoding="utf-8"),
            "dev_spec_text": "",
            "acceptance_spec_text": self._read_optional(self.paths.acceptance_spec_file),
            "current_task": {
                "task_id": task.task_id,
                "title": task.title,
                "module_id": task.module_id,
                "module_title": task.module_title,
            },
        }

    @staticmethod
    def _read_optional(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _require_current_task(self, state: OrchestratorState) -> TaskItem:
        if not state.current_task_id or not state.current_task_title:
            raise AdapterError("Current task is missing from state.")
        for task in parse_tasks(self.paths.tasks_file):
            if task.task_id == state.current_task_id:
                return task
        return TaskItem(
            task_id=state.current_task_id,
            title=state.current_task_title,
            done=False,
        )

    def _delete_task_outputs(self, task_id: str) -> None:
        builder_file = self.paths.builder_artifacts_dir / f"{task_id}-result.json"
        verifier_file = self.paths.verifier_artifacts_dir / f"{task_id}-review.json"
        task_record_file = self.paths.task_record_file(task_id)
        for path in [builder_file, verifier_file, task_record_file]:
            if path.exists():
                path.unlink()

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
