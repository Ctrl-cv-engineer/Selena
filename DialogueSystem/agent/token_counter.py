"""Token estimation and budget checks for autonomous task mode."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from DialogueSystem.autonomous.autonomous_task_log import AutonomousTaskLog


logger = logging.getLogger(__name__)

DEFAULT_MAX_INPUT_PER_SESSION = 20000
DEFAULT_MAX_OUTPUT_PER_SESSION = 8000
DEFAULT_MAX_INPUT_PER_TASK = 8000
DEFAULT_MAX_OUTPUT_PER_TASK = 3000


def _safe_int(value, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _normalize_jsonish_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _get_llm_call_logs(since_id: int = 0) -> list:
    """Load LLM inspector logs lazily so this module stays import-friendly."""
    try:
        from DialogueSystem.llm.CallingAPI import get_llm_call_logs
    except Exception:
        try:
            from DialogueSystem.llm.CallingAPI import get_llm_call_logs
        except Exception:
            logger.debug("LLM call log provider unavailable when loading token usage.")
            return []
    try:
        return list(get_llm_call_logs(since_id=since_id) or [])
    except Exception:
        logger.exception("Failed to read LLM call logs.")
        return []


class TokenCounter:
    """Tracks token limits for autonomous sessions and tasks."""

    def __init__(self, config: dict):
        normalized_config = dict(config or {})
        self._max_input_per_session = max(
            0,
            _safe_int(
                normalized_config.get(
                    "max_input_tokens_per_session",
                    DEFAULT_MAX_INPUT_PER_SESSION,
                ),
                default=DEFAULT_MAX_INPUT_PER_SESSION,
            ),
        )
        self._max_output_per_session = max(
            0,
            _safe_int(
                normalized_config.get(
                    "max_output_tokens_per_session",
                    DEFAULT_MAX_OUTPUT_PER_SESSION,
                ),
                default=DEFAULT_MAX_OUTPUT_PER_SESSION,
            ),
        )
        self._max_input_per_task = max(
            0,
            _safe_int(
                normalized_config.get(
                    "max_input_tokens_per_task",
                    DEFAULT_MAX_INPUT_PER_TASK,
                ),
                default=DEFAULT_MAX_INPUT_PER_TASK,
            ),
        )
        self._max_output_per_task = max(
            0,
            _safe_int(
                normalized_config.get(
                    "max_output_tokens_per_task",
                    DEFAULT_MAX_OUTPUT_PER_TASK,
                ),
                default=DEFAULT_MAX_OUTPUT_PER_TASK,
            ),
        )

    @staticmethod
    def count_tokens(text: str) -> int:
        """Estimate tokens for arbitrary text-like content."""
        normalized_text = _normalize_jsonish_text(text)
        if not normalized_text:
            return 0

        try:
            import tiktoken

            try:
                encoding = tiktoken.encoding_for_model("gpt-4")
            except Exception:
                encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(normalized_text))
        except Exception:
            chinese_chars = sum(1 for char in normalized_text if "\u4e00" <= char <= "\u9fff")
            other_chars = len(normalized_text) - chinese_chars
            estimated = int(chinese_chars / 1.5 + other_chars / 4)
            return max(1, estimated)

    @classmethod
    def count_messages_tokens(cls, messages: list[dict]) -> int:
        """Estimate total input tokens for a messages payload."""
        total_tokens = 0
        for raw_message in list(messages or []):
            if not isinstance(raw_message, dict):
                total_tokens += cls.count_tokens(raw_message)
                continue

            total_tokens += 4
            for key in ("role", "name", "content", "tool_call_id"):
                if raw_message.get(key) not in (None, ""):
                    total_tokens += cls.count_tokens(raw_message.get(key))
            if raw_message.get("tool_calls"):
                total_tokens += cls.count_tokens(raw_message.get("tool_calls"))
            if raw_message.get("function_call"):
                total_tokens += cls.count_tokens(raw_message.get("function_call"))
        if total_tokens > 0:
            total_tokens += 2
        return total_tokens

    @staticmethod
    def get_actual_usage_from_logs(caller_prefix: str, since_id: int = 0) -> dict:
        """Aggregate actual token usage from the in-memory LLM call log."""
        normalized_prefix = str(caller_prefix or "").strip()
        if not normalized_prefix:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "tracked_calls": 0,
                "missing_usage_calls": 0,
            }

        total_input = 0
        total_output = 0
        tracked_calls = 0
        missing_usage_calls = 0
        for entry in _get_llm_call_logs(since_id=since_id):
            caller_name = str(entry.get("caller", "") or "").strip()
            if not caller_name.startswith(normalized_prefix):
                continue
            usage = dict((entry.get("extra") or {}).get("usage") or {})
            if usage:
                total_input += _safe_int(usage.get("prompt_tokens"), default=0)
                total_output += _safe_int(usage.get("completion_tokens"), default=0)
                tracked_calls += 1
            elif str(entry.get("status", "") or "").strip().lower() != "running":
                missing_usage_calls += 1
        return {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "tracked_calls": tracked_calls,
            "missing_usage_calls": missing_usage_calls,
        }

    @property
    def session_limits(self) -> dict:
        return {
            "max_input_tokens": self._max_input_per_session,
            "max_output_tokens": self._max_output_per_session,
        }

    @property
    def task_limits(self) -> dict:
        return {
            "max_input_tokens": self._max_input_per_task,
            "max_output_tokens": self._max_output_per_task,
        }

    @staticmethod
    def _check_budget_usage(
        *,
        input_tokens: int,
        output_tokens: int,
        max_input_tokens: int,
        max_output_tokens: int,
        reason_prefix: str,
    ) -> tuple[bool, str]:
        normalized_input = max(0, _safe_int(input_tokens, default=0))
        normalized_output = max(0, _safe_int(output_tokens, default=0))
        normalized_max_input = max(0, _safe_int(max_input_tokens, default=0))
        normalized_max_output = max(0, _safe_int(max_output_tokens, default=0))

        if normalized_max_input > 0 and normalized_input >= normalized_max_input:
            return False, f"{reason_prefix}_input_budget_exhausted"
        if normalized_max_output > 0 and normalized_output >= normalized_max_output:
            return False, f"{reason_prefix}_output_budget_exhausted"
        return True, ""

    def check_session_budget(
        self,
        task_log: AutonomousTaskLog,
        session_date: str,
    ) -> tuple[bool, str]:
        """Check whether the current autonomous day still has session budget."""
        session_record = task_log.get_or_create_daily_session(session_date) or {}
        return self._check_budget_usage(
            input_tokens=session_record.get("total_input_tokens", 0),
            output_tokens=session_record.get("total_output_tokens", 0),
            max_input_tokens=self._max_input_per_session,
            max_output_tokens=self._max_output_per_session,
            reason_prefix="session",
        )

    def check_task_budget(
        self,
        task_log: AutonomousTaskLog,
        task_id: int,
    ) -> tuple[bool, str]:
        """Check whether a single autonomous task still has task budget."""
        task_record = task_log.get_task(task_id)
        if task_record is None:
            return False, "task_not_found"
        return self._check_budget_usage(
            input_tokens=task_record.get("token_usage_input", 0),
            output_tokens=task_record.get("token_usage_output", 0),
            max_input_tokens=self._max_input_per_task,
            max_output_tokens=self._max_output_per_task,
            reason_prefix="task",
        )
