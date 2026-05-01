"""对话上下文辅助模块，负责静默监听、关键记忆维护与上下文摘要。"""

import asyncio
import json
import logging
import queue
import re
import threading
import time
import uuid

from project_config import get_character_config, get_model_select_task_config, get_project_config

try:
    from ..llm.CallingAPI import call_LLM
    from ..config.resources import render_prompt_text
except ImportError:
    from DialogueSystem.llm.CallingAPI import call_LLM
    from DialogueSystem.config.resources import render_prompt_text


config = get_project_config()
logger = logging.getLogger(__name__)

CORE_MEMORY_HEADER = "【关键记忆】"
RECENT_EXPERIENCES_HEADER_TEMPLATE = "【{char_name}的近期经历与感受】"


def _resolve_recent_experiences_header() -> str:
    """从配置读取角色名构造 header，未配置时回退到通用文案。"""
    try:
        char_name = (get_character_config(config) or {}).get("char_name") or ""
        char_name = str(char_name).strip()
    except Exception:
        char_name = ""
    if char_name:
        return RECENT_EXPERIENCES_HEADER_TEMPLATE.format(char_name=char_name)
    return "【近期经历与感受】"


RECENT_EXPERIENCES_HEADER = _resolve_recent_experiences_header()
RECENT_EXPERIENCES_SECTION_KEY = "recent_experiences"
SPECIAL_PERSISTED_SECTION_KEYS = (
    RECENT_EXPERIENCES_SECTION_KEY,
)
DEFAULT_CONTEXT_MEMORY_CONFIG = {
    "max_chars": 2200,
    "max_items_total": 18,
    "max_items_per_section": 4,
    "max_item_chars": 80,
    "recent_message_limit": 12,
}
CORE_MEMORY_SECTION_SPECS = (
    {
        "key": "user_profile",
        "label": "用户画像",
        "description": "只保留稳定、长期有效的用户事实、偏好、约束与背景。",
        "layer": "persistent_core",
        "preferred_items": 4,
        "priority": 5,
    },
    {
        "key": "current_context",
        "label": "当前工作集",
        "description": "保留当前阶段、环境、最近进展与此刻真正影响回答的上下文。",
        "layer": "topic_working_memory",
        "preferred_items": 3,
        "priority": 4,
    },
    {
        "key": RECENT_EXPERIENCES_SECTION_KEY,
        "label": "近期经历与感受",
        "description": "保留尚未在最近对话中被自然提起、适合继续分享的近期自主经历与感受。",
        "layer": "topic_working_memory",
        "preferred_items": 3,
        "priority": 3,
        "max_item_chars": 120,
    },
    {
        "key": "active_tasks",
        "label": "开放循环",
        "description": "保留尚未完成的任务、待解决问题、明确目标与后续承诺。",
        "layer": "persistent_core",
        "preferred_items": 4,
        "priority": 5,
    },
    {
        "key": "response_preferences",
        "label": "回复契约",
        "description": "保留用户明确表达的回复风格、长度、语气、禁忌与协作方式偏好。",
        "layer": "persistent_core",
        "preferred_items": 2,
        "priority": 3,
    },
    {
        "key": "relationship_signals",
        "label": "关系线索",
        "description": "仅保留会显著影响后续回复的情绪、关系或互动信号。",
        "layer": "topic_working_memory",
        "preferred_items": 1,
        "priority": 2,
    },
    {
        "key": "learned_procedures",
        "label": "操作经验",
        "description": "保留 Agent 成功执行过的操作快捷方式：关键 URL、工具调用序列、参数等，供下次同类任务直接复用。",
        "layer": "persistent_core",
        "preferred_items": 5,
        "priority": 4,
        "max_item_chars": 250,
    },
)
CORE_MEMORY_SECTIONS = tuple(
    (spec["key"], spec["label"])
    for spec in CORE_MEMORY_SECTION_SPECS
)
PERSISTENT_CORE_LAYER = "persistent_core"
TOPIC_WORKING_MEMORY_LAYER = "topic_working_memory"

# 主循环会把最新输入序号写入 `data_queue`。
# 静默监听线程只要观察到比启动基线更大的值，就说明用户已经有新输入了。
data_queue = queue.Queue()

# 任意时刻只允许有一个静默监听线程在运行。下面这些全局变量用于保存当前活动的
# 线程及其停止事件，便于新一轮监听安全地替换旧线程。
stop_thread = threading.Event()
silence_monitor_thread = None
silence_monitor_lock = threading.Lock()


def _get_context_memory_config() -> dict:
    """读取关键记忆块相关配置，并补齐默认值。"""
    configured = dict(config.get("ContextMemory", {}))
    merged = {**DEFAULT_CONTEXT_MEMORY_CONFIG, **configured}
    for key in DEFAULT_CONTEXT_MEMORY_CONFIG:
        try:
            merged[key] = int(merged[key])
        except (TypeError, ValueError):
            merged[key] = DEFAULT_CONTEXT_MEMORY_CONFIG[key]
    merged["max_chars"] = max(1800, merged["max_chars"])
    merged["max_items_total"] = max(4, merged["max_items_total"])
    merged["max_items_per_section"] = max(1, merged["max_items_per_section"])
    merged["max_item_chars"] = max(12, merged["max_item_chars"])
    merged["recent_message_limit"] = max(4, merged["recent_message_limit"])
    return merged


def build_default_core_memory_state() -> dict:
    """构造空的关键记忆结构。每个 section 是 dict item 列表（id+text+时间戳）。"""
    return {
        spec["key"]: []
        for spec in CORE_MEMORY_SECTION_SPECS
    }


CORE_MEMORY_HISTORY_LIMIT_PER_SECTION = 20


def _generate_core_memory_item_id(section_key: str) -> str:
    """为一条 core memory item 生成稳定的短 id。"""
    prefix = "".join(ch for ch in str(section_key or "") if ch.isalpha())[:3].lower() or "mem"
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _build_core_memory_item(section_key: str, text: str, *, now: float = None, item_id: str = None,
                            created_at: float = None, updated_at: float = None,
                            extra_fields: dict = None) -> dict:
    """构造一条 dict 形态的 core memory item。"""
    timestamp = float(now if now is not None else time.time())
    normalized_text = str(text or "").strip()
    item = {
        "id": str(item_id or _generate_core_memory_item_id(section_key)),
        "text": normalized_text,
        "created_at": float(created_at if created_at is not None else timestamp),
        "updated_at": float(updated_at if updated_at is not None else timestamp),
    }
    if isinstance(extra_fields, dict):
        for key, value in extra_fields.items():
            normalized_key = str(key or "").strip()
            if not normalized_key or normalized_key in item:
                continue
            item[normalized_key] = value
    return item


def _coerce_core_memory_item(raw_value, *, section_key: str, now: float = None) -> dict:
    """接受任意形态（旧 v1 的 str、dict、嵌套 dict），统一返回 dict item。"""
    if isinstance(raw_value, dict):
        text_value = raw_value.get("text")
        if text_value is None:
            text_value = raw_value.get("content")
        if text_value is None:
            text_value = raw_value.get("value")
        text = str(text_value or "").strip()
        if not text:
            return None
        extra_fields = {
            key: value
            for key, value in raw_value.items()
            if key not in {
                "id",
                "text",
                "content",
                "value",
                "created_at",
                "createdAt",
                "updated_at",
                "updatedAt",
            }
        }
        return _build_core_memory_item(
            section_key,
            text,
            now=now,
            item_id=str(raw_value.get("id") or "").strip() or None,
            created_at=raw_value.get("created_at") or raw_value.get("createdAt"),
            updated_at=raw_value.get("updated_at") or raw_value.get("updatedAt"),
            extra_fields=extra_fields,
        )
    text = str(raw_value or "").strip()
    if not text:
        return None
    return _build_core_memory_item(section_key, text, now=now)


def _get_core_memory_section_specs():
    """返回关键记忆各 section 的规格定义。"""
    return CORE_MEMORY_SECTION_SPECS


def get_core_memory_section_specs():
    """返回关键记忆 section 规格，供主流程和调试视图读取。"""
    return tuple(dict(spec) for spec in _get_core_memory_section_specs())


def _get_core_memory_section_specs_by_layer(layer: str):
    normalized_layer = str(layer or "").strip().lower()
    return tuple(
        spec
        for spec in _get_core_memory_section_specs()
        if str(spec.get("layer", "")).strip().lower() == normalized_layer
    )


def _copy_core_memory_state(raw_state: dict):
    if not isinstance(raw_state, dict):
        return build_default_core_memory_state()
    copied_state = build_default_core_memory_state()
    for spec in _get_core_memory_section_specs():
        copied_state[spec["key"]] = [dict(item) for item in (raw_state.get(spec["key"]) or []) if isinstance(item, dict)]
    return copied_state


def extract_persistent_core_memory_state(core_memory_state: dict) -> dict:
    """只保留跨程序重启的 sections。

    `recent_experiences` 虽然渲染时独立于 `【关键记忆】`，但需要跨重启保留，
    因此这里按 special persisted section 一并写入本地持久层。
    """
    normalized_state = normalize_core_memory_state(core_memory_state or {})
    persistent_keys = {
        spec["key"]
        for spec in _get_core_memory_section_specs_by_layer(PERSISTENT_CORE_LAYER)
    }
    persistent_keys.update(SPECIAL_PERSISTED_SECTION_KEYS)
    persistent_state = build_default_core_memory_state()
    for section_key in persistent_keys:
        persistent_state[section_key] = [dict(item) for item in (normalized_state.get(section_key) or [])]
    return persistent_state


def clear_topic_working_memory_state(
    core_memory_state: dict,
    *,
    preserve_recent_experiences: bool = True,
) -> dict:
    """清空 topic working memory sections，默认保留 `recent_experiences`。"""
    normalized_state = normalize_core_memory_state(core_memory_state or {})
    cleared_state = _copy_core_memory_state(normalized_state)
    for spec in _get_core_memory_section_specs_by_layer(TOPIC_WORKING_MEMORY_LAYER):
        if preserve_recent_experiences and spec["key"] == RECENT_EXPERIENCES_SECTION_KEY:
            continue
        cleared_state[spec["key"]] = []
    return normalize_core_memory_state(cleared_state)


def hydrate_core_memory_state(persistent_core_state: dict = None) -> dict:
    """用持久层初始化完整的上下文记忆状态。

    常规 persistent core sections 会被恢复；`recent_experiences` 作为独立 prompt
    的长期状态也会在启动时回填，其他 working memory sections 默认仍为空。
    """
    hydrated_state = build_default_core_memory_state()
    persistent_state = normalize_core_memory_state(persistent_core_state or {})
    for spec in _get_core_memory_section_specs_by_layer(PERSISTENT_CORE_LAYER):
        hydrated_state[spec["key"]] = [dict(item) for item in (persistent_state.get(spec["key"]) or [])]
    for section_key in SPECIAL_PERSISTED_SECTION_KEYS:
        hydrated_state[section_key] = [
            dict(item)
            for item in (persistent_state.get(section_key) or [])
            if isinstance(item, dict)
        ]
    return normalize_core_memory_state(hydrated_state)


def _normalize_core_memory_items(values, *, max_item_chars: int, section_key: str):
    """归一化单个 section 的 item 列表：去空、去重、裁剪、生成 id。"""
    normalized_items = []
    seen_texts = set()
    pending_values = list(values or [])
    while pending_values:
        value = pending_values.pop(0)
        if isinstance(value, (list, tuple, set)):
            pending_values[:0] = list(value)
            continue
        item = _coerce_core_memory_item(value, section_key=section_key)
        if not item:
            continue
        text = re.sub(r"\s+", " ", item["text"]).strip(" \t\r\n-•*")
        if not text:
            continue
        if len(text) > max_item_chars:
            text = text[: max_item_chars - 1].rstrip() + "…"
        if text in seen_texts:
            continue
        seen_texts.add(text)
        item["text"] = text
        normalized_items.append(item)
    return normalized_items


def render_core_memory_system_prompt(core_memory_state: dict = None) -> str:
    """把关键记忆状态渲染成可直接注入上下文的 system prompt。"""
    normalized_state = normalize_core_memory_state(core_memory_state or {})
    return _render_core_memory_system_prompt_no_recursion(normalized_state)


def render_recent_experiences_system_prompt(core_memory_state: dict = None) -> str:
    """把近期经历与感受渲染成独立的 system prompt。"""
    normalized_state = normalize_core_memory_state(core_memory_state or {})
    return _render_recent_experiences_system_prompt_no_recursion(normalized_state)


def normalize_core_memory_state(raw_state: dict, *, config_override: dict = None) -> dict:
    """归一化并裁剪关键记忆结构。输出形如 {section_key: [dict_item, ...]}。"""
    limits = dict(_get_context_memory_config())
    if config_override:
        limits.update(config_override)

    max_chars = limits["max_chars"]
    max_items_total = limits["max_items_total"]
    max_items_per_section = limits["max_items_per_section"]
    max_item_chars = limits["max_item_chars"]

    normalized_state = build_default_core_memory_state()
    if isinstance(raw_state, dict):
        for spec in _get_core_memory_section_specs():
            section_key = spec["key"]
            section_limit = min(max_items_per_section, int(spec["preferred_items"]))
            section_max_item_chars = int(spec.get("max_item_chars") or max_item_chars)
            normalized_state[section_key] = _normalize_core_memory_items(
                raw_state.get(section_key),
                max_item_chars=section_max_item_chars,
                section_key=section_key,
            )[:section_limit]

    total_items = sum(len(items) for items in normalized_state.values())
    if total_items > max_items_total:
        while total_items > max_items_total:
            removable_specs = [
                spec
                for spec in _get_core_memory_section_specs()
                if normalized_state.get(spec["key"])
            ]
            if not removable_specs:
                break
            removable_spec = min(
                removable_specs,
                key=lambda spec: (
                    int(spec["priority"]),
                    -len(normalized_state.get(spec["key"], [])),
                    spec["key"],
                ),
            )
            normalized_state[removable_spec["key"]].pop()
            total_items -= 1

    while len(_render_context_memory_prompt_bundle_no_recursion(normalized_state)) > max_chars:
        removable_specs = [
            spec
            for spec in _get_core_memory_section_specs()
            if normalized_state.get(spec["key"])
        ]
        if not removable_specs:
            break
        removable_spec = min(
            removable_specs,
            key=lambda spec: (
                int(spec["priority"]),
                -len(normalized_state.get(spec["key"], [])),
                spec["key"],
            ),
        )
        normalized_state[removable_spec["key"]].pop()

    return normalized_state


def _render_core_memory_system_prompt_no_recursion(core_memory_state: dict) -> str:
    """渲染紧凑版关键记忆 prompt，避免在长度裁剪阶段递归调用 normalize。"""
    body_lines = []
    layer_title_map = {
        PERSISTENT_CORE_LAYER: "持久核心记忆",
        TOPIC_WORKING_MEMORY_LAYER: "当前话题工作层",
    }
    for layer_key in (PERSISTENT_CORE_LAYER, TOPIC_WORKING_MEMORY_LAYER):
        layer_specs = _get_core_memory_section_specs_by_layer(layer_key)
        layer_lines = []
        for spec in layer_specs:
            section_key = spec["key"]
            if section_key == RECENT_EXPERIENCES_SECTION_KEY:
                continue
            section_items = core_memory_state.get(section_key) or []
            if not section_items:
                continue
            layer_lines.append(f"{spec['label']}:")
            for item in section_items:
                text = item.get("text", "") if isinstance(item, dict) else str(item or "")
                if not text:
                    continue
                layer_lines.append(f"- {text}")
            layer_lines.append("")
        if not layer_lines:
            continue
        body_lines.append(f"{layer_title_map[layer_key]}:")
        body_lines.extend(layer_lines)
    memory_body = "\n".join(body_lines).strip()
    if not memory_body:
        memory_body = "当前暂无已维护的关键记忆。"
    return render_prompt_text(
        "CoreMemorySystem",
        {"MEMORY_BODY": memory_body},
    )


def _render_recent_experiences_system_prompt_no_recursion(core_memory_state: dict) -> str:
    section_items = core_memory_state.get(RECENT_EXPERIENCES_SECTION_KEY) or []
    if not section_items:
        return ""
    body_lines = []
    for item in section_items:
        text = item.get("text", "") if isinstance(item, dict) else str(item or "")
        if not text:
            continue
        body_lines.append(f"- {text}")
    if not body_lines:
        return ""
    return render_prompt_text(
        "RecentExperiencesSystem",
        {"RECENT_EXPERIENCES_BODY": "\n".join(body_lines)},
    )


def _render_context_memory_prompt_bundle_no_recursion(core_memory_state: dict) -> str:
    prompt_texts = [
        _render_core_memory_system_prompt_no_recursion(core_memory_state),
        _render_recent_experiences_system_prompt_no_recursion(core_memory_state),
    ]
    return "\n\n".join(text for text in prompt_texts if str(text or "").strip())


def build_core_memory_system_message(core_memory_state: dict = None) -> dict:
    """构造关键记忆块对应的 system 消息。"""
    return {
        "role": "system",
        "content": render_core_memory_system_prompt(core_memory_state),
    }


def build_recent_experiences_system_message(core_memory_state: dict = None) -> dict | None:
    """构造近期经历与感受对应的独立 system 消息。"""
    prompt_text = render_recent_experiences_system_prompt(core_memory_state)
    if not str(prompt_text or "").strip():
        return None
    return {
        "role": "system",
        "content": prompt_text,
    }


def is_core_memory_system_message(message: dict) -> bool:
    """判断某条消息是否为关键记忆 system prompt。"""
    return (
        isinstance(message, dict)
        and str(message.get("role", "")).strip().lower() == "system"
        and str(message.get("content", "")).startswith(CORE_MEMORY_HEADER)
    )


def is_recent_experiences_system_message(message: dict) -> bool:
    """判断某条消息是否为近期经历与感受 system prompt。"""
    return (
        isinstance(message, dict)
        and str(message.get("role", "")).strip().lower() == "system"
        and str(message.get("content", "")).startswith(RECENT_EXPERIENCES_HEADER)
    )


def rebuild_context_from_topic(system_messages, topic_records):
    """基于固定 system prompt 与当前话题记录重建上下文。"""
    rebuilt_messages = [dict(message) for message in system_messages or []]
    for record in topic_records or []:
        rebuilt_messages.append(
            {
                "role": str(record.get("role", "assistant") or "assistant"),
                "content": str(record.get("content", "")),
            }
        )
    return rebuilt_messages


def reset_summary_context_state():
    """重置上下文摘要状态，通常在话题切换后调用。"""
    globals()["SummaryNum"] = 0


def _parse_json_response(raw_text: str):
    try:
        return json.loads(raw_text)
    except Exception:
        start = str(raw_text).find("{")
        end = str(raw_text).rfind("}")
        if start >= 0 and end > start:
            return json.loads(str(raw_text)[start:end + 1])
        raise


def _make_history_entry(section_key: str, item: dict, *, superseded_by: str, reason: str, now: float) -> dict:
    return {
        "id": str(item.get("id", "") or ""),
        "section": section_key,
        "text": str(item.get("text", "") or ""),
        "created_at": float(item.get("created_at") or now),
        "superseded_at": float(now),
        "superseded_by": superseded_by,
        "superseded_reason": str(reason or "").strip(),
    }


def prune_core_memory_history(history, *, limit_per_section: int = CORE_MEMORY_HISTORY_LIMIT_PER_SECTION):
    """每 section 只保留最近 N 条 superseded；整体按时间倒序输出。"""
    buckets = {}
    for entry in history or []:
        if not isinstance(entry, dict):
            continue
        section = str(entry.get("section", "") or "")
        buckets.setdefault(section, []).append(entry)
    pruned = []
    for entries in buckets.values():
        entries_sorted = sorted(
            entries,
            key=lambda e: float(e.get("superseded_at") or 0),
            reverse=True,
        )
        pruned.extend(entries_sorted[:limit_per_section])
    pruned.sort(key=lambda e: float(e.get("superseded_at") or 0), reverse=True)
    return pruned


def apply_core_memory_operations(
    core_memory_state: dict,
    history,
    operations,
    *,
    now: float = None,
):
    """把 LLM 返回的 operations 应用到 state，并把被替换/删除的条目归档到 history。

    每条 operation 结构：
      {"op": "ADD"|"UPDATE"|"DELETE"|"KEEP",
       "section": <section_key>,
       "id": <target_item_id, UPDATE/DELETE 必填>,
       "text": <new_text, ADD/UPDATE 必填>,
       "reason": <一句话原因, 可选>}
    """
    timestamp = float(now if now is not None else time.time())
    new_state = build_default_core_memory_state()
    valid_sections = set(new_state.keys())
    for section_key in valid_sections:
        new_state[section_key] = [
            dict(item)
            for item in (core_memory_state or {}).get(section_key, []) or []
            if isinstance(item, dict) and item.get("id") and item.get("text")
        ]
    new_history = list(history or [])

    for raw_op in operations or []:
        if not isinstance(raw_op, dict):
            continue
        action = str(raw_op.get("op", "") or "").strip().upper()
        section_key = str(raw_op.get("section", "") or "").strip()
        if section_key not in valid_sections:
            continue
        reason = str(raw_op.get("reason", "") or "").strip()

        if action == "ADD":
            text = str(raw_op.get("text", "") or "").strip()
            if not text:
                continue
            new_item = _build_core_memory_item(section_key, text, now=timestamp)
            new_state[section_key].append(new_item)
        elif action == "UPDATE":
            item_id = str(raw_op.get("id", "") or "").strip()
            text = str(raw_op.get("text", "") or "").strip()
            if not item_id or not text:
                continue
            target_index = next(
                (idx for idx, item in enumerate(new_state[section_key]) if item.get("id") == item_id),
                -1,
            )
            if target_index < 0:
                continue
            old_item = new_state[section_key][target_index]
            new_item = _build_core_memory_item(
                section_key,
                text,
                now=timestamp,
                created_at=old_item.get("created_at"),
                updated_at=timestamp,
            )
            new_state[section_key][target_index] = new_item
            new_history.append(
                _make_history_entry(
                    section_key,
                    old_item,
                    superseded_by=new_item["id"],
                    reason=reason or "updated",
                    now=timestamp,
                )
            )
        elif action == "DELETE":
            item_id = str(raw_op.get("id", "") or "").strip()
            if not item_id:
                continue
            target_index = next(
                (idx for idx, item in enumerate(new_state[section_key]) if item.get("id") == item_id),
                -1,
            )
            if target_index < 0:
                continue
            old_item = new_state[section_key].pop(target_index)
            new_history.append(
                _make_history_entry(
                    section_key,
                    old_item,
                    superseded_by="",
                    reason=reason or "deleted",
                    now=timestamp,
                )
            )
        # KEEP / 其他: no-op

    new_history = prune_core_memory_history(new_history)
    return normalize_core_memory_state(new_state), new_history


def _serialize_core_memory_state_for_prompt(core_memory_state: dict, *, section_keys=None) -> dict:
    """把 state 转成给 LLM 看的精简结构：只暴露 section → [{id, text}]。"""
    requested_section_keys = None
    if section_keys is not None:
        requested_section_keys = {
            str(section_key or "").strip()
            for section_key in list(section_keys or [])
            if str(section_key or "").strip()
        }
    simplified = {}
    for spec in _get_core_memory_section_specs():
        section_key = spec["key"]
        if requested_section_keys is not None and section_key not in requested_section_keys:
            continue
        simplified[section_key] = [
            {"id": item.get("id", ""), "text": item.get("text", "")}
            for item in (core_memory_state.get(section_key) or [])
            if isinstance(item, dict) and item.get("id") and item.get("text")
        ]
    return simplified


async def update_core_memory_state(
    core_memory_state: dict,
    topic_records,
    session,
    model_key: str,
    *,
    history=None,
    thinking: bool = True,
    json_mode: bool = True,
    reasoning_effort: str = "high",
):
    """基于当前关键记忆与最近话题内容，用 LLM 生成 operations 并应用。

    返回 `(new_state, new_history)`；若发生异常，会保留旧状态与旧 history。
    """
    normalized_current = normalize_core_memory_state(core_memory_state or {})
    current_history = list(history or [])
    if session is None or not model_key:
        return normalized_current, current_history

    limits = _get_context_memory_config()
    recent_records = list(topic_records or [])[-limits["recent_message_limit"]:]
    recent_dialogue = [
        {
            "role": str(record.get("role", "assistant") or "assistant"),
            "content": str(record.get("content", "")),
        }
        for record in recent_records
        if str(record.get("content", "")).strip()
    ]
    if not recent_dialogue:
        return normalized_current, current_history

    editable_section_keys = [
        spec["key"]
        for spec in _get_core_memory_section_specs()
        if spec["key"] not in {"learned_procedures", RECENT_EXPERIENCES_SECTION_KEY}
    ]
    system_prompt = render_prompt_text(
        "CoreMemoryUpdate",
        {
            "CURRENT_CORE_MEMORY_JSON": json.dumps(
                _serialize_core_memory_state_for_prompt(
                    normalized_current,
                    section_keys=editable_section_keys,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            "RECENT_DIALOGUE_JSON": json.dumps(
                recent_dialogue,
                ensure_ascii=False,
                indent=2,
            ),
            "MAX_ITEMS_PER_SECTION": limits["max_items_per_section"],
            "MAX_ITEMS_TOTAL": limits["max_items_total"],
            "MAX_ITEM_CHARS": limits["max_item_chars"],
        },
    )

    try:
        raw_result = await asyncio.to_thread(
            call_LLM,
            [{"role": "system", "content": system_prompt}],
            model_key,
            session,
            bool(thinking),
            bool(json_mode),
            caller="core_memory_update",
            reasoning_effort=reasoning_effort,
        )
        parsed = _parse_json_response(raw_result)
    except Exception:
        logger.exception("关键记忆维护失败，保留旧状态")
        return normalized_current, current_history

    operations = []
    if isinstance(parsed, dict):
        raw_ops = parsed.get("operations")
        if isinstance(raw_ops, list):
            operations = raw_ops
    elif isinstance(parsed, list):
        operations = parsed

    if not operations:
        return normalized_current, current_history

    return apply_core_memory_operations(
        normalized_current,
        current_history,
        operations,
    )


def _build_silence_system_message(seconds: int) -> str:
    """构造用户静默时注入给模型的系统提示词。"""
    return render_prompt_text(
        "SilenceFollowUp",
        {"SILENCE_SECONDS": seconds},
    ).strip()


def _get_silence_follow_up_task_config() -> dict:
    """读取静默追问 LLM 配置。"""
    return get_model_select_task_config(
        "SilenceFollowUpPrompt",
        config_data=get_project_config(),
    )


def _call_silence_follow_up(message_list: list, seconds: int, session, on_follow_up=None):
    """在达到静默阈值后追加提示词，并触发一次追问调用。"""
    task_config = _get_silence_follow_up_task_config()
    if not task_config.get("enabled", True):
        logger.info("Silence follow-up skipped | reason=disabled")
        return
    model_key = str(task_config.get("model", "") or "").strip()
    if not model_key:
        logger.info("Silence follow-up skipped | reason=model_not_configured")
        return
    silence_message = _build_silence_system_message(seconds)
    message_list.append({"role": "system", "content": silence_message})
    result = call_LLM(
        message_list,
        model_key,
        session,
        task_config.get("thinking", True),
        task_config.get("json_mode", False),
        caller="silence_follow_up",
        reasoning_effort=task_config.get("reasoning_effort", "high"),
    )
    normalized_result = str(result or "").strip()
    if normalized_result:
        message_list.append({"role": "assistant", "content": normalized_result})
    if callable(on_follow_up):
        try:
            on_follow_up(
                {
                    "seconds": seconds,
                    "system_prompt": silence_message,
                    "assistant_reply": normalized_result,
                }
            )
        except Exception:
            logger.exception("Silence follow-up callback failed")
    logger.info("静默追问触发 | seconds=%s | result=%s", seconds, result)


def SilenceContext(Message: list = None, InputNum: int = 0, session=None, on_follow_up=None):
    """启动或替换当前对话对应的后台静默监听线程。"""
    global stop_thread
    global silence_monitor_thread

    with silence_monitor_lock:
        if silence_monitor_thread and silence_monitor_thread.is_alive():
            stop_thread.set()
            silence_monitor_thread.join(timeout=1)

        stop_thread = threading.Event()
        silence_monitor_thread = threading.Thread(
            target=_run_silence_monitor,
            args=(stop_thread, Message, InputNum, session, on_follow_up),
            daemon=True,
        )
        silence_monitor_thread.start()


def _run_silence_monitor(stop_event: threading.Event, Message: list, InputNum: int, session, on_follow_up=None):
    """后台线程执行体：监听静默时长，并按阶段触发追问。"""
    timer = Timer()
    timer.start()
    sent_first_prompt = False
    sent_last_prompt = False
    latest_input_num = InputNum

    while not stop_event.is_set():
        latest_input_num = _drain_latest_input_num(latest_input_num)
        if latest_input_num > InputNum:
            logger.info("检测到用户新输入，停止当前静默监听")
            break

        elapsed = timer.get_elapsed()
        first_silence_seconds = config["SilentTime"]["First"]
        last_silence_seconds = config["SilentTime"]["Last"]

        if (not sent_first_prompt) and elapsed >= first_silence_seconds:
            logger.info("达到第一阶段静默阈值 | seconds=%s", first_silence_seconds)
            if Message is not None:
                _call_silence_follow_up(Message, first_silence_seconds, session, on_follow_up)
            sent_first_prompt = True

        if (not sent_last_prompt) and elapsed >= last_silence_seconds:
            logger.info("达到最终静默阈值 | seconds=%s", last_silence_seconds)
            if Message is not None:
                _call_silence_follow_up(Message, last_silence_seconds, session, on_follow_up)
            sent_last_prompt = True
            break

        time.sleep(0.2)


def _drain_latest_input_num(default_input_num: int) -> int:
    """从队列中取出当前最新的输入序号。"""
    latest = default_input_num
    while True:
        try:
            latest = data_queue.get_nowait()
        except queue.Empty:
            break
    return latest


async def SummaryContext(
    SimpleMessage: list,
    RolePlayMessage: list,
    Session,
    *,
    enabled: bool = True,
    model_key: str = None,
    thinking: bool = False,
    json_mode: bool = False,
    reasoning_effort: str = "high",
    summary_limits: dict = None,
    fixed_system_prefix_count: int | None = None,
):
    """当上下文过长时，对中间段做摘要收敛，防止上下文无限膨胀。"""
    global SummaryNum
    if not enabled:
        return SimpleMessage, RolePlayMessage
    effective_summary_config = dict(config.get("Summary", {}))
    if isinstance(summary_limits, dict):
        effective_summary_config.update(summary_limits)
    try:
        max_context = int(effective_summary_config.get("Max_context", 100))
    except (TypeError, ValueError):
        max_context = 100
    try:
        summary_context = int(effective_summary_config.get("Summary_context", 70))
    except (TypeError, ValueError):
        summary_context = 70
    max_context = max(4, max_context)
    summary_context = max(2, summary_context)
    resolved_model_key = model_key or "kimi"
    logger.info("ChatMessage 长度：%s", len(RolePlayMessage))
    if len(RolePlayMessage) <= max_context:
        return SimpleMessage, RolePlayMessage

    SummaryNum = globals().get("SummaryNum", 0)
    if fixed_system_prefix_count is None:
        leading_system_count = 0
        for message in RolePlayMessage:
            if str(message.get("role", "")).strip().lower() != "system":
                break
            leading_system_count += 1
        fixed_system_prefix_count = max(1, leading_system_count - max(0, int(SummaryNum or 0)))
    summary_anchor = fixed_system_prefix_count + SummaryNum
    summary_messages = RolePlayMessage[summary_anchor:summary_context]
    context_messages = RolePlayMessage[summary_context:]
    summary_prompt = render_prompt_text(
        "SummaryContext",
        {
            "SUMMARY_MESSAGES": json.dumps(
                summary_messages,
                ensure_ascii=False,
            )
        },
    )

    try:
        summary_text = await asyncio.to_thread(
            call_LLM,
            [{"role": "system", "content": summary_prompt}],
            resolved_model_key,
            Session,
            bool(thinking),
            bool(json_mode),
            caller="context_summary",
            reasoning_effort=reasoning_effort,
        )
    except Exception:
        logger.exception("上下文摘要失败，保留原始上下文")
        return SimpleMessage, RolePlayMessage

    summarized_message = {"role": "system", "content": summary_text}
    new_simple_message = SimpleMessage[0:summary_anchor] + [summarized_message] + context_messages
    new_role_play_message = RolePlayMessage[0:summary_anchor] + [summarized_message] + context_messages
    SummaryNum += 1
    return new_simple_message, new_role_play_message


class Timer:
    """供静默监听使用的轻量计时器。"""

    def __init__(self):
        self._start_time = None
        self._elapsed = 0.0
        self._is_running = False

    def start(self):
        """启动或继续计时。"""
        if not self._is_running:
            self._start_time = time.time()
            self._is_running = True

    def pause(self):
        """暂停计时，并累计当前已经过去的时长。"""
        if self._is_running:
            self._elapsed += time.time() - self._start_time
            self._is_running = False

    def reset(self):
        """清零并停止计时器。"""
        self._elapsed = 0.0
        self._start_time = None
        self._is_running = False

    def get_elapsed(self):
        """返回当前累计经过的秒数。"""
        if self._is_running:
            return self._elapsed + (time.time() - self._start_time)
        return self._elapsed
