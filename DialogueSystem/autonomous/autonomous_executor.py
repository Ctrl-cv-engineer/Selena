"""Planning and execution helpers for autonomous task mode."""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

try:
    from ..llm.CallingAPI import call_LLM
    from ..memory.ChatContext import get_core_memory_section_specs, normalize_core_memory_state
    from .autonomous_task_log import (
        TASK_SOURCE_CARRIED_OVER,
        TASK_SOURCE_SELF_GENERATED,
        TASK_STATUS_COMPLETED,
        TASK_STATUS_FAILED,
        TASK_STATUS_INTERRUPT_REQUESTED,
        TASK_STATUS_PAUSED,
        TASK_STATUS_PENDING,
        TASK_STATUS_RUNNING,
        TASK_TERMINAL_STATUSES,
        AutonomousTaskLog,
    )
    from ..config.resources import load_prompt_text, render_prompt_text
    from ..agent.token_counter import TokenCounter
except ImportError:
    from DialogueSystem.llm.CallingAPI import call_LLM
    from DialogueSystem.memory.ChatContext import get_core_memory_section_specs, normalize_core_memory_state
    from DialogueSystem.autonomous.autonomous_task_log import (
        TASK_SOURCE_CARRIED_OVER,
        TASK_SOURCE_SELF_GENERATED,
        TASK_STATUS_COMPLETED,
        TASK_STATUS_FAILED,
        TASK_STATUS_INTERRUPT_REQUESTED,
        TASK_STATUS_PAUSED,
        TASK_STATUS_PENDING,
        TASK_STATUS_RUNNING,
        TASK_TERMINAL_STATUSES,
        AutonomousTaskLog,
    )
    from DialogueSystem.config.resources import load_prompt_text, render_prompt_text
    from DialogueSystem.agent.token_counter import TokenCounter

if TYPE_CHECKING:
    import threading


logger = logging.getLogger("autonomous_task_mode.executor")

DEFAULT_MAX_DAILY_TASKS = 3
DEFAULT_MAX_TASK_ATTEMPTS = 3
DEFAULT_MAX_DAILY_INTERRUPTS = 5
DEFAULT_CANCEL_WAIT_SECONDS = 15
DEFAULT_STALE_ATTEMPT_TIMEOUT_SECONDS = 600
DEFAULT_SESSION_LEASE_TIMEOUT_SECONDS = 180
DEFAULT_TASK_PLANNING_CONFIG = {
    "enabled": True,
    "model": "qwen",
    "thinking": True,
    "json_mode": True,
    "reasoning_effort": "high",
}
DEFAULT_TASK_EXECUTION_CONFIG = {
    "enabled": True,
    "agent_type": "autonomous",
    "model": "deepseek_flash",
    "thinking": True,
    "json_mode": False,
    "reasoning_effort": "max",
    "max_tool_calls": 10,
    "timeout_seconds": 300,
    "poll_interval_seconds": 1.0,
}
DEFAULT_CORE_MEMORY_SUMMARY = "当前暂无可用的核心记忆摘要。"
CORE_MEMORY_LAYER_ORDER = ("persistent_core", "topic_working_memory")
CORE_MEMORY_LAYER_LABELS = {
    "persistent_core": "持久核心记忆",
    "topic_working_memory": "当前话题工作层",
}
TERMINAL_SUBAGENT_STATUSES = {"completed", "failed", "cancelled", "timed_out"}
UNSUPPORTED_AUTONOMOUS_WAITING_STATUSES = {"waiting_input", "waiting_approval"}
DEFAULT_SHARE_SCORE_CONFIG = {
    "enabled": True,
    "model": "qwen_flash",
    "thinking": False,
    "json_mode": False,
    "reasoning_effort": "high",
}
AUTONOMOUS_TASK_SPEAKER = "Assistant"


def _resolve_autonomous_task_speaker() -> str:
    """从配置读取角色名作为自主任务消息前缀，未配置时回退到默认值。"""
    try:
        from project_config import get_character_config

        char_name = (get_character_config() or {}).get("char_name") or ""
        char_name = str(char_name).strip()
        if char_name:
            return char_name
    except Exception:
        pass
    return AUTONOMOUS_TASK_SPEAKER


class AutonomousPlanParseError(ValueError):
    """Raised when the planning model output cannot be parsed into tasks."""


def _safe_int(value, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_bool(value, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _preview_log_text(value, *, limit: int = 160) -> str:
    normalized_text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(normalized_text) <= limit:
        return normalized_text
    return f"{normalized_text[: max(0, limit - 3)]}..."


def _resolve_session_lease_timeout_seconds(config: dict | None) -> int:
    return max(
        30,
        _safe_int(
            (config or {}).get(
                "session_lease_timeout_seconds",
                DEFAULT_SESSION_LEASE_TIMEOUT_SECONDS,
            ),
            default=DEFAULT_SESSION_LEASE_TIMEOUT_SECONDS,
        ),
    )


def _build_autonomous_session_owner_id(dialogue_system) -> str:
    runtime_name = type(dialogue_system).__name__
    return f"{socket.gethostname()}:{os.getpid()}:{runtime_name}:{id(dialogue_system)}"


def _sync_tasks_to_schedule(dialogue_system, task_specs: list[dict], task_date: str):
    """将自主任务写入 schedule_system，失败不阻塞主流程。"""
    repo = getattr(dialogue_system, "schedule_repository", None)
    if repo is None:
        return
    for spec in task_specs:
        content = spec.get("task_content", "")
        if not content:
            continue
        try:
            repo.create_task(
                task_date=task_date,
                reminder_time="00:00",
                task_content=f"[ATM] {content}",
            )
        except Exception:
            logger.warning("Failed to sync ATM task to schedule | task=%s", content[:80], exc_info=True)


def _resolve_session_day(session_date: str | None = None) -> date:
    normalized_session_date = str(session_date or "").strip()
    if not normalized_session_date:
        return date.today()
    return datetime.strptime(normalized_session_date, "%Y-%m-%d").date()


def _strip_code_fence(text: str) -> str:
    normalized_text = str(text or "").strip()
    if not normalized_text.startswith("```"):
        return normalized_text
    lines = normalized_text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _load_json_payload(raw_result):
    if isinstance(raw_result, (list, dict)):
        return raw_result

    normalized_text = _strip_code_fence(str(raw_result or ""))
    if not normalized_text:
        raise AutonomousPlanParseError("Planning response is empty.")

    candidate_texts = [normalized_text]
    array_start = normalized_text.find("[")
    array_end = normalized_text.rfind("]")
    if array_start >= 0 and array_end >= array_start:
        candidate_text = normalized_text[array_start: array_end + 1].strip()
        if candidate_text and candidate_text not in candidate_texts:
            candidate_texts.append(candidate_text)

    object_start = normalized_text.find("{")
    object_end = normalized_text.rfind("}")
    if object_start >= 0 and object_end >= object_start:
        candidate_text = normalized_text[object_start: object_end + 1].strip()
        if candidate_text and candidate_text not in candidate_texts:
            candidate_texts.append(candidate_text)

    last_error = None
    for candidate_text in candidate_texts:
        try:
            return json.loads(candidate_text)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue

    raise AutonomousPlanParseError(f"Failed to decode planning JSON: {last_error}")


def _normalize_task_spec(raw_task) -> dict | None:
    if not isinstance(raw_task, dict):
        return None
    task_content = str(
        raw_task.get("task_content")
        or raw_task.get("task")
        or raw_task.get("content")
        or ""
    ).strip()
    expected_goal = str(
        raw_task.get("expected_goal")
        or raw_task.get("goal")
        or raw_task.get("expectedGoal")
        or ""
    ).strip()
    if not task_content:
        return None
    return {
        "task_content": task_content,
        "expected_goal": expected_goal,
    }


def _build_autonomous_task_prompt(task: dict) -> str:
    task_content = str((task or {}).get("task_content") or "").strip()
    if not task_content:
        return ""
    speaker = _resolve_autonomous_task_speaker()
    normalized_prefixes = (
        f"{speaker}：",
        f"{speaker}:",
    )
    if task_content.startswith(normalized_prefixes):
        return task_content
    return f"{speaker}：{task_content}"


def parse_json_response(raw_result, *, max_items: int | None = None) -> list[dict]:
    """Parse planning JSON into normalized task specs."""
    payload = _load_json_payload(raw_result)
    if isinstance(payload, dict):
        for key in ("tasks", "items", "data"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                payload = candidate
                break
    if not isinstance(payload, list):
        raise AutonomousPlanParseError("Planning response is not a JSON array.")

    normalized_tasks = []
    for raw_task in payload:
        task_spec = _normalize_task_spec(raw_task)
        if task_spec is None:
            continue
        normalized_tasks.append(task_spec)

    if max_items is not None:
        normalized_tasks = normalized_tasks[: max(0, int(max_items))]
    return normalized_tasks


def _format_carryover_tasks(tasks: list[dict]) -> str:
    normalized_tasks = [task for task in list(tasks or []) if isinstance(task, dict)]
    if not normalized_tasks:
        return "无"

    lines = []
    for index, task in enumerate(normalized_tasks, start=1):
        task_content = str(task.get("task_content") or "").strip()
        if not task_content:
            continue
        lines.append(f"{index}. {task_content}")
        expected_goal = str(task.get("expected_goal") or "").strip()
        if expected_goal:
            lines.append(f"   预期目标：{expected_goal}")
        status = str(task.get("status") or "").strip()
        if status:
            lines.append(f"   当前状态：{status}")
        pause_reason = str(task.get("pause_reason") or "").strip()
        if pause_reason:
            lines.append(f"   暂停原因：{pause_reason}")
    return "\n".join(lines).strip() or "无"


def _get_core_memory_summary(dialogue_system) -> str:
    summary_getter = getattr(dialogue_system, "get_core_memory_summary", None)
    if callable(summary_getter):
        try:
            summary_text = str(summary_getter() or "").strip()
        except Exception:
            logger.exception("Failed to call dialogue_system.get_core_memory_summary().")
        else:
            if summary_text:
                return summary_text

    raw_state = getattr(dialogue_system, "_context_memory_state", None)
    if not isinstance(raw_state, dict):
        return DEFAULT_CORE_MEMORY_SUMMARY

    try:
        normalized_state = normalize_core_memory_state(raw_state)
        section_specs = list(get_core_memory_section_specs() or [])
    except Exception:
        logger.exception("Failed to serialize core memory summary for autonomous planning.")
        return DEFAULT_CORE_MEMORY_SUMMARY

    lines = []
    for layer_key in CORE_MEMORY_LAYER_ORDER:
        layer_lines = []
        for spec in section_specs:
            if str(spec.get("layer") or "").strip() != layer_key:
                continue
            section_items = normalized_state.get(spec["key"]) or []
            item_texts = [
                str(item.get("text") or "").strip()
                for item in section_items
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            ]
            if not item_texts:
                continue
            layer_lines.append(f"- {spec['label']}：{'；'.join(item_texts)}")
        if not layer_lines:
            continue
        lines.append(f"{CORE_MEMORY_LAYER_LABELS.get(layer_key, layer_key)}：")
        lines.extend(layer_lines)
    return "\n".join(lines).strip() or DEFAULT_CORE_MEMORY_SUMMARY


def _normalize_task_planning_config(config: dict | None) -> dict:
    planning_config = dict(DEFAULT_TASK_PLANNING_CONFIG)
    planning_config.update(dict((config or {}).get("task_planning") or {}))
    planning_config["enabled"] = _safe_bool(planning_config.get("enabled"), default=True)
    planning_config["thinking"] = _safe_bool(planning_config.get("thinking"), default=True)
    planning_config["json_mode"] = _safe_bool(planning_config.get("json_mode"), default=True)
    planning_config["model"] = str(planning_config.get("model") or "qwen").strip() or "qwen"
    planning_config["reasoning_effort"] = (
        str(planning_config.get("reasoning_effort") or "high").strip() or "high"
    )
    return planning_config


def _normalize_task_execution_config(config: dict | None) -> dict:
    execution_config = dict(DEFAULT_TASK_EXECUTION_CONFIG)
    execution_config.update(dict((config or {}).get("task_execution") or {}))
    execution_config["enabled"] = _safe_bool(execution_config.get("enabled"), default=True)
    execution_config["agent_type"] = (
        str(execution_config.get("agent_type") or "autonomous").strip().lower() or "autonomous"
    )
    execution_config["model"] = str(execution_config.get("model") or "").strip()
    execution_config["max_tool_calls"] = max(
        1,
        _safe_int(
            execution_config.get("max_tool_calls"),
            default=DEFAULT_TASK_EXECUTION_CONFIG["max_tool_calls"],
        ),
    )
    try:
        execution_config["timeout_seconds"] = max(
            1.0,
            float(execution_config.get("timeout_seconds") or DEFAULT_TASK_EXECUTION_CONFIG["timeout_seconds"]),
        )
    except (TypeError, ValueError):
        execution_config["timeout_seconds"] = float(DEFAULT_TASK_EXECUTION_CONFIG["timeout_seconds"])
    try:
        execution_config["poll_interval_seconds"] = min(
            2.0,
            max(
                0.2,
                float(
                    execution_config.get("poll_interval_seconds")
                    or DEFAULT_TASK_EXECUTION_CONFIG["poll_interval_seconds"]
                ),
            ),
        )
    except (TypeError, ValueError):
        execution_config["poll_interval_seconds"] = float(
            DEFAULT_TASK_EXECUTION_CONFIG["poll_interval_seconds"]
        )
    return execution_config


def _enforce_task_attempt_limits(
    task_log: AutonomousTaskLog,
    tasks: list[dict],
    *,
    max_attempts: int,
) -> list[dict]:
    normalized_max_attempts = max(0, int(max_attempts or 0))
    runnable_tasks = []
    for task in list(tasks or []):
        if not isinstance(task, dict):
            continue
        if normalized_max_attempts > 0 and int(task.get("attempt_count", 0) or 0) >= normalized_max_attempts:
            task_log.fail_task(
                int(task["id"]),
                error=f"Autonomous task exceeded max attempts ({normalized_max_attempts}).",
            )
            continue
        runnable_tasks.append(task)
    return runnable_tasks


def _resolve_session_finish_reason(task_log: AutonomousTaskLog, session_date: str) -> str:
    tasks = task_log.get_today_tasks(session_date)
    if not tasks:
        return "no_tasks"
    statuses = {str(task.get("status") or "").strip().lower() for task in tasks if isinstance(task, dict)}
    if statuses & {
        TASK_STATUS_PENDING,
        TASK_STATUS_PAUSED,
        TASK_STATUS_INTERRUPT_REQUESTED,
        TASK_STATUS_RUNNING,
    }:
        return "partial"
    if TASK_STATUS_FAILED in statuses:
        return "partial"
    if statuses == {TASK_STATUS_COMPLETED}:
        return "all_completed"
    return "partial"


def _load_summary_memory_helpers():
    try:
        from ..memory.history_summary_worker import (
            normalize_memory,
            persist_memories,
            summarize_records,
        )
    except ImportError:
        from DialogueSystem.memory.history_summary_worker import (
            normalize_memory,
            persist_memories,
            summarize_records,
        )
    return {
        "normalize_memory": normalize_memory,
        "persist_memories": persist_memories,
        "summarize_records": summarize_records,
    }


def _get_summary_model_key(dialogue_system) -> str:
    task_config_getter = getattr(dialogue_system, "_get_model_select_task_config", None)
    if callable(task_config_getter):
        try:
            task_config = dict(task_config_getter("SummaryAndMermory") or {})
        except Exception:
            logger.exception("Failed to resolve SummaryAndMermory task config.")
        else:
            model_key = str(task_config.get("model") or "").strip()
            if model_key:
                return model_key

    model_key_getter = getattr(dialogue_system, "_get_model_select_model_key", None)
    if callable(model_key_getter):
        try:
            model_key = str(model_key_getter("SummaryAndMermory") or "").strip()
        except Exception:
            logger.exception("Failed to resolve SummaryAndMermory model key.")
        else:
            if model_key:
                return model_key

    fallback_model = str(getattr(dialogue_system, "model", "") or "").strip()
    return fallback_model


def _normalize_sharing_score_config(config: dict | None) -> dict:
    sharing_config = dict(DEFAULT_SHARE_SCORE_CONFIG)
    sharing_config.update(dict((config or {}).get("sharing_score") or {}))
    sharing_config["enabled"] = _safe_bool(sharing_config.get("enabled"), default=True)
    sharing_config["thinking"] = _safe_bool(sharing_config.get("thinking"), default=False)
    sharing_config["json_mode"] = _safe_bool(sharing_config.get("json_mode"), default=False)
    sharing_config["model"] = (
        str(sharing_config.get("model") or DEFAULT_SHARE_SCORE_CONFIG["model"]).strip()
        or DEFAULT_SHARE_SCORE_CONFIG["model"]
    )
    sharing_config["reasoning_effort"] = (
        str(sharing_config.get("reasoning_effort") or DEFAULT_SHARE_SCORE_CONFIG["reasoning_effort"]).strip()
        or DEFAULT_SHARE_SCORE_CONFIG["reasoning_effort"]
    )
    return sharing_config


def _extract_topic_keywords(text: str, *, limit: int = 5) -> list[str]:
    normalized_text = str(text or "").strip().lower()
    if not normalized_text:
        return []
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9][a-z0-9_-]{2,}", normalized_text)
    if not tokens:
        return []
    stop_words = {
        "then",
        "that",
        "this",
        "with",
        "from",
        "have",
        "just",
        "really",
        "very",
        "about",
        "into",
        "them",
        "they",
        "were",
        "what",
        "when",
        "where",
        "which",
        "自己",
        "我们",
        "你们",
        "他们",
        "事情",
        "一下",
        "一个",
        "这个",
        "那个",
        "然后",
        "已经",
        "因为",
        "所以",
        "还是",
        "觉得",
        "有点",
        "今天",
        "刚刚",
        "进行",
        "完成",
        "处理",
    }
    frequencies = {}
    order = []
    for token in tokens:
        if token in stop_words:
            continue
        if token not in frequencies:
            order.append(token)
        frequencies[token] = frequencies.get(token, 0) + 1
    ordered_tokens = sorted(
        frequencies.items(),
        key=lambda item: (-item[1], order.index(item[0])),
    )
    return [token for token, _ in ordered_tokens[: max(1, int(limit or 5))]]


def score_sharing_desire(
    memories: list,
    *,
    config: dict | None,
    session,
    caller_prefix: str,
):
    """Score each memory with a 0-10 sharing desire value."""
    helpers = _load_summary_memory_helpers()
    normalize_memory = helpers["normalize_memory"]
    sharing_config = _normalize_sharing_score_config(config)
    if not sharing_config["enabled"]:
        scored_memories = []
        caller_names = []
        for raw_memory in list(memories or []):
            normalized = normalize_memory(raw_memory)
            if normalized is None:
                continue
            normalized["sharing_score"] = 5
            normalized["topic_keywords"] = _extract_topic_keywords(
                normalized.get("personalizedText") or normalized.get("text")
            )
            scored_memories.append(normalized)
        return scored_memories, caller_names

    scoring_prompt = render_prompt_text("AutonomousSharingScore")
    scored_memories = []
    caller_names = []
    valid_index = 0
    for raw_memory in list(memories or []):
        normalized = normalize_memory(raw_memory)
        if normalized is None:
            continue
        valid_index += 1
        caller_name = f"{caller_prefix}.{valid_index}"
        messages = [
            {"role": "system", "content": scoring_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "text": normalized["text"],
                        "personalizedText": normalized["personalizedText"],
                        "textType": normalized["textType"],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        raw_score = call_LLM(
            messages,
            sharing_config["model"],
            session,
            sharing_config["thinking"],
            sharing_config["json_mode"],
            caller=caller_name,
            reasoning_effort=sharing_config["reasoning_effort"],
        )
        try:
            score = max(0, min(10, int(float(str(raw_score or "").strip()))))
        except (TypeError, ValueError):
            score = 5
        normalized["sharing_score"] = score
        normalized["topic_keywords"] = _extract_topic_keywords(
            normalized.get("personalizedText") or normalized.get("text")
        )
        scored_memories.append(normalized)
        caller_names.append(caller_name)
    return scored_memories, caller_names


def summarize_autonomous_session(dialogue_system, task_log: AutonomousTaskLog, session_date: str):
    """Summarize completed autonomous tasks into long-term atomic memories."""
    tasks = task_log.get_today_tasks(session_date)
    completed_tasks = [
        task
        for task in list(tasks or [])
        if isinstance(task, dict)
        and str(task.get("status") or "").strip().lower() == TASK_STATUS_COMPLETED
    ]
    if not completed_tasks:
        logger.info(
            "Skip autonomous session summary | session_date=%s | reason=no_completed_tasks",
            session_date,
        )
        return []

    summary_model_key = _get_summary_model_key(dialogue_system)
    if not summary_model_key:
        logger.warning(
            "Skip autonomous session summary | session_date=%s | reason=missing_summary_model",
            session_date,
        )
        return []

    embedding_model = getattr(dialogue_system, "local_embedding_model", None)
    if embedding_model is None:
        ensure_embedding_model = getattr(dialogue_system, "ensure_local_embedding_model", None)
        if callable(ensure_embedding_model):
            try:
                embedding_model = ensure_embedding_model()
            except Exception:
                logger.exception(
                    "Failed to initialize embedding model for autonomous summary | session_date=%s",
                    session_date,
                )
        if embedding_model is None:
            logger.warning(
                "Skip autonomous session summary | session_date=%s | reason=embedding_model_unavailable",
                session_date,
            )
            return []

    memory_collection = getattr(dialogue_system, "_memory_collection", None)
    qdrant_getter = getattr(dialogue_system, "_get_or_create_search_qdrant", None)
    if not memory_collection or not callable(qdrant_getter):
        logger.warning(
            "Skip autonomous session summary | session_date=%s | reason=memory_qdrant_unavailable",
            session_date,
        )
        return []

    try:
        memory_qdrant = qdrant_getter("_memory_qdrant", memory_collection)
    except Exception:
        logger.exception(
            "Failed to initialize memory qdrant for autonomous summary | session_date=%s",
            session_date,
        )
        return []

    helpers = _load_summary_memory_helpers()
    summarize_records = helpers["summarize_records"]
    persist_memories = helpers["persist_memories"]
    records = []
    for task in completed_tasks:
        task_content = str(task.get("task_content") or "").strip()
        if not task_content:
            continue
        expected_goal = str(task.get("expected_goal") or "").strip()
        user_content = f"[自主任务] {task_content}"
        if expected_goal:
            user_content = f"{user_content}\n预期目标：{expected_goal}"
        execution_log = str(task.get("execution_log") or "").strip()
        if not execution_log:
            execution_log = "任务已完成，但当前没有可用的执行记录。"
        records.append({"role": "user", "content": user_content})
        records.append({"role": "assistant", "content": execution_log})
    if not records:
        logger.info(
            "Skip autonomous session summary | session_date=%s | reason=no_summary_records",
            session_date,
        )
        return []

    summary_caller = f"autonomous_task.summary.{session_date}"
    raw_memories = summarize_records(
        records=records,
        summary_prompt=render_prompt_text("SummaryMermory"),
        model_key=summary_model_key,
        session=dialogue_system.session,
        reasoning_effort="high",
        caller=summary_caller,
    )
    _sync_session_usage_from_logs(
        task_log,
        session_date,
        caller_prefix=summary_caller,
    )
    scored_memories, share_score_callers = score_sharing_desire(
        raw_memories,
        config=(dialogue_system.config or {}).get("AutonomousTaskMode", {}),
        session=dialogue_system.session,
        caller_prefix=f"autonomous_task.share_score.{session_date}",
    )
    for caller_name in share_score_callers:
        _sync_session_usage_from_logs(
            task_log,
            session_date,
            caller_prefix=caller_name,
        )
    if not scored_memories:
        logger.info(
            "Autonomous session summary produced no valid memories | session_date=%s",
            session_date,
        )
        return []

    conflict_caller_prefix = f"autonomous_task.memory_conflict.{session_date}"
    persist_memories(
        memories=scored_memories,
        embedding_model=embedding_model,
        memory_qdrant=memory_qdrant,
        similarity_threshold=0.9,
        model_key=summary_model_key,
        session=dialogue_system.session,
        source_file=f"autonomous_{session_date}.jsonl",
        logger=logger,
        reasoning_effort="high",
        source="autonomous_task",
        conflict_caller_prefix=conflict_caller_prefix,
        extra_payload_builder=lambda raw_memory, normalized_memory: {
            "sharing_score": max(
                0,
                min(10, _safe_int(raw_memory.get("sharing_score", 5), default=5)),
            ),
            "topic_keywords": [
                str(keyword).strip()
                for keyword in list(raw_memory.get("topic_keywords") or [])
                if str(keyword).strip()
            ][:5]
            or _extract_topic_keywords(
                normalized_memory.get("personalizedText") or normalized_memory.get("text")
            ),
            "last_mentioned_at": None,
            "mention_count": 0,
        },
    )
    _sync_session_usage_from_logs(
        task_log,
        session_date,
        caller_prefix=conflict_caller_prefix,
    )
    logger.info(
        "Autonomous session summary persisted | session_date=%s | task_count=%s | memory_count=%s",
        session_date,
        len(completed_tasks),
        len(scored_memories),
    )
    return scored_memories


def _sync_session_usage_from_logs(
    task_log: AutonomousTaskLog,
    session_date: str,
    *,
    caller_prefix: str,
) -> dict:
    usage = TokenCounter.get_actual_usage_from_logs(caller_prefix)
    input_tokens = max(0, _safe_int(usage.get("input_tokens", 0), default=0))
    output_tokens = max(0, _safe_int(usage.get("output_tokens", 0), default=0))
    if input_tokens > 0 or output_tokens > 0:
        task_log.add_session_tokens(
            session_date,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    return usage


def _sync_task_usage_delta_from_logs(
    task_log: AutonomousTaskLog,
    *,
    session_date: str,
    task_id: int,
    caller_prefix: str,
    synced_usage: dict,
) -> dict:
    usage = TokenCounter.get_actual_usage_from_logs(caller_prefix)
    total_input = max(0, _safe_int(usage.get("input_tokens", 0), default=0))
    total_output = max(0, _safe_int(usage.get("output_tokens", 0), default=0))
    previous_input = max(0, _safe_int((synced_usage or {}).get("input_tokens", 0), default=0))
    previous_output = max(0, _safe_int((synced_usage or {}).get("output_tokens", 0), default=0))
    delta_input = max(0, total_input - previous_input)
    delta_output = max(0, total_output - previous_output)
    if delta_input > 0 or delta_output > 0:
        task_log.record_token_usage(
            task_id,
            input_tokens=delta_input,
            output_tokens=delta_output,
        )
        task_log.add_session_tokens(
            session_date,
            input_tokens=delta_input,
            output_tokens=delta_output,
        )
    synced_usage["input_tokens"] = total_input
    synced_usage["output_tokens"] = total_output
    synced_usage["total_tokens"] = total_input + total_output
    return usage


def generate_daily_plan(
    dialogue_system,
    task_log: AutonomousTaskLog,
    config: dict | None,
    session_date: str | None = None,
) -> list[dict]:
    """Generate today's autonomous plan, including carryover and new tasks."""
    today_dt = _resolve_session_day(session_date)
    today = today_dt.strftime("%Y-%m-%d")
    yesterday = (today_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    atm_config = dict(config or {})
    max_tasks = max(
        0,
        _safe_int(
            atm_config.get("max_daily_tasks", DEFAULT_MAX_DAILY_TASKS),
            default=DEFAULT_MAX_DAILY_TASKS,
        ),
    )
    planning_config = _normalize_task_planning_config(atm_config)

    session_record = task_log.get_or_create_daily_session(today) or {}
    if session_record.get("plan_generated_at"):
        existing_tasks = task_log.get_today_tasks(today)
        logger.info(
            "Reuse autonomous daily plan | session_date=%s | task_count=%s",
            today,
            len(existing_tasks),
        )
        return existing_tasks

    carryover_tasks = task_log.get_pending_carryover_tasks(yesterday)
    for task in carryover_tasks[:max_tasks]:
        task_log.create_task(
            task_date=today,
            task_content=task.get("task_content") or "",
            expected_goal=task.get("expected_goal") or "",
            source=TASK_SOURCE_CARRIED_OVER,
            carry_over_from_date=task.get("task_date") or yesterday,
            carry_over_from_id=task.get("id"),
        )
        task_log.mark_carried_over(task["id"])

    today_tasks = task_log.get_today_tasks(today)
    remaining_slots = max(0, max_tasks - len(today_tasks))
    if remaining_slots <= 0 or not planning_config["enabled"]:
        task_log.mark_plan_generated(today)
        final_tasks = task_log.get_today_tasks(today)
        logger.info(
            "Autonomous daily plan finalized without new generation | session_date=%s | task_count=%s | reason=%s",
            today,
            len(final_tasks),
            "no_remaining_slots" if remaining_slots <= 0 else "planning_disabled",
        )
        return final_tasks

    logger.info(
        "Generating autonomous daily plan | session_date=%s | carryover_count=%s | current_task_count=%s | remaining_slots=%s",
        today,
        len(carryover_tasks),
        len(today_tasks),
        remaining_slots,
    )

    roleplay_prompt = render_prompt_text("RolePlay")
    rendered_prompt = render_prompt_text(
        "AutonomousTaskPlan",
        {
            "current_date": today,
            "carryover_tasks": _format_carryover_tasks(carryover_tasks[:max_tasks]),
            "remaining_slots": remaining_slots,
            "core_memory_summary": _get_core_memory_summary(dialogue_system),
        },
    )
    planning_caller = f"autonomous_task.plan.{today}"
    raw_result = call_LLM(
        [
            {"role": "system", "content": roleplay_prompt},
            {"role": "system", "content": rendered_prompt},
        ],
        planning_config["model"],
        dialogue_system.session,
        planning_config["thinking"],
        planning_config["json_mode"],
        caller=planning_caller,
        reasoning_effort=planning_config["reasoning_effort"],
    )
    _sync_session_usage_from_logs(
        task_log,
        today,
        caller_prefix=planning_caller,
    )

    try:
        new_tasks = parse_json_response(raw_result, max_items=remaining_slots)
    except AutonomousPlanParseError:
        logger.exception("Failed to parse autonomous daily planning response.")
        task_log.mark_plan_generated(today)
        return task_log.get_today_tasks(today)

    for task_spec in new_tasks:
        task_log.create_task(
            task_date=today,
            task_content=task_spec["task_content"],
            expected_goal=task_spec.get("expected_goal", ""),
            source=TASK_SOURCE_SELF_GENERATED,
        )

    _sync_tasks_to_schedule(dialogue_system, new_tasks, today)

    task_log.mark_plan_generated(today)
    final_tasks = task_log.get_today_tasks(today)
    logger.info(
        "Autonomous daily plan ready | session_date=%s | new_task_count=%s | total_task_count=%s",
        today,
        len(new_tasks),
        len(final_tasks),
    )
    return final_tasks


def run_daily_autonomous_session(
    *,
    dialogue_system,
    task_log: AutonomousTaskLog,
    token_counter: TokenCounter,
    interrupt_event: threading.Event,
    config: dict | None,
    session_date: str | None = None,
):
    """Run one autonomous daily session: reconcile -> plan -> execute -> finish."""
    today = _resolve_session_day(session_date).strftime("%Y-%m-%d")
    atm_config = dict(config or {})
    session_record = task_log.get_or_create_daily_session(today) or {}
    if session_record.get("session_finished_at"):
        logger.info(
            "Skip autonomous daily session | session_date=%s | reason=already_finished | finish_reason=%s",
            today,
            session_record.get("finish_reason") or "",
        )
        return session_record

    if interrupt_event.is_set():
        logger.info(
            "Skip autonomous daily session | session_date=%s | reason=interrupt_already_requested",
            today,
        )
        return session_record

    max_daily_interrupts = max(
        0,
        _safe_int(
            atm_config.get("max_daily_interrupts", DEFAULT_MAX_DAILY_INTERRUPTS),
            default=DEFAULT_MAX_DAILY_INTERRUPTS,
        ),
    )
    if (
        max_daily_interrupts > 0
        and task_log.get_daily_interrupt_count(today) >= max_daily_interrupts
    ):
        logger.warning(
            "Autonomous daily session blocked by interrupt limit | session_date=%s | interrupt_limit=%s",
            today,
            max_daily_interrupts,
        )
        return task_log.finish_daily_session(today, finish_reason="interrupt_limit")

    session_lease_timeout_seconds = _resolve_session_lease_timeout_seconds(atm_config)
    session_owner_id = _build_autonomous_session_owner_id(dialogue_system)
    session_lease = task_log.acquire_session_lease(
        today,
        owner_id=session_owner_id,
        ttl_seconds=session_lease_timeout_seconds,
    )
    if not session_lease:
        logger.info(
            "Skip autonomous daily session | session_date=%s | reason=lease_held_elsewhere",
            today,
        )
        return task_log.get_or_create_daily_session(today)
    session_lease_id = str((session_lease or {}).get("lease_id") or "").strip()
    logger.info(
        "Autonomous daily session started | session_date=%s | lease_id=%s | owner_id=%s",
        today,
        session_lease_id,
        session_owner_id,
    )

    try:
        max_attempts = max(
            0,
            _safe_int(
                atm_config.get("max_task_attempts", DEFAULT_MAX_TASK_ATTEMPTS),
                default=DEFAULT_MAX_TASK_ATTEMPTS,
            ),
        )
        stale_timeout_seconds = max(
            0,
            _safe_int(
                atm_config.get(
                    "stale_attempt_timeout_seconds",
                    DEFAULT_STALE_ATTEMPT_TIMEOUT_SECONDS,
                ),
                default=DEFAULT_STALE_ATTEMPT_TIMEOUT_SECONDS,
            ),
        )
        try:
            reconcile_timeout_seconds = min(
                stale_timeout_seconds,
                session_lease_timeout_seconds,
            )
            reconciled_count = task_log.reconcile_incomplete_tasks(
                today,
                stale_timeout_seconds=reconcile_timeout_seconds,
            )
            if reconciled_count > 0:
                logger.warning(
                    "Reconciled stale autonomous attempts | session_date=%s | paused_task_count=%s | reconcile_timeout_seconds=%s",
                    today,
                    reconciled_count,
                    reconcile_timeout_seconds,
                )

            if not session_record.get("plan_generated_at"):
                if not task_log.heartbeat_session_lease(
                    today,
                    session_lease_id,
                    ttl_seconds=session_lease_timeout_seconds,
                ):
                    logger.warning(
                        "Autonomous daily session lease lost before planning | session_date=%s | lease_id=%s",
                        today,
                        session_lease_id,
                    )
                    return task_log.get_or_create_daily_session(today)
                generate_daily_plan(
                    dialogue_system,
                    task_log,
                    atm_config,
                    session_date=today,
                )
                if not task_log.heartbeat_session_lease(
                    today,
                    session_lease_id,
                    ttl_seconds=session_lease_timeout_seconds,
                ):
                    logger.warning(
                        "Autonomous daily session lease lost after planning | session_date=%s | lease_id=%s",
                        today,
                        session_lease_id,
                    )
                    return task_log.get_or_create_daily_session(today)

            tasks = _enforce_task_attempt_limits(
                task_log,
                task_log.get_today_runnable_tasks(today),
                max_attempts=max_attempts,
            )

            if not tasks:
                finish_reason = _resolve_session_finish_reason(task_log, today)
                logger.info(
                    "No runnable autonomous tasks | session_date=%s | finish_reason=%s",
                    today,
                    finish_reason,
                )
                return task_log.finish_daily_session(today, finish_reason=finish_reason)

            execution_config = _normalize_task_execution_config(atm_config)
            if not execution_config.get("enabled", True):
                logger.info(
                    "Autonomous task execution disabled | session_date=%s",
                    today,
                )
                return task_log.finish_daily_session(today, finish_reason="partial")

            finish_reason = ""
            for task in tasks:
                current_task = task_log.get_task(task["id"]) or task
                if not current_task or current_task.get("status") in TASK_TERMINAL_STATUSES:
                    continue
                if interrupt_event.is_set():
                    return task_log.get_or_create_daily_session(today)
                if not task_log.heartbeat_session_lease(
                    today,
                    session_lease_id,
                    ttl_seconds=session_lease_timeout_seconds,
                ):
                    logger.warning(
                        "Autonomous daily session lease lost before task execution | session_date=%s | lease_id=%s | task_id=%s",
                        today,
                        session_lease_id,
                        current_task.get("id"),
                    )
                    return task_log.get_or_create_daily_session(today)
                can_continue, _ = token_counter.check_session_budget(task_log, today)
                if not can_continue:
                    finish_reason = "token_limit"
                    logger.warning(
                        "Autonomous daily session stopped by token budget | session_date=%s",
                        today,
                    )
                    break
                logger.info(
                    "Executing autonomous task | session_date=%s | task_id=%s | attempt_count=%s | status=%s | task=%s",
                    today,
                    current_task.get("id"),
                    current_task.get("attempt_count"),
                    current_task.get("status"),
                    _preview_log_text(current_task.get("task_content")),
                )
                execute_single_task(
                    dialogue_system=dialogue_system,
                    task_log=task_log,
                    token_counter=token_counter,
                    interrupt_event=interrupt_event,
                    task=current_task,
                    config=atm_config,
                    session_date=today,
                    session_lease_id=session_lease_id,
                    session_lease_timeout_seconds=session_lease_timeout_seconds,
                )
                refreshed_task = task_log.get_task(current_task["id"]) or {}
                logger.info(
                    "Autonomous task step finished | session_date=%s | task_id=%s | status=%s | pause_reason=%s",
                    today,
                    current_task.get("id"),
                    refreshed_task.get("status") or "",
                    refreshed_task.get("pause_reason") or "",
                )
                if interrupt_event.is_set():
                    return task_log.get_or_create_daily_session(today)
            if not finish_reason:
                finish_reason = _resolve_session_finish_reason(task_log, today)
        except Exception:
            logger.exception("Autonomous daily session failed unexpectedly.")
            finish_reason = "error"

        finished_session = task_log.finish_daily_session(today, finish_reason=finish_reason)
        logger.info(
            "Autonomous daily session finished | session_date=%s | finish_reason=%s | tasks_planned=%s | tasks_completed=%s | tasks_carried_over=%s | input_tokens=%s | output_tokens=%s",
            today,
            finish_reason,
            (finished_session or {}).get("tasks_planned", 0),
            (finished_session or {}).get("tasks_completed", 0),
            (finished_session or {}).get("tasks_carried_over", 0),
            (finished_session or {}).get("total_input_tokens", 0),
            (finished_session or {}).get("total_output_tokens", 0),
        )
        if (
            _safe_bool(atm_config.get("summary_on_complete"), default=True)
            and finish_reason in {"all_completed", "token_limit"}
        ):
            try:
                summarize_autonomous_session(dialogue_system, task_log, today)
            except Exception:
                logger.exception("Failed to summarize autonomous session | session_date=%s", today)
        return finished_session
    finally:
        if session_lease_id:
            task_log.release_session_lease(today, session_lease_id)


def execute_single_task(
    *,
    dialogue_system,
    task_log: AutonomousTaskLog,
    token_counter: TokenCounter,
    interrupt_event: threading.Event,
    task: dict,
    config: dict | None,
    session_date: str | None = None,
    session_lease_id: str | None = None,
    session_lease_timeout_seconds: int = DEFAULT_SESSION_LEASE_TIMEOUT_SECONDS,
):
    """Execute a single autonomous task using background sub-agent polling."""
    task_id = int(task["id"])
    normalized_session_date = str(
        session_date or task.get("task_date") or date.today().strftime("%Y-%m-%d")
    ).strip()
    execution_config = _normalize_task_execution_config(config)
    cancel_wait_seconds = max(
        0,
        _safe_int(
            (config or {}).get("cancel_wait_seconds", DEFAULT_CANCEL_WAIT_SECONDS),
            default=DEFAULT_CANCEL_WAIT_SECONDS,
        ),
    )
    runtime = getattr(dialogue_system, "subagent_runtime", None)
    if runtime is None:
        return task_log.fail_task(task_id, error="subagent_runtime is unavailable.")

    attempt_id = ""
    lease_id = ""
    subagent_task_id = ""
    attempt_caller_prefix = ""
    synced_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    try:
        attempt = task_log.begin_task_attempt(task_id)
        attempt_id = str(attempt.get("attempt_id") or "").strip()
        lease_id = str(attempt.get("lease_id") or "").strip()
        attempt_caller_prefix = f"autonomous_task.exec.{task_id}.{attempt_id}"
        task_prompt = _build_autonomous_task_prompt(task)
        logger.info(
            "Autonomous task attempt started | task_id=%s | attempt_id=%s | lease_id=%s | task=%s | expected_goal=%s",
            task_id,
            attempt_id,
            lease_id,
            _preview_log_text(task.get("task_content")),
            _preview_log_text(task.get("expected_goal")),
        )
        spawn_result = runtime.spawn(
            task=task_prompt,
            agent_type=execution_config["agent_type"],
            task_context={
                "autonomous_task_id": task_id,
                "attempt_id": attempt_id,
                "lease_id": lease_id,
                "llm_caller_prefix": attempt_caller_prefix,
                "expected_goal": str(task.get("expected_goal") or "").strip(),
                "resume_snapshot": str(task.get("resume_snapshot") or ""),
            },
            model=execution_config["model"],
            max_tool_calls=execution_config["max_tool_calls"],
            timeout_seconds=execution_config["timeout_seconds"],
            run_in_background=True,
            priority="background",
            use_cache=False,
        )
        if not spawn_result.get("ok", False):
            logger.warning(
                "Autonomous task spawn failed | task_id=%s | attempt_id=%s | error=%s",
                task_id,
                attempt_id,
                _preview_log_text(spawn_result.get("error")),
            )
            return task_log.fail_task(
                task_id,
                error=str(spawn_result.get("error") or "Autonomous subagent spawn failed."),
            )

        spawned_task = dict(spawn_result.get("task") or {})
        subagent_task_id = str(spawned_task.get("task_id") or "").strip()
        if not subagent_task_id:
            logger.warning(
                "Autonomous task spawn missing runtime id | task_id=%s | attempt_id=%s",
                task_id,
                attempt_id,
            )
            return task_log.fail_task(
                task_id,
                error="Autonomous subagent spawn did not return task_id.",
            )
        logger.info(
            "Autonomous subagent spawned | task_id=%s | attempt_id=%s | runtime_task_id=%s | agent_type=%s | model=%s | timeout_seconds=%s",
            task_id,
            attempt_id,
            subagent_task_id,
            execution_config["agent_type"],
            execution_config["model"],
            execution_config["timeout_seconds"],
        )
        task_log.persist_attempt_runtime_task_id(
            task_id,
            attempt_id,
            lease_id,
            subagent_task_id,
        )

        def _finalize_runtime_attempt_callback(_runtime_task_id: str, runtime_task_snapshot: dict):
            try:
                latest_task = task_log.get_task(task_id)
                latest_attempt = task_log.get_attempt(attempt_id) if attempt_id else None
                if latest_task is None or latest_attempt is None:
                    return
                if str(latest_task.get("current_attempt_id") or "").strip() != attempt_id:
                    return
                if str(latest_attempt.get("lease_id") or "").strip() != lease_id:
                    return
                if str(latest_task.get("status") or "").strip().lower() in TASK_TERMINAL_STATUSES:
                    return
                logger.info(
                    "Autonomous runtime callback received | task_id=%s | attempt_id=%s | runtime_task_id=%s | status=%s",
                    task_id,
                    attempt_id,
                    _runtime_task_id,
                    str((runtime_task_snapshot or {}).get("status") or "").strip().lower() or "unknown",
                )
                task_log.finalize_attempt_from_runtime(
                    task_id,
                    attempt_id,
                    lease_id,
                    dict(runtime_task_snapshot or {}),
                )
            except Exception:
                logger.exception(
                    "Failed to finalize autonomous runtime callback | task_id=%s | attempt_id=%s",
                    task_id,
                    attempt_id,
                )

        runtime.on_task_complete(subagent_task_id, _finalize_runtime_attempt_callback)

        while True:
            task_log.heartbeat_attempt(task_id, attempt_id)
            if session_lease_id:
                lease_alive = task_log.heartbeat_session_lease(
                    normalized_session_date,
                    session_lease_id,
                    ttl_seconds=session_lease_timeout_seconds,
                )
                if not lease_alive:
                    logger.warning(
                        "Autonomous session lease lost during task execution | session_date=%s | lease_id=%s | task_id=%s | attempt_id=%s | runtime_task_id=%s",
                        normalized_session_date,
                        session_lease_id,
                        task_id,
                        attempt_id,
                        subagent_task_id,
                    )
                    latest_task = task_log.get_task(task_id) or task
                    try:
                        runtime.cancel_task(
                            subagent_task_id,
                            reason="Autonomous session lease lost.",
                        )
                    except Exception:
                        logger.exception(
                            "Failed to cancel autonomous subagent after session lease loss | task_id=%s | attempt_id=%s | runtime_task_id=%s",
                            task_id,
                            attempt_id,
                            subagent_task_id,
                        )
                    task_log.mark_task_paused_pending_attempt_exit(
                        task_id,
                        attempt_id,
                        reason="session_lease_lost",
                        resume_snapshot=str(
                            latest_task.get("execution_log")
                            or latest_task.get("resume_snapshot")
                            or ""
                        ),
                    )
                    return task_log.get_task(task_id)
            if attempt_caller_prefix:
                _sync_task_usage_delta_from_logs(
                    task_log,
                    session_date=normalized_session_date,
                    task_id=task_id,
                    caller_prefix=attempt_caller_prefix,
                    synced_usage=synced_usage,
                )

            if interrupt_event.is_set():
                logger.warning(
                    "Autonomous task interrupted by user | task_id=%s | attempt_id=%s | runtime_task_id=%s",
                    task_id,
                    attempt_id,
                    subagent_task_id,
                )
                task_log.request_task_interrupt(task_id, attempt_id, reason="user_interrupt")
                runtime.cancel_task(
                    subagent_task_id,
                    reason="Autonomous mode interrupted by user.",
                )
                confirmed = _wait_for_subagent_exit(
                    runtime,
                    subagent_task_id,
                    timeout=cancel_wait_seconds,
                    poll_interval=execution_config["poll_interval_seconds"],
                )
                if confirmed:
                    latest_task = task_log.get_task(task_id) or task
                    task_log.mark_task_paused(
                        task_id,
                        attempt_id,
                        reason="user_interrupt",
                        resume_snapshot=str(
                            latest_task.get("execution_log")
                            or latest_task.get("resume_snapshot")
                            or ""
                        ),
                    )
                    logger.info(
                        "Autonomous task paused after confirmed user interrupt | task_id=%s | attempt_id=%s",
                        task_id,
                        attempt_id,
                    )
                else:
                    latest_task = task_log.get_task(task_id) or task
                    task_log.mark_task_paused_pending_attempt_exit(
                        task_id,
                        attempt_id,
                        reason="user_interrupt",
                        resume_snapshot=str(
                            latest_task.get("execution_log")
                            or latest_task.get("resume_snapshot")
                            or ""
                        ),
                    )
                    logger.warning(
                        "Autonomous task pause pending runtime exit after user interrupt | task_id=%s | attempt_id=%s | runtime_task_id=%s",
                        task_id,
                        attempt_id,
                        subagent_task_id,
                    )
                return task_log.get_task(task_id)

            can_continue_session, session_reason = token_counter.check_session_budget(
                task_log,
                normalized_session_date,
            )
            can_continue_task, task_reason = token_counter.check_task_budget(task_log, task_id)
            if not can_continue_session or not can_continue_task:
                budget_reason = session_reason or task_reason or "token_limit"
                logger.warning(
                    "Autonomous task reached token budget | task_id=%s | attempt_id=%s | budget_reason=%s",
                    task_id,
                    attempt_id,
                    budget_reason,
                )
                task_log.request_task_interrupt(task_id, attempt_id, reason="token_limit")
                runtime.cancel_task(
                    subagent_task_id,
                    reason=f"Autonomous task reached token limit: {budget_reason}",
                )
                confirmed = _wait_for_subagent_exit(
                    runtime,
                    subagent_task_id,
                    timeout=cancel_wait_seconds,
                    poll_interval=execution_config["poll_interval_seconds"],
                )
                if confirmed:
                    latest_task = task_log.get_task(task_id) or task
                    task_log.mark_task_paused(
                        task_id,
                        attempt_id,
                        reason="token_limit",
                        resume_snapshot=str(
                            latest_task.get("execution_log")
                            or latest_task.get("resume_snapshot")
                            or ""
                        ),
                    )
                    logger.info(
                        "Autonomous task paused after confirmed token-limit cancellation | task_id=%s | attempt_id=%s",
                        task_id,
                        attempt_id,
                    )
                else:
                    latest_task = task_log.get_task(task_id) or task
                    task_log.mark_task_paused_pending_attempt_exit(
                        task_id,
                        attempt_id,
                        reason="token_limit",
                        resume_snapshot=str(
                            latest_task.get("execution_log")
                            or latest_task.get("resume_snapshot")
                            or ""
                        ),
                    )
                    logger.warning(
                        "Autonomous task pause pending runtime exit after token-limit cancellation | task_id=%s | attempt_id=%s | runtime_task_id=%s",
                        task_id,
                        attempt_id,
                        subagent_task_id,
                    )
                return task_log.get_task(task_id)

            status_result = runtime.get_status(subagent_task_id)
            if not status_result.get("ok", False):
                logger.warning(
                    "Autonomous runtime status lookup failed | task_id=%s | attempt_id=%s | runtime_task_id=%s | error=%s",
                    task_id,
                    attempt_id,
                    subagent_task_id,
                    _preview_log_text(status_result.get("error")),
                )
                return task_log.fail_task(
                    task_id,
                    error=str(status_result.get("error") or "Unknown autonomous subagent task."),
                )
            task_data = dict(status_result.get("task") or {})
            subagent_status = str(task_data.get("status") or "").strip().lower()
            if subagent_status in TERMINAL_SUBAGENT_STATUSES:
                logger.info(
                    "Autonomous runtime reported terminal status | task_id=%s | attempt_id=%s | runtime_task_id=%s | status=%s",
                    task_id,
                    attempt_id,
                    subagent_task_id,
                    subagent_status,
                )
                return task_log.finalize_attempt_from_runtime(
                    task_id,
                    attempt_id,
                    lease_id,
                    task_data,
                )
            if subagent_status in UNSUPPORTED_AUTONOMOUS_WAITING_STATUSES:
                logger.warning(
                    "Autonomous runtime entered unsupported waiting state | task_id=%s | attempt_id=%s | runtime_task_id=%s | status=%s",
                    task_id,
                    attempt_id,
                    subagent_task_id,
                    subagent_status,
                )
                runtime.cancel_task(
                    subagent_task_id,
                    reason="Unsupported autonomous waiting state.",
                )
                return task_log.fail_task(
                    task_id,
                    error=(
                        "Autonomous agent entered unsupported waiting state: "
                        f"{subagent_status}"
                    ),
                )
            time.sleep(execution_config["poll_interval_seconds"])
    except Exception as exc:
        logger.exception("Autonomous task execution failed | task_id=%s", task_id)
        if attempt_id:
            current_task = task_log.get_task(task_id)
            if current_task and current_task.get("status") not in TASK_TERMINAL_STATUSES:
                return task_log.fail_task(task_id, error=str(exc))
        return task_log.fail_task(task_id, error=str(exc))
    finally:
        if attempt_caller_prefix:
            try:
                _sync_task_usage_delta_from_logs(
                    task_log,
                    session_date=normalized_session_date,
                    task_id=task_id,
                    caller_prefix=attempt_caller_prefix,
                    synced_usage=synced_usage,
                )
            except Exception:
                logger.exception(
                    "Failed to sync autonomous token usage from logs | task_id=%s | caller=%s",
                    task_id,
                    attempt_caller_prefix,
                )


def _wait_for_subagent_exit(
    subagent_runtime,
    subagent_task_id: str,
    *,
    timeout: float = DEFAULT_CANCEL_WAIT_SECONDS,
    poll_interval: float = 0.5,
) -> bool:
    """Wait until a sub-agent reaches terminal state after cancellation."""
    normalized_task_id = str(subagent_task_id or "").strip()
    if not normalized_task_id:
        return False
    normalized_timeout = max(0.0, float(timeout or 0.0))
    normalized_poll_interval = min(max(0.1, float(poll_interval or 0.5)), 2.0)
    deadline = time.time() + normalized_timeout
    while time.time() < deadline:
        status_result = subagent_runtime.get_status(normalized_task_id)
        if not status_result.get("ok", False):
            return False
        task_data = dict(status_result.get("task") or {})
        task_status = str(task_data.get("status") or "").strip().lower()
        if task_status in TERMINAL_SUBAGENT_STATUSES:
            return True
        time.sleep(normalized_poll_interval)
    return False
