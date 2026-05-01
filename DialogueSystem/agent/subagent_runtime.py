"""Simple sub-agent runtime for bounded parallel delegation inside DialogueSystem."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict

try:
    from DialogueSystem.agent.agent_loader import AgentRegistry
    from DialogueSystem.security.subagent_policy import (
        build_subagent_policy,
        build_subagent_runtime_limits,
    )
except ImportError:
    from DialogueSystem.agent.agent_loader import AgentRegistry
    from security.subagent_policy import build_subagent_policy, build_subagent_runtime_limits

logger = logging.getLogger(__name__)


class SubAgentRuntime:
    """Runs isolated child agent loops in background threads."""

    TERMINAL_STATUSES = {"completed", "failed", "cancelled", "timed_out"}
    WAITING_STATUSES = {"waiting_input", "waiting_approval"}
    ACTIVE_STATUSES = {"running", "cancelling"}
    QUEUED_STATUSES = {"queued"}
    PRIORITY_ALIASES = {
        "urgent": 100,
        "high": 50,
        "normal": 0,
        "medium": 0,
        "low": -50,
        "background": -100,
    }

    def __init__(self, owner, extra_agent_dirs: list | None = None):
        self.owner = owner
        self.agent_registry = AgentRegistry(extra_dirs=extra_agent_dirs)
        self._lock = threading.RLock()
        self._tasks = OrderedDict()
        self._task_sessions = {}
        self._task_controls = {}
        self._next_task_id = 1
        self._next_group_id = 1
        self._event_callbacks = {}
        self._next_queue_sequence = 1
        self._result_cache = OrderedDict()

    def _snapshot_base_agent_messages(self, agent_type: str, task_context: dict | None = None) -> list:
        return self.owner.build_subagent_seed_messages(agent_type=agent_type, task_context=task_context)

    def _get_runtime_limits(self) -> dict:
        owner_config = getattr(self.owner, "config", {}) or {}
        subagent_config = owner_config.get("SubAgentPolicy", {}) if isinstance(owner_config, dict) else {}
        return build_subagent_runtime_limits(subagent_config)

    def _resolve_default_max_tool_calls(self, agent_type: str) -> int:
        owner_config = getattr(self.owner, "config", {}) or {}
        subagent_config = owner_config.get("SubAgentPolicy", {}) if isinstance(owner_config, dict) else {}
        resolved_policy = build_subagent_policy(
            subagent_config,
            agent_type=agent_type,
            registry=self.agent_registry,
        )
        return max(1, int(resolved_policy.get("max_tool_calls", 1) or 1))

    @staticmethod
    def _copy_jsonish(value):
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))

    def _allocate_group_id_locked(self) -> str:
        group_id = f"subagent-group-{self._next_group_id}"
        self._next_group_id += 1
        return group_id

    def _build_result_cache_key(
        self,
        *,
        task: str,
        agent_type: str,
        task_context: dict | None,
        model: str,
        max_tool_calls: int,
    ) -> str:
        canonical_payload = {
            "task": str(task or "").strip(),
            "agent_type": str(agent_type or "general").strip().lower(),
            "task_context": self._copy_jsonish(task_context or {}),
            "model": str(model or self.owner.model).strip() or self.owner.model,
            "max_tool_calls": int(max_tool_calls or 0),
        }
        canonical_text = json.dumps(canonical_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha1(canonical_text.encode("utf-8")).hexdigest()

    def _prune_result_cache_locked(self):
        runtime_limits = self._get_runtime_limits()
        ttl_seconds = float(runtime_limits.get("result_cache_ttl_seconds", 0.0) or 0.0)
        max_entries = int(runtime_limits.get("result_cache_max_entries", 0) or 0)
        now = time.time()
        expired_keys = []
        for cache_key, entry in self._result_cache.items():
            expires_at = entry.get("expires_at")
            if ttl_seconds > 0 and expires_at not in (None, ""):
                try:
                    if now >= float(expires_at):
                        expired_keys.append(cache_key)
                except (TypeError, ValueError):
                    expired_keys.append(cache_key)
        for cache_key in expired_keys:
            self._result_cache.pop(cache_key, None)
        if max_entries <= 0:
            self._result_cache.clear()
            return
        while len(self._result_cache) > max_entries:
            self._result_cache.popitem(last=False)

    def _get_cached_result_locked(self, cache_key: str) -> dict | None:
        runtime_limits = self._get_runtime_limits()
        if not runtime_limits.get("result_cache_enabled", True):
            return None
        self._prune_result_cache_locked()
        entry = self._result_cache.get(str(cache_key or "").strip())
        if entry is None:
            return None
        self._result_cache.move_to_end(str(cache_key or "").strip())
        return copy.deepcopy(entry)

    def _store_completed_result_locked(self, task_record: dict):
        runtime_limits = self._get_runtime_limits()
        if not runtime_limits.get("result_cache_enabled", True):
            return
        max_entries = int(runtime_limits.get("result_cache_max_entries", 0) or 0)
        if max_entries <= 0:
            return
        cache_key = str(task_record.get("cache_key", "") or "").strip()
        if not cache_key:
            return
        now = time.time()
        ttl_seconds = float(runtime_limits.get("result_cache_ttl_seconds", 0.0) or 0.0)
        expires_at = now + ttl_seconds if ttl_seconds > 0 else None
        self._result_cache[cache_key] = {
            "cache_key": cache_key,
            "cached_at": now,
            "expires_at": expires_at,
            "source_task_id": str(task_record.get("task_id", "") or "").strip(),
            "task": str(task_record.get("task", "") or "").strip(),
            "agent_type": str(task_record.get("agent_type", "") or "").strip(),
            "task_context": self._copy_jsonish(task_record.get("task_context") or {}),
            "model": str(task_record.get("model", "") or "").strip(),
            "max_tool_calls": int(task_record.get("max_tool_calls", 0) or 0),
            "result": str(task_record.get("result", "") or ""),
            "structured_output": self._copy_jsonish(task_record.get("structured_output") or {}),
            "tool_trace": self._copy_jsonish(task_record.get("tool_trace") or []),
        }
        self._result_cache.move_to_end(cache_key)
        self._prune_result_cache_locked()

    def _build_task_batch_summary(
        self,
        tasks: list[dict],
        *,
        requested_count: int | None = None,
        errors: list | None = None,
        wait_completed: bool = False,
        waiting_for_external_input: bool = False,
        deadline_reached: bool = False,
    ) -> dict:
        normalized_tasks = [dict(task or {}) for task in list(tasks or []) if isinstance(task, dict)]
        normalized_errors = [dict(item or {}) for item in list(errors or []) if isinstance(item, dict)]
        status_counts = {}
        group_ids = []
        group_labels = []
        terminal_task_ids = []
        waiting_task_ids = []
        active_task_ids = []
        cache_hit_count = 0

        for task in normalized_tasks:
            status = str(task.get("status", "") or "").strip().lower() or "unknown"
            status_counts[status] = int(status_counts.get(status, 0) or 0) + 1
            task_id = str(task.get("task_id", "") or "").strip()
            if task_id:
                if status in self.TERMINAL_STATUSES:
                    terminal_task_ids.append(task_id)
                elif status in self.WAITING_STATUSES:
                    waiting_task_ids.append(task_id)
                else:
                    active_task_ids.append(task_id)
            if bool(task.get("cache_hit", False)):
                cache_hit_count += 1
            group_id = str(task.get("group_id", "") or "").strip()
            if group_id and group_id not in group_ids:
                group_ids.append(group_id)
            group_label = str(task.get("group_label", "") or "").strip()
            if group_label and group_label not in group_labels:
                group_labels.append(group_label)

        unknown_task_ids = []
        for error_entry in normalized_errors:
            if str(error_entry.get("code", "") or "").strip().lower() != "unknown_task_id":
                continue
            task_id = str(error_entry.get("task_id", "") or "").strip()
            if task_id and task_id not in unknown_task_ids:
                unknown_task_ids.append(task_id)

        resolved_count = len(normalized_tasks)
        requested_total = int(requested_count if requested_count is not None else resolved_count)
        waiting_count = sum(status_counts.get(status, 0) for status in self.WAITING_STATUSES)
        terminal_count = sum(status_counts.get(status, 0) for status in self.TERMINAL_STATUSES)
        queued_count = sum(status_counts.get(status, 0) for status in self.QUEUED_STATUSES)
        running_count = sum(status_counts.get(status, 0) for status in self.ACTIVE_STATUSES)
        return {
            "requested_count": requested_total,
            "resolved_count": resolved_count,
            "error_count": len(normalized_errors),
            "unknown_task_ids": unknown_task_ids,
            "status_counts": status_counts,
            "terminal_count": terminal_count,
            "completed_count": int(status_counts.get("completed", 0) or 0),
            "failed_count": int(status_counts.get("failed", 0) or 0),
            "cancelled_count": int(status_counts.get("cancelled", 0) or 0),
            "timed_out_count": int(status_counts.get("timed_out", 0) or 0),
            "running_count": running_count,
            "queued_count": queued_count,
            "waiting_count": waiting_count,
            "cache_hit_count": cache_hit_count,
            "wait_completed": bool(wait_completed),
            "waiting_for_external_input": bool(waiting_for_external_input),
            "deadline_reached": bool(deadline_reached),
            "group_id": group_ids[0] if len(group_ids) == 1 else "",
            "group_label": group_labels[0] if len(group_labels) == 1 else "",
            "group_ids": group_ids,
            "group_labels": group_labels,
            "terminal_task_ids": terminal_task_ids,
            "waiting_task_ids": waiting_task_ids,
            "active_task_ids": active_task_ids,
        }

    def _normalize_priority(self, priority) -> int:
        default_priority = int(self._get_runtime_limits().get("default_priority", 0))
        if priority in (None, ""):
            return default_priority
        if isinstance(priority, str):
            normalized = priority.strip().lower()
            if not normalized:
                return default_priority
            if normalized in self.PRIORITY_ALIASES:
                return int(self.PRIORITY_ALIASES[normalized])
            try:
                return int(float(normalized))
            except (TypeError, ValueError):
                return default_priority
        try:
            return int(priority)
        except (TypeError, ValueError):
            return default_priority

    @staticmethod
    def _priority_label(priority: int) -> str:
        normalized_priority = int(priority or 0)
        if normalized_priority >= 75:
            return "urgent"
        if normalized_priority >= 25:
            return "high"
        if normalized_priority <= -75:
            return "background"
        if normalized_priority <= -25:
            return "low"
        return "normal"

    def _get_running_task_count_locked(self) -> int:
        return sum(
            1
            for task_record in self._tasks.values()
            if str(task_record.get("status", "")).strip().lower() in self.ACTIVE_STATUSES
        )

    def _get_queue_capacity_locked(self) -> int:
        runtime_limits = self._get_runtime_limits()
        return int(runtime_limits.get("max_queue_size", 0) or 0)

    def _get_queued_task_ids_locked(self) -> list[str]:
        queued_tasks = [
            task_record
            for task_record in self._tasks.values()
            if str(task_record.get("status", "")).strip().lower() in self.QUEUED_STATUSES
        ]
        queued_tasks.sort(
            key=lambda item: (
                -int(item.get("priority", 0) or 0),
                int(item.get("queue_sequence", 0) or 0),
                float(item.get("created_at", 0) or 0),
                str(item.get("task_id", "") or ""),
            )
        )
        return [str(task_record.get("task_id", "") or "") for task_record in queued_tasks if str(task_record.get("task_id", "") or "").strip()]

    def _refresh_queue_positions_locked(self):
        queued_task_ids = self._get_queued_task_ids_locked()
        queued_id_set = set(queued_task_ids)
        for index, task_id in enumerate(queued_task_ids, start=1):
            task_record = self._tasks.get(task_id)
            if task_record is None:
                continue
            task_record["queue_position"] = index
        for task_record in self._tasks.values():
            task_id = str(task_record.get("task_id", "") or "")
            if task_id not in queued_id_set:
                task_record["queue_position"] = None
                if str(task_record.get("status", "")).strip().lower() not in self.QUEUED_STATUSES:
                    task_record["queue_sequence"] = 0
                    task_record["queued_at"] = None
                    task_record["queue_reason"] = ""

    def _queue_task_locked(
        self,
        task_id: str,
        *,
        mode: str,
        user_reply: str = "",
        approval_decision: str = "",
        reason: str = "",
    ):
        task_record = self._tasks.get(task_id)
        if task_record is None:
            return
        control = self._task_controls.get(task_id)
        if control is None:
            return
        now = time.time()
        queue_reason = str(reason or "Queued waiting for an available worker slot.").strip()
        task_record["status"] = "queued"
        task_record["updated_at"] = now
        task_record["finished_at"] = None
        task_record["result"] = ""
        task_record["error"] = ""
        task_record["awaiting"] = {}
        task_record["status_message"] = queue_reason
        task_record["queue_reason"] = queue_reason
        task_record["queued_at"] = now
        task_record["queue_sequence"] = self._next_queue_sequence
        self._next_queue_sequence += 1
        control["pending_dispatch"] = {
            "mode": str(mode or "start").strip() or "start",
            "user_reply": str(user_reply or "").strip(),
            "approval_decision": str(approval_decision or "").strip().lower(),
        }
        control["worker"] = None
        self._refresh_queue_positions_locked()

    def _expire_overdue_nonrunning_tasks_locked(self):
        now = time.time()
        expired_snapshots = []
        for task_id, task_record in list(self._tasks.items()):
            status = str(task_record.get("status", "")).strip().lower()
            if status in self.TERMINAL_STATUSES or status in self.ACTIVE_STATUSES:
                continue
            deadline_at = task_record.get("deadline_at")
            if deadline_at in (None, ""):
                continue
            try:
                overdue = now >= float(deadline_at)
            except (TypeError, ValueError):
                overdue = False
            if not overdue:
                continue
            control = self._task_controls.pop(task_id, {}) or {}
            task_record["status"] = "timed_out"
            task_record["updated_at"] = now
            task_record["finished_at"] = now
            task_record["error"] = str(
                control.get("timeout_message")
                or f"Delegated task {task_id} exceeded its timeout budget."
            )
            task_record["result"] = ""
            task_record["awaiting"] = {}
            task_record["status_message"] = task_record["error"]
            task_record["cancel_requested"] = False
            task_record["cancel_reason"] = ""
            task_record["queue_reason"] = ""
            task_record["queued_at"] = None
            task_record["queue_sequence"] = 0
            task_record["queue_position"] = None
            task_record["stats"] = {
                "tool_calls": len(task_record.get("tool_trace") or []),
                "duration_seconds": now - float(task_record.get("created_at") or now),
            }
            self._task_sessions.pop(task_id, None)
            expired_snapshots.append((task_id, copy.deepcopy(task_record)))
        if expired_snapshots:
            self._refresh_queue_positions_locked()
        return expired_snapshots

    def _pump_queue(self):
        expired_snapshots = []
        failed_snapshots = []
        start_specs = []
        with self._lock:
            expired_snapshots = self._expire_overdue_nonrunning_tasks_locked()
            runtime_limits = self._get_runtime_limits()
            max_concurrent_tasks = int(runtime_limits.get("max_concurrent_tasks", 1) or 1)
            while self._get_running_task_count_locked() < max_concurrent_tasks:
                queued_task_ids = self._get_queued_task_ids_locked()
                if not queued_task_ids:
                    break
                task_id = queued_task_ids[0]
                task_record = self._tasks.get(task_id)
                control = self._task_controls.get(task_id)
                pending_dispatch = dict((control or {}).get("pending_dispatch") or {})
                if task_record is None or control is None or not pending_dispatch:
                    if task_record is not None:
                        task_record["status"] = "failed"
                        task_record["updated_at"] = time.time()
                        task_record["finished_at"] = task_record["updated_at"]
                        task_record["error"] = "Queued delegated task lost its dispatch payload."
                        task_record["status_message"] = task_record["error"]
                        task_record["queue_reason"] = ""
                        task_record["queued_at"] = None
                        task_record["queue_sequence"] = 0
                        task_record["queue_position"] = None
                        task_record["stats"] = {
                            "tool_calls": len(task_record.get("tool_trace") or []),
                            "duration_seconds": task_record["finished_at"] - task_record["created_at"],
                        }
                        failed_snapshots.append((task_id, copy.deepcopy(task_record)))
                    self._task_controls.pop(task_id, None)
                    self._task_sessions.pop(task_id, None)
                    self._refresh_queue_positions_locked()
                    continue
                task_record["status"] = "running"
                task_record["updated_at"] = time.time()
                task_record["status_message"] = "Worker slot acquired."
                task_record["queue_reason"] = ""
                task_record["queued_at"] = None
                task_record["queue_sequence"] = 0
                task_record["queue_position"] = None
                control["pending_dispatch"] = None
                start_specs.append(
                    {
                        "task_id": task_id,
                        "mode": str(pending_dispatch.get("mode", "start") or "start"),
                        "user_reply": str(pending_dispatch.get("user_reply", "") or ""),
                        "approval_decision": str(pending_dispatch.get("approval_decision", "") or ""),
                    }
                )
                self._refresh_queue_positions_locked()

        for expired_task_id, task_snapshot in expired_snapshots:
            self._notify_task_callbacks(expired_task_id, task_snapshot)
        for failed_task_id, task_snapshot in failed_snapshots:
            self._notify_task_callbacks(failed_task_id, task_snapshot)
        for start_spec in start_specs:
            self._start_task_worker(**start_spec)

    def spawn(
        self,
        *,
        task: str,
        agent_type: str = "general",
        task_context: dict | None = None,
        model: str = "",
        max_tool_calls: int | None = None,
        timeout_seconds: float = 60.0,
        run_in_background: bool = True,
        priority=None,
        use_cache: bool = True,
        group_id: str = "",
        group_index=None,
        group_size=None,
        group_label: str = "",
    ) -> dict:
        self._pump_queue()
        normalized_task = str(task or "").strip()
        if not normalized_task:
            raise ValueError("Task is required.")

        normalized_agent_type = str(agent_type or "general").strip().lower()
        agent_def = self.agent_registry.get(normalized_agent_type)
        default_max_tool_calls = self._resolve_default_max_tool_calls(normalized_agent_type)
        normalized_model = str(model or self.owner.model).strip() or self.owner.model
        resolved_max_tool_calls = int(max_tool_calls or default_max_tool_calls)
        normalized_priority = self._normalize_priority(priority)
        normalized_group_id = str(group_id or "").strip()
        normalized_group_label = str(group_label or "").strip()
        try:
            normalized_group_index = int(group_index) if group_index not in (None, "") else None
        except (TypeError, ValueError):
            normalized_group_index = None
        try:
            normalized_group_size = int(group_size) if group_size not in (None, "") else None
        except (TypeError, ValueError):
            normalized_group_size = None
        start_spec = None

        with self._lock:
            cache_key = self._build_result_cache_key(
                task=normalized_task,
                agent_type=normalized_agent_type,
                task_context=task_context,
                model=normalized_model,
                max_tool_calls=resolved_max_tool_calls,
            )
            runtime_limits = self._get_runtime_limits()
            cached_entry = self._get_cached_result_locked(cache_key) if bool(use_cache) else None
            if cached_entry is not None:
                task_id = f"subagent-{self._next_task_id}"
                self._next_task_id += 1
                now = time.time()
                cached_at = cached_entry.get("cached_at")
                try:
                    cache_age_seconds = max(0.0, now - float(cached_at))
                except (TypeError, ValueError):
                    cache_age_seconds = 0.0
                task_record = {
                    "task_id": task_id,
                    "task": normalized_task,
                    "agent_type": agent_def["name"],
                    "task_context": dict(task_context or {}),
                    "model": normalized_model,
                    "status": "completed",
                    "created_at": now,
                    "updated_at": now,
                    "finished_at": now,
                    "result": str(cached_entry.get("result", "") or ""),
                    "error": "",
                    "tool_trace": list(cached_entry.get("tool_trace") or []),
                    "structured_output": dict(cached_entry.get("structured_output") or {}),
                    "max_tool_calls": resolved_max_tool_calls,
                    "timeout_seconds": float(timeout_seconds or 60.0),
                    "run_in_background": bool(run_in_background),
                    "deadline_at": None,
                    "awaiting": {},
                    "resume_count": 0,
                    "cancel_requested": False,
                    "cancel_reason": "",
                    "status_message": "Result served from cache.",
                    "priority": normalized_priority,
                    "priority_label": self._priority_label(normalized_priority),
                    "queue_position": None,
                    "queue_reason": "",
                    "queued_at": None,
                    "queue_sequence": 0,
                    "stats": {
                        "tool_calls": len(cached_entry.get("tool_trace") or []),
                        "duration_seconds": 0.0,
                    },
                    "cache_key": cache_key,
                    "cache_hit": True,
                    "cache_source_task_id": str(cached_entry.get("source_task_id", "") or "").strip(),
                    "cache_created_at": cached_entry.get("cached_at"),
                    "cache_expires_at": cached_entry.get("expires_at"),
                    "cache_age_seconds": cache_age_seconds,
                    "group_id": normalized_group_id,
                    "group_index": normalized_group_index,
                    "group_size": normalized_group_size,
                    "group_label": normalized_group_label,
                }
                self._tasks[task_id] = task_record
                return {"ok": True, "task": copy.deepcopy(task_record)}
            max_concurrent_tasks = int(runtime_limits.get("max_concurrent_tasks", 1) or 1)
            max_queue_size = int(runtime_limits.get("max_queue_size", 0) or 0)
            running_task_count = self._get_running_task_count_locked()
            queued_task_count = len(self._get_queued_task_ids_locked())
            if running_task_count >= max_concurrent_tasks and max_queue_size > 0 and queued_task_count >= max_queue_size:
                return {
                    "ok": False,
                    "error": "Delegated task queue is full.",
                }
            task_id = f"subagent-{self._next_task_id}"
            self._next_task_id += 1
            created_at = time.time()
            task_session = self.owner.create_detached_agent_session(
                session_name=task_id,
                user_input=normalized_task,
                agent_type=agent_def["name"],
            )
            task_record = {
                "task_id": task_id,
                "task": normalized_task,
                "agent_type": agent_def["name"],
                "task_context": dict(task_context or {}),
                "model": normalized_model,
                "status": "running",
                "created_at": created_at,
                "updated_at": created_at,
                "finished_at": None,
                "result": "",
                "error": "",
                "tool_trace": [],
                "structured_output": {},
                "max_tool_calls": resolved_max_tool_calls,
                "timeout_seconds": float(timeout_seconds or 60.0),
                "run_in_background": bool(run_in_background),
                "deadline_at": created_at + float(timeout_seconds or 60.0) if float(timeout_seconds or 60.0) > 0 else None,
                "awaiting": {},
                "resume_count": 0,
                "cancel_requested": False,
                "cancel_reason": "",
                "status_message": "",
                "priority": normalized_priority,
                "priority_label": self._priority_label(normalized_priority),
                "queue_position": None,
                "queue_reason": "",
                "queued_at": None,
                "queue_sequence": 0,
                "cache_key": cache_key,
                "cache_hit": False,
                "cache_source_task_id": "",
                "cache_created_at": None,
                "cache_expires_at": None,
                "cache_age_seconds": None,
                "group_id": normalized_group_id,
                "group_index": normalized_group_index,
                "group_size": normalized_group_size,
                "group_label": normalized_group_label,
            }
            self._tasks[task_id] = task_record
            self._task_sessions[task_id] = task_session
            self._task_controls[task_id] = {
                "cancel_event": threading.Event(),
                "deadline_at": task_record["deadline_at"],
                "timeout_message": f"Delegated task {task_id} exceeded its timeout budget.",
                "cancel_reason": "",
                "worker": None,
                "pending_dispatch": None,
            }
            if running_task_count < max_concurrent_tasks:
                start_spec = {
                    "task_id": task_id,
                    "mode": "start",
                    "user_reply": "",
                    "approval_decision": "",
                }
            else:
                self._queue_task_locked(
                    task_id,
                    mode="start",
                    reason="Queued waiting for an available worker slot.",
                )

        if start_spec is not None:
            self._start_task_worker(**start_spec)

        if not run_in_background:
            return self.wait_for_task(task_id, timeout_seconds=timeout_seconds)

        return self.get_status(task_id)

    def spawn_parallel(
        self,
        *,
        task_specs: list[dict],
        group_label: str = "",
        wait_for_completion: bool = False,
        timeout_seconds: float = 20.0,
        poll_interval_seconds: float = 0.5,
    ) -> dict:
        normalized_specs = []
        for index, raw_spec in enumerate(list(task_specs or []), start=1):
            if not isinstance(raw_spec, dict):
                return {"ok": False, "error": f"Tasks[{index}] must be an object."}
            normalized_task = str(raw_spec.get("task", raw_spec.get("Task", "")) or "").strip()
            if not normalized_task:
                return {"ok": False, "error": f"Tasks[{index}].Task is required."}
            normalized_specs.append(
                {
                    "task": normalized_task,
                    "agent_type": raw_spec.get("agent_type", raw_spec.get("AgentType", "general")),
                    "task_context": raw_spec.get("task_context", raw_spec.get("Context")),
                    "model": raw_spec.get("model", raw_spec.get("Model", "")),
                    "max_tool_calls": raw_spec.get("max_tool_calls", raw_spec.get("MaxToolCalls")),
                    "timeout_seconds": raw_spec.get("timeout_seconds", raw_spec.get("TimeoutSeconds", 60.0)),
                    "priority": raw_spec.get("priority", raw_spec.get("Priority")),
                    "use_cache": raw_spec.get("use_cache", raw_spec.get("UseCache", True)),
                }
            )
        if not normalized_specs:
            return {"ok": False, "error": "Tasks is required."}

        normalized_group_label = str(group_label or "").strip()
        with self._lock:
            group_id = self._allocate_group_id_locked()
        created_at = time.time()
        requested_count = len(normalized_specs)
        created_tasks = []
        errors = []
        for index, task_spec in enumerate(normalized_specs, start=1):
            response = self.spawn(
                task=task_spec["task"],
                agent_type=task_spec["agent_type"],
                task_context=task_spec["task_context"],
                model=task_spec["model"],
                max_tool_calls=task_spec["max_tool_calls"],
                timeout_seconds=task_spec["timeout_seconds"],
                run_in_background=True,
                priority=task_spec["priority"],
                use_cache=task_spec["use_cache"],
                group_id=group_id,
                group_index=index,
                group_size=requested_count,
                group_label=normalized_group_label,
            )
            if response.get("ok", False) and isinstance(response.get("task"), dict):
                created_tasks.append(dict(response.get("task") or {}))
                continue
            errors.append(
                {
                    "index": index,
                    "task": task_spec["task"],
                    "code": "spawn_failed",
                    "error": str(response.get("error") or f"Failed to create delegated task {index}."),
                }
            )

        tasks_snapshot = list(created_tasks)
        if wait_for_completion and tasks_snapshot:
            wait_result = self.wait_for_tasks(
                [str(task.get("task_id", "") or "").strip() for task in tasks_snapshot],
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
            if isinstance(wait_result.get("tasks"), list):
                tasks_snapshot = list(wait_result.get("tasks") or [])

        statuses = [str(task.get("status", "") or "").strip().lower() for task in tasks_snapshot if isinstance(task, dict)]
        any_waiting = any(status in self.WAITING_STATUSES for status in statuses)
        all_terminal = bool(tasks_snapshot) and len(tasks_snapshot) == requested_count and all(
            status in self.TERMINAL_STATUSES for status in statuses
        )
        summary = self._build_task_batch_summary(
            tasks_snapshot,
            requested_count=requested_count,
            errors=errors,
            wait_completed=(len(errors) == 0 and all_terminal),
            waiting_for_external_input=any_waiting,
            deadline_reached=False,
        )
        group_payload = {
            "group_id": group_id,
            "group_label": normalized_group_label,
            "requested_count": requested_count,
            "created_count": len(tasks_snapshot),
            "error_count": len(errors),
            "created_at": created_at,
        }
        response = {
            "ok": len(tasks_snapshot) > 0,
            "count": len(tasks_snapshot),
            "tasks": tasks_snapshot,
            "group": group_payload,
            "summary": summary,
            "errors": errors,
            "partial_failure": bool(errors),
        }
        if errors:
            if tasks_snapshot:
                response["error"] = f"Created {len(tasks_snapshot)} of {requested_count} delegated tasks."
            else:
                response["error"] = str(errors[0].get("error") or "Failed to create delegated tasks.")
        return response

    def _start_task_worker(self, task_id: str, *, mode: str, user_reply: str = "", approval_decision: str = ""):
        worker = threading.Thread(
            target=self._run_subagent_task,
            args=(task_id, mode, user_reply, approval_decision),
            name=f"dialogue-subagent-{task_id}-{mode}",
            daemon=True,
        )
        with self._lock:
            control = self._task_controls.get(task_id)
            if isinstance(control, dict):
                control["worker"] = worker
        worker.start()
        return worker

    def _run_subagent_task(self, task_id: str, mode: str = "start", user_reply: str = "", approval_decision: str = ""):
        try:
            with self._lock:
                task_record = self._tasks.get(task_id)
                if task_record is None:
                    return
                task_prompt = task_record["task"]
                agent_type_str = task_record["agent_type"]
                task_context = task_record["task_context"]
                model = task_record["model"]
                max_tool_calls = task_record["max_tool_calls"]
                task_session = self._task_sessions.get(task_id)
                task_control = self._task_controls.get(task_id) or {}

            if mode == "resume":
                result = self.owner.resume_detached_agent(
                    model=model,
                    agent_session=task_session,
                    max_tool_calls=max_tool_calls,
                    agent_type=agent_type_str,
                    user_reply=user_reply,
                    approval_decision=approval_decision,
                    task_context=task_context,
                    task_control=task_control,
                )
            else:
                messages = self._snapshot_base_agent_messages(agent_type_str, task_context)
                instruction = self.agent_registry.get_instruction(agent_type_str)
                messages.append(
                    {
                        "role": "system",
                        "content": instruction,
                    }
                )
                messages.append({"role": "user", "content": task_prompt})
                result = self.owner.run_agent_detached(
                    model=model,
                    base_messages=messages,
                    max_tool_calls=max_tool_calls,
                    agent_type=agent_type_str,
                    agent_session=task_session,
                    task_input=task_prompt,
                    task_context=task_context,
                    task_control=task_control,
                )
            self._apply_task_result(task_id, result, mode=mode)
        except Exception as error:
            logger.exception("Sub-agent failed | task_id=%s", task_id)
            self._complete_task(task_id, status="failed", error=str(error))

    def _complete_task(
        self,
        task_id: str,
        *,
        status: str,
        result: str = "",
        error: str = "",
        tool_trace=None,
        structured_output: dict | None = None
    ):
        task_snapshot = None
        with self._lock:
            task_record = self._tasks.get(task_id)
            if task_record is None:
                return
            task_record["status"] = status
            task_record["updated_at"] = time.time()
            task_record["finished_at"] = task_record["updated_at"]
            task_record["result"] = str(result or "")
            task_record["error"] = str(error or "")
            task_record["tool_trace"] = list(tool_trace or [])
            task_record["structured_output"] = dict(structured_output or {})
            task_record["stats"] = {
                "tool_calls": len(tool_trace or []),
                "duration_seconds": task_record["finished_at"] - task_record["created_at"],
            }
            task_record["awaiting"] = {}
            task_record["status_message"] = task_record["error"] or task_record["result"]
            task_record["cancel_requested"] = False
            task_record["cancel_reason"] = ""
            task_record["queue_reason"] = ""
            task_record["queued_at"] = None
            task_record["queue_sequence"] = 0
            task_record["queue_position"] = None
            task_record["cache_age_seconds"] = None
            task_record["cache_hit"] = False
            self._task_sessions.pop(task_id, None)
            self._task_controls.pop(task_id, None)
            if status == "completed":
                self._store_completed_result_locked(task_record)
            task_snapshot = copy.deepcopy(task_record)

        self._notify_task_callbacks(task_id, task_snapshot or {})
        self._pump_queue()

    def _notify_task_callbacks(self, task_id: str, task_record: dict):
        with self._lock:
            callbacks = list(self._event_callbacks.pop(task_id, []))
        for callback in callbacks:
            try:
                callback(task_id, task_record)
            except Exception:
                logger.exception("Task callback failed | task_id=%s", task_id)

    def _set_task_waiting(self, task_id: str, *, status: str, awaiting: dict | None = None, tool_trace=None, structured_output: dict | None = None):
        with self._lock:
            task_record = self._tasks.get(task_id)
            if task_record is None:
                return
            task_record["status"] = status
            task_record["updated_at"] = time.time()
            task_record["result"] = ""
            task_record["error"] = ""
            task_record["tool_trace"] = list(tool_trace or [])
            task_record["structured_output"] = dict(structured_output or {})
            task_record["awaiting"] = dict(awaiting or {})
            task_record["status_message"] = str(task_record["awaiting"].get("question", "") or "").strip()
            task_record["queue_reason"] = ""
            task_record["queued_at"] = None
            task_record["queue_sequence"] = 0
            task_record["queue_position"] = None
        self._pump_queue()

    def _apply_task_result(self, task_id: str, result: dict | None, *, mode: str):
        result = dict(result or {})
        lifecycle = str(result.get("lifecycle", "") or "").strip().lower()
        tool_trace = list(result.get("tool_trace") or [])
        structured_output = dict(result.get("structured_output") or {})
        if lifecycle in self.WAITING_STATUSES:
            self._set_task_waiting(
                task_id,
                status=lifecycle,
                awaiting=result.get("awaiting") or {},
                tool_trace=tool_trace,
                structured_output=structured_output,
            )
            return
        if lifecycle == "cancelled":
            self._complete_task(
                task_id,
                status="cancelled",
                error=str(result.get("error") or "Delegated task was cancelled."),
                tool_trace=tool_trace,
                structured_output=structured_output,
            )
            return
        if lifecycle == "timed_out":
            self._complete_task(
                task_id,
                status="timed_out",
                error=str(result.get("error") or "Delegated task timed out."),
                tool_trace=tool_trace,
                structured_output=structured_output,
            )
            return
        if lifecycle == "failed" or (not result.get("ok", True) and not lifecycle):
            self._complete_task(
                task_id,
                status="failed",
                error=str(result.get("error") or "Delegated task failed."),
                tool_trace=tool_trace,
                structured_output=structured_output,
            )
            return
        self._complete_task(
            task_id,
            status="completed",
            result=str(result.get("final_text", "") or "").strip(),
            tool_trace=tool_trace,
            structured_output=structured_output,
        )

    def on_task_complete(self, task_id: str, callback):
        """Register a callback to be invoked when task completes."""
        with self._lock:
            if task_id not in self._event_callbacks:
                self._event_callbacks[task_id] = []
            self._event_callbacks[task_id].append(callback)

    def get_status(self, task_id: str) -> dict:
        self._pump_queue()
        with self._lock:
            task_record = self._tasks.get(str(task_id or "").strip())
            if task_record is None:
                return {"ok": False, "error": f"Unknown sub-agent task: {task_id}"}
            return {"ok": True, "task": copy.deepcopy(task_record)}

    def wait_for_task(self, task_id: str, *, timeout_seconds: float = 20.0, poll_interval_seconds: float = 0.5) -> dict:
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            return {"ok": False, "error": "TaskId is required."}
        normalized_timeout = max(0.0, float(timeout_seconds or 0.0))
        normalized_poll_interval = min(max(0.1, float(poll_interval_seconds or 0.5)), 2.0)
        deadline = time.time() + normalized_timeout
        while True:
            self._pump_queue()
            status = self.get_status(normalized_task_id)
            if not status.get("ok", False):
                return status
            task = status.get("task") or {}
            if str(task.get("status", "")).strip().lower() in self.TERMINAL_STATUSES:
                task["wait_completed"] = True
                return {"ok": True, "task": task}
            if str(task.get("status", "")).strip().lower() in self.WAITING_STATUSES:
                task["wait_completed"] = False
                task["waiting_for_external_input"] = True
                return {"ok": True, "task": task}
            if normalized_timeout <= 0 or time.time() >= deadline:
                task["wait_completed"] = False
                return {"ok": True, "task": task}
            time.sleep(normalized_poll_interval)

    def wait_for_tasks(self, task_ids: list[str], *, timeout_seconds: float = 20.0, poll_interval_seconds: float = 0.5) -> dict:
        normalized_task_ids = []
        seen_task_ids = set()
        for raw_task_id in list(task_ids or []):
            task_id = str(raw_task_id or "").strip()
            if not task_id or task_id in seen_task_ids:
                continue
            normalized_task_ids.append(task_id)
            seen_task_ids.add(task_id)
        if not normalized_task_ids:
            return {"ok": False, "error": "TaskIds is required."}

        normalized_timeout = max(0.0, float(timeout_seconds or 0.0))
        normalized_poll_interval = min(max(0.1, float(poll_interval_seconds or 0.5)), 2.0)
        deadline = time.time() + normalized_timeout
        requested_count = len(normalized_task_ids)
        while True:
            self._pump_queue()
            tasks = []
            errors = []
            with self._lock:
                for task_id in normalized_task_ids:
                    task_record = self._tasks.get(task_id)
                    if task_record is None:
                        errors.append(
                            {
                                "task_id": task_id,
                                "code": "unknown_task_id",
                                "error": f"Unknown sub-agent task: {task_id}",
                            }
                        )
                        continue
                    tasks.append(copy.deepcopy(task_record))

            statuses = [str(task.get("status", "") or "").strip().lower() for task in tasks if isinstance(task, dict)]
            any_waiting = any(status in self.WAITING_STATUSES for status in statuses)
            all_terminal = bool(tasks) and len(tasks) == requested_count and all(
                status in self.TERMINAL_STATUSES for status in statuses
            )
            deadline_reached = normalized_timeout <= 0 or time.time() >= deadline
            summary = self._build_task_batch_summary(
                tasks,
                requested_count=requested_count,
                errors=errors,
                wait_completed=(len(errors) == 0 and all_terminal),
                waiting_for_external_input=any_waiting,
                deadline_reached=(not all_terminal and not any_waiting and deadline_reached),
            )
            payload = {
                "ok": len(errors) == 0,
                "count": len(tasks),
                "tasks": tasks,
                "summary": summary,
                "errors": errors,
            }
            if errors:
                payload["error"] = str(errors[0].get("error") or "Unknown sub-agent task.")
                return payload
            if any_waiting or all_terminal or deadline_reached:
                return payload
            time.sleep(normalized_poll_interval)

    def list_tasks(self, *, include_completed: bool = True) -> dict:
        self._pump_queue()
        with self._lock:
            tasks = []
            for task_record in self._tasks.values():
                if not include_completed and task_record["status"] in self.TERMINAL_STATUSES:
                    continue
                tasks.append(copy.deepcopy(task_record))
        return {"ok": True, "count": len(tasks), "tasks": tasks}

    def resume_task(self, task_id: str, *, user_reply: str = "", approval_decision: str = "") -> dict:
        self._pump_queue()
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            return {"ok": False, "error": "TaskId is required."}
        normalized_user_reply = str(user_reply or "").strip()
        normalized_approval_decision = str(approval_decision or "").strip().lower()
        start_spec = None
        with self._lock:
            task_record = self._tasks.get(normalized_task_id)
            if task_record is None:
                return {"ok": False, "error": f"Unknown sub-agent task: {task_id}"}
            status = str(task_record.get("status", "")).strip().lower()
            if status not in self.WAITING_STATUSES:
                return {"ok": False, "error": f"Task {normalized_task_id} is not waiting for input."}
            if status == "waiting_input" and not normalized_user_reply:
                return {"ok": False, "error": "UserReply is required for waiting_input tasks."}
            if status == "waiting_approval" and not normalized_user_reply and normalized_approval_decision not in {"approved", "rejected"}:
                return {"ok": False, "error": "Provide UserReply or ApprovalDecision for waiting_approval tasks."}
            runtime_limits = self._get_runtime_limits()
            max_concurrent_tasks = int(runtime_limits.get("max_concurrent_tasks", 1) or 1)
            max_queue_size = int(runtime_limits.get("max_queue_size", 0) or 0)
            queued_task_count = len(self._get_queued_task_ids_locked())
            running_task_count = self._get_running_task_count_locked()
            if running_task_count >= max_concurrent_tasks and max_queue_size > 0 and queued_task_count >= max_queue_size:
                return {"ok": False, "error": "Delegated task queue is full."}
            task_record["updated_at"] = time.time()
            task_record["resume_count"] = int(task_record.get("resume_count") or 0) + 1
            if running_task_count < max_concurrent_tasks:
                task_record["status"] = "running"
                task_record["awaiting"] = {}
                task_record["status_message"] = ""
                task_record["queue_reason"] = ""
                task_record["queued_at"] = None
                task_record["queue_sequence"] = 0
                task_record["queue_position"] = None
                start_spec = {
                    "task_id": normalized_task_id,
                    "mode": "resume",
                    "user_reply": normalized_user_reply,
                    "approval_decision": normalized_approval_decision,
                }
            else:
                self._queue_task_locked(
                    normalized_task_id,
                    mode="resume",
                    user_reply=normalized_user_reply,
                    approval_decision=normalized_approval_decision,
                    reason="Queued waiting to resume on an available worker slot.",
                )
        if start_spec is not None:
            self._start_task_worker(**start_spec)
        return self.get_status(normalized_task_id)

    def cancel_task(self, task_id: str, *, reason: str = "") -> dict:
        self._pump_queue()
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            return {"ok": False, "error": "TaskId is required."}
        normalized_reason = str(reason or "").strip() or "Cancelled by user."
        callback_snapshot = None
        with self._lock:
            task_record = self._tasks.get(normalized_task_id)
            if task_record is None:
                return {"ok": False, "error": f"Unknown sub-agent task: {task_id}"}
            status = str(task_record.get("status", "")).strip().lower()
            if status in self.TERMINAL_STATUSES:
                return {"ok": False, "error": f"Task {normalized_task_id} is already finished."}
            task_record["cancel_requested"] = True
            task_record["cancel_reason"] = normalized_reason
            task_record["updated_at"] = time.time()
            control = self._task_controls.get(normalized_task_id) or {}
            cancel_event = control.get("cancel_event")
            if cancel_event is not None and getattr(cancel_event, "set", None):
                cancel_event.set()
            control["cancel_reason"] = normalized_reason
            self._task_controls[normalized_task_id] = control
            if status in self.WAITING_STATUSES or status in self.QUEUED_STATUSES:
                task_record["status"] = "cancelled"
                task_record["finished_at"] = task_record["updated_at"]
                task_record["error"] = normalized_reason
                task_record["result"] = ""
                task_record["awaiting"] = {}
                task_record["status_message"] = normalized_reason
                task_record["queue_reason"] = ""
                task_record["queued_at"] = None
                task_record["queue_sequence"] = 0
                task_record["queue_position"] = None
                task_record["stats"] = {
                    "tool_calls": len(task_record.get("tool_trace") or []),
                    "duration_seconds": task_record["finished_at"] - task_record["created_at"],
                }
                self._task_sessions.pop(normalized_task_id, None)
                self._task_controls.pop(normalized_task_id, None)
                callback_snapshot = copy.deepcopy(task_record)
                self._refresh_queue_positions_locked()
            else:
                task_record["status"] = "cancelling"
                task_record["status_message"] = normalized_reason
        if callback_snapshot is not None:
            self._notify_task_callbacks(normalized_task_id, callback_snapshot)
            self._pump_queue()
        return self.get_status(normalized_task_id)
