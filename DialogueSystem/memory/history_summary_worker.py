"""后台摘要 worker：把原始对话历史提炼成记忆记录。

主对话进程会把 user / assistant 原始消息写进按话题分组的 `.jsonl` 文件。
这个 worker 会在后台轮询这些文件，挑出尚未总结的片段，让模型提炼成记忆后，
再写入记忆向量库。
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
import traceback
import unicodedata

import requests

try:
    import msvcrt
except ImportError:
    msvcrt = None

PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT_HINT = os.path.dirname(PACKAGE_ROOT)
if PROJECT_ROOT_HINT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT_HINT)

try:
    from DialogueSystem.config.paths import HISTORY_DIR, PROJECT_ROOT, SCRIPT_DIR
    from DialogueSystem.config.resources import load_prompt_text, render_prompt_text
except ImportError:
    from DialogueSystem.config.paths import HISTORY_DIR, PROJECT_ROOT, SCRIPT_DIR
    from DialogueSystem.config.resources import load_prompt_text, render_prompt_text
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from logging_utils import build_component_log_path, build_daily_log_path, cleanup_daily_log_files, configure_logger
from project_config import (
    get_model_select_task_config,
    get_model_select_model_key,
    get_project_config,
    get_qdrant_collection_config,
    load_project_config,
)

script_dir = SCRIPT_DIR
project_root = PROJECT_ROOT
BOOTSTRAP_LOG_COMPONENT = "history_summary_worker_bootstrap"
DEFAULT_WORKER_LOG_PATH = build_component_log_path(script_dir, "history_summary_worker")
DEFAULT_MEMORY_COLLECTION = get_qdrant_collection_config("memory", get_project_config())


def append_bootstrap_log(message: str, include_traceback: bool = False):
    """在完整日志系统尚未就绪前，尽力写一条 bootstrap 日志。"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    bootstrap_log_path = build_daily_log_path(script_dir, BOOTSTRAP_LOG_COMPONENT)
    try:
        cleanup_daily_log_files(script_dir, BOOTSTRAP_LOG_COMPONENT)
        with open(bootstrap_log_path, "a", encoding="utf-8") as file:
            file.write(f"{timestamp} | {message}\n")
            if include_traceback:
                file.write(traceback.format_exc())
                if not traceback.format_exc().endswith("\n"):
                    file.write("\n")
    except Exception:
        pass


try:
    from DialogueSystem.llm.CallingAPI import call_LLM
except Exception:
    try:
        from DialogueSystem.llm.CallingAPI import call_LLM
    except Exception:
        append_bootstrap_log("Failed to import call_LLM", include_traceback=True)
        raise

try:
    from MemorySystem.Qdrant import Qdrant
except Exception:
    append_bootstrap_log("Failed to import Qdrant", include_traceback=True)
    raise


FILE_PATTERN = re.compile(r"^(?P<prefix>.+)_(?P<group>\d+)\.jsonl$")
ALLOWED_TEXT_TYPES = {"Fact", "Emotion", "Requirement", "UserInformation", "Environment", "Disposable"}
MEMORY_DECISION_ACTIONS = {"insert_new", "skip_duplicate", "insert_with_conflict"}


def build_logger(log_path: str):
    """构造绑定到指定日志文件的 worker logger。"""
    return configure_logger("history_summary_worker", log_path)

def load_state(state_path: str):
    """读取 worker 状态文件；若不存在或损坏，则返回空状态。"""
    if not os.path.exists(state_path):
        return {"files": {}, "updated_at": time.time()}
    try:
        with open(state_path, "r", encoding="utf-8") as file:
            state = json.load(file)
    except Exception:
        return {"files": {}, "updated_at": time.time()}
    if not isinstance(state, dict):
        return {"files": {}, "updated_at": time.time()}
    state.setdefault("files", {})
    return state


def save_state(state_path: str, state: dict):
    """原子化保存 worker 状态，降低中途中断导致半写入的风险。"""
    state["updated_at"] = time.time()
    temp_path = state_path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
    os.replace(temp_path, state_path)


def acquire_single_instance_lock(lock_path: str):
    """获取单实例锁，确保同一时刻只有一个摘要 worker 在运行。"""
    if msvcrt is None:
        return open(lock_path, "a+", encoding="utf-8")
    lock_file = open(lock_path, "a+", encoding="utf-8")
    try:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock_file.close()
        return None
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def group_history_files(history_dir: str):
    """按会话前缀对话题 jsonl 文件分组，并按 group 顺序排序。"""
    grouped = {}
    if not os.path.isdir(history_dir):
        return grouped
    for name in os.listdir(history_dir):
        if not name.endswith(".jsonl"):
            continue
        match = FILE_PATTERN.match(name)
        if match:
            group = int(match.group("group"))
            prefix = match.group("prefix")
        else:
            # Backward compatibility for old naming style: raw_dialogue_xxx.jsonl
            group = 0
            prefix = os.path.splitext(name)[0]
        grouped.setdefault(prefix, []).append((group, name, os.path.join(history_dir, name)))
    for prefix in grouped:
        grouped[prefix].sort(key=lambda item: item[0])
    return grouped


def parse_topic_file_identity(file_name: str):
    """从历史文件名中解析会话前缀与 topic_group。"""
    base_name = os.path.basename(str(file_name or ""))
    match = FILE_PATTERN.match(base_name)
    if match:
        return match.group("prefix"), int(match.group("group"))
    return os.path.splitext(base_name)[0], 0


def read_lines(path: str):
    """用 UTF-8 读取历史 jsonl 文件的全部行。"""
    with open(path, "r", encoding="utf-8") as file:
        return file.readlines()


def collect_unsummarized_records(raw_lines, logger: logging.Logger, file_name: str):
    """提取尚未被总结的 user / assistant 记录。"""
    unsummarized = []
    for line_no, line in enumerate(raw_lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except Exception:
            logger.warning("Skip invalid json line | file=%s | line=%s", file_name, line_no)
            continue
        role = payload.get("role")
        content = payload.get("content")
        if role not in {"user", "assistant"}:
            continue
        if content is None:
            continue
        # 只有未总结的消息才会进入本轮总结队列。
        if payload.get("memory_summarized") is True:
            continue
        unsummarized.append((line_no - 1, {"role": role, "content": str(content)}))
    return unsummarized


def mark_lines_summarized(file_path: str, line_indices, logger: logging.Logger):
    """把指定行标记为已总结，并重写对应文件。"""
    if not line_indices:
        return False
    try:
        lines = read_lines(file_path)
    except FileNotFoundError:
        return False

    changed = False
    file_name = os.path.basename(file_path)
    for line_idx in sorted(set(line_indices)):
        if line_idx < 0 or line_idx >= len(lines):
            continue
        stripped = lines[line_idx].strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except Exception:
            logger.warning("Skip mark summarized for invalid json line | file=%s | line=%s", file_name, line_idx + 1)
            continue
        if payload.get("memory_summarized") is True:
            continue
        payload["memory_summarized"] = True
        lines[line_idx] = json.dumps(payload, ensure_ascii=False) + "\n"
        changed = True

    if not changed:
        return False
    tmp_path = file_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        file.writelines(lines)
    os.replace(tmp_path, file_path)
    return True


def parse_json_response(raw_text: str):
    """解析 JSON 响应，并兼容模型在 JSON 外再包一层文本的情况。"""
    try:
        return json.loads(raw_text)
    except Exception:
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw_text[start:end + 1])
        raise


def summarize_records(
    records,
    summary_prompt: str,
    model_key: str,
    session: requests.Session,
    reasoning_effort: str = "high",
    caller: str = "history_summary",
):
    """调用 LLM，把一批原始对话记录提炼成记忆列表。"""
    messages = [
        {"role": "system", "content": summary_prompt},
        {"role": "user", "content": json.dumps(records, ensure_ascii=False)}
    ]
    raw_result = call_LLM(
        messages,
        model_key,
        session,
        True,
        True,
        caller=caller,
        reasoning_effort=reasoning_effort,
    )
    parsed = parse_json_response(raw_result)
    memories = parsed.get("memories", [])
    if not isinstance(memories, list):
        return []
    return memories


def normalize_memory(item):
    """校验并规整模型返回的一条记忆对象。"""
    if not isinstance(item, dict):
        return None
    text = str(item.get("text", "")).strip()
    personalized_text = str(item.get("personalizedText", "")).strip()
    if not text or not personalized_text:
        return None
    text_type = str(item.get("textType", "Fact")).strip() or "Fact"
    if text_type not in ALLOWED_TEXT_TYPES:
        text_type = "Fact"
    if text_type == "Disposable":
        return None
    try:
        importance = float(item.get("importance", 0.5))
    except (TypeError, ValueError):
        importance = 0.5
    importance = max(0.0, min(1.0, importance))
    try:
        ttl = int(float(item.get("ttl", 30)))
    except (TypeError, ValueError):
        ttl = 30
    ttl = max(7, min(600, ttl))
    memory_dedupe_key = build_memory_dedupe_key(
        text=text,
        personalized_text=personalized_text,
        text_type=text_type
    )
    return {
        "text": text,
        "personalizedText": personalized_text,
        "textType": text_type,
        "importance": importance,
        "ttl": ttl,
        "memory_dedupe_key": memory_dedupe_key
    }


def canonicalize_memory_text(value: str) -> str:
    """为去重做文本归一化，尽量消除表层格式差异。"""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" \t\r\n,，。！？!?；;：:、")


def build_memory_dedupe_key(*, text: str, personalized_text: str, text_type: str) -> str:
    """为候选记忆构造稳定指纹，用于去重。"""
    canonical_payload = {
        "text": canonicalize_memory_text(text),
        "personalizedText": canonicalize_memory_text(personalized_text),
        "textType": canonicalize_memory_text(text_type) or "Fact"
    }
    serialized = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _normalize_record_ids(values):
    normalized_ids = []
    seen_ids = set()
    pending_values = list(values or [])
    while pending_values:
        value = pending_values.pop(0)
        if isinstance(value, (list, tuple, set)):
            pending_values[:0] = list(value)
            continue
        try:
            parsed_value = int(value)
        except (TypeError, ValueError):
            continue
        if parsed_value in seen_ids:
            continue
        seen_ids.add(parsed_value)
        normalized_ids.append(parsed_value)
    return normalized_ids


def serialize_memory_candidate(record):
    """把 Qdrant 命中项转成可供 LLM 判定的轻量结构。"""
    payload = record.payload or {}
    memory_timestamp = payload.get("timestamp")
    if memory_timestamp in (None, ""):
        memory_timestamp = payload.get("UpdateTime")
    return {
        "id": getattr(record, "id", None),
        "score": float(getattr(record, "score", 0.0)),
        "text": str(payload.get("text", "")).strip(),
        "personalizedText": str(payload.get("personalizedText", "")).strip(),
        "textType": str(payload.get("textType", "Fact") or "Fact"),
        "importance": payload.get("importance"),
        "ttl": payload.get("ttl"),
        "timestamp": memory_timestamp,
        "memory_status": str(payload.get("memory_status", "active") or "active"),
        "memory_status_detail": str(payload.get("memory_status_detail", "") or ""),
        "source": str(payload.get("source", "") or ""),
        "source_file": str(payload.get("source_file", "") or ""),
        "source_topic_group": payload.get("source_topic_group"),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "valid_from": payload.get("valid_from"),
        "valid_to": payload.get("valid_to"),
    }


def collect_candidate_existing_memories(
    normalized_memory: dict,
    vector,
    memory_qdrant: Qdrant,
    similarity_threshold: float,
):
    """从库中召回需要参与冲突判定的候选旧记忆。"""
    candidate_threshold = max(0.55, float(similarity_threshold) - 0.25)
    candidates = []
    seen_ids = set()
    for record in memory_qdrant.SearchRawScore(vector):
        record_id = getattr(record, "id", None)
        if record_id in seen_ids:
            continue
        seen_ids.add(record_id)
        payload = record.payload or {}
        memory_status = str(payload.get("memory_status", "active") or "active").strip().lower()
        memory_kind = str(payload.get("memory_kind", "") or "").strip().lower()
        if memory_status == "historical":
            continue
        if memory_kind == "topic_summary":
            continue
        same_fingerprint = payload.get("memory_dedupe_key") == normalized_memory["memory_dedupe_key"]
        score = float(getattr(record, "score", 0.0))
        if not same_fingerprint and score < candidate_threshold:
            continue
        candidates.append(record)
        if len(candidates) >= 5:
            break
    return candidates


def judge_memory_write_action(
    *,
    normalized_memory: dict,
    existing_records,
    model_key: str,
    session: requests.Session,
    reasoning_effort: str = "high",
    caller: str = "memory_conflict",
):
    """让 LLM 判断候选新记忆与旧记忆的关系。"""
    if not existing_records:
        return {
            "action": "insert_new",
            "duplicate_ids": [],
            "conflict_ids": [],
            "reason": "no_similar_existing_memory",
        }

    existing_candidates = [serialize_memory_candidate(record) for record in existing_records]
    valid_record_ids = {
        item["id"]
        for item in existing_candidates
        if item.get("id") is not None
    }
    user_payload = {
        "candidate_memory": normalized_memory,
        "existing_memories": existing_candidates,
    }

    try:
        conflict_prompt = load_prompt_text("MemoryConflict")
        raw_result = call_LLM(
            [
                {"role": "system", "content": conflict_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            model_key,
            session,
            True,
            True,
            caller=caller,
            reasoning_effort=reasoning_effort,
        )
        parsed = parse_json_response(raw_result)
    except Exception:
        parsed = {}

    action = str(parsed.get("action", "")).strip()
    if action not in MEMORY_DECISION_ACTIONS:
        action = ""
    duplicate_ids = [
        record_id
        for record_id in _normalize_record_ids(parsed.get("duplicate_ids"))
        if record_id in valid_record_ids
    ]
    conflict_ids = [
        record_id
        for record_id in _normalize_record_ids(parsed.get("conflict_ids"))
        if record_id in valid_record_ids
    ]
    if not action:
        if any(
            (record.payload or {}).get("memory_dedupe_key") == normalized_memory["memory_dedupe_key"]
            for record in existing_records
        ):
            action = "skip_duplicate"
            duplicate_ids = [
                int(getattr(record, "id"))
                for record in existing_records
                if getattr(record, "id", None) is not None
                and (record.payload or {}).get("memory_dedupe_key") == normalized_memory["memory_dedupe_key"]
            ]
        else:
            action = "insert_new"
    if action == "skip_duplicate":
        conflict_ids = []
    if action == "insert_with_conflict" and not conflict_ids:
        action = "insert_new"
    reason = str(parsed.get("reason", "")).strip() or "llm_judged"
    return {
        "action": action,
        "duplicate_ids": duplicate_ids,
        "conflict_ids": conflict_ids,
        "reason": reason,
    }


def mark_conflicting_memories_historical(
    *,
    memory_qdrant: Qdrant,
    conflict_ids,
    new_memory_id,
    reason: str,
    logger: logging.Logger,
):
    """把与新记忆冲突的旧记忆标记为 historical，而不是删除。"""
    normalized_conflict_ids = _normalize_record_ids(conflict_ids)
    if not normalized_conflict_ids:
        return
    now = time.time()
    memory_qdrant.client.set_payload(
        collection_name=memory_qdrant.CollectionName,
        payload={
            "memory_status": "historical",
            "memory_status_detail": "superseded",
            "superseded_at": now,
            "superseded_by": new_memory_id,
            "superseded_reason": str(reason or "").strip() or "conflict_update",
            "updated_at": now,
            "valid_to": now,
        },
        points=normalized_conflict_ids,
    )
    logger.info(
        "Historicalized conflicting memories | conflict_ids=%s | new_memory_id=%s",
        normalized_conflict_ids,
        new_memory_id,
    )


def persist_memories(
    memories,
    embedding_model,
    memory_qdrant: Qdrant,
    similarity_threshold: float,
    model_key: str,
    session: requests.Session,
    source_file: str,
    logger: logging.Logger,
    reasoning_effort: str = "high",
    source: str = "history_summary_worker",
    extra_payload_builder=None,
    conflict_caller_prefix: str = "memory_conflict",
):
    """对摘要记忆做 embedding、去重，并最终写入 Qdrant。"""
    source_session_prefix, source_topic_group = parse_topic_file_identity(source_file)
    inserted = 0
    skipped_duplicate = 0
    inserted_with_conflict = 0
    for memory_index, memory in enumerate(memories, start=1):
        normalized = normalize_memory(memory)
        if normalized is None:
            continue
        vector = embedding_model.encode(normalized["personalizedText"])
        existing_records = collect_candidate_existing_memories(
            normalized_memory=normalized,
            vector=vector,
            memory_qdrant=memory_qdrant,
            similarity_threshold=similarity_threshold,
        )
        decision = judge_memory_write_action(
            normalized_memory=normalized,
            existing_records=existing_records,
            model_key=model_key,
            session=session,
            reasoning_effort=reasoning_effort,
            caller=f"{conflict_caller_prefix}.{memory_index}",
        )
        if decision["action"] == "skip_duplicate":
            skipped_duplicate += 1
            continue
        extra_payload = {}
        if callable(extra_payload_builder):
            try:
                extra_payload = extra_payload_builder(memory, normalized) or {}
            except Exception:
                logger.exception(
                    "Failed to build extra payload for summary memory | file=%s | index=%s",
                    source_file,
                    memory_index,
                )
                extra_payload = {}
        if not isinstance(extra_payload, dict):
            extra_payload = {}
        for reserved_key in (
            "text",
            "personalizedText",
            "textType",
            "importance",
            "timestamp",
            "ttl",
            "initialTTL",
            "UpdateTime",
            "source",
            "source_file",
            "source_session_prefix",
            "source_topic_group",
            "memory_dedupe_key",
            "memory_status",
            "memory_status_detail",
            "memory_kind",
            "memory_relation_action",
            "memory_relation_reason",
            "duplicate_of_ids",
            "conflict_with_ids",
            "created_at",
            "updated_at",
            "valid_from",
            "valid_to",
        ):
            extra_payload.pop(reserved_key, None)
        now = time.time()
        _, inserted_id = memory_qdrant.add(
            normalized["text"],
            normalized["personalizedText"],
            vector,
            textType=normalized["textType"],
            importance=normalized["importance"],
            timestamp=now,
            ttl=normalized["ttl"],
            deduplication=False,
            memory_dedupe_key=normalized["memory_dedupe_key"],
            memory_status="active",
            memory_status_detail="active",
            memory_kind="atomic_memory",
            memory_relation_action=decision["action"],
            memory_relation_reason=decision["reason"],
            duplicate_of_ids=json.dumps(decision["duplicate_ids"], ensure_ascii=False),
            conflict_with_ids=json.dumps(decision["conflict_ids"], ensure_ascii=False),
            source_file=source_file,
            source_session_prefix=source_session_prefix,
            source_topic_group=source_topic_group,
            source=source,
            created_at=now,
            updated_at=now,
            valid_from=now,
            valid_to=None,
            **extra_payload,
        )
        if decision["action"] == "insert_with_conflict":
            mark_conflicting_memories_historical(
                memory_qdrant=memory_qdrant,
                conflict_ids=decision["conflict_ids"],
                new_memory_id=inserted_id,
                reason=decision["reason"],
                logger=logger,
            )
            inserted_with_conflict += 1
        inserted += 1
    logger.info(
        "Persisted summary memories | file=%s | inserted=%s | skipped_duplicate=%s | inserted_with_conflict=%s | dedupe_strategy=llm_judge",
        source_file,
        inserted,
        skipped_duplicate,
        inserted_with_conflict,
    )
    return inserted, skipped_duplicate


def process_file(
    *,
    file_path: str,
    file_name: str,
    is_active_file: bool,
    state: dict,
    summary_prompt: str,
    model_key: str,
    session: requests.Session,
    embedding_model,
    memory_qdrant: Qdrant,
    similarity_threshold: float,
    active_idle_seconds: int,
    force_flush_idle_seconds: int,
    batch_lines: int,
    min_messages: int,
    logger: logging.Logger,
    reasoning_effort: str = "high",
):
    """处理单个历史文件，直到它不再有可立即总结的批次。"""
    try:
        idle_seconds = int(time.time() - os.path.getmtime(file_path))
    except FileNotFoundError:
        return False
    if is_active_file and idle_seconds < active_idle_seconds:
        return False

    changed = False
    while True:
        try:
            all_lines = read_lines(file_path)
        except FileNotFoundError:
            break

        unsummarized = collect_unsummarized_records(
            all_lines,
            logger=logger,
            file_name=file_name
        )
        if not unsummarized:
            break

        # 活跃文件在持续写入时，允许先攒一小批，避免频繁改写同一个 jsonl。
        if is_active_file and len(unsummarized) < min_messages and idle_seconds < force_flush_idle_seconds:
            break

        batch = unsummarized[:batch_lines]
        batch_line_indices = [idx for idx, _ in batch]
        batch_records = [record for _, record in batch]

        try:
            memories = summarize_records(
                records=batch_records,
                summary_prompt=summary_prompt,
                model_key=model_key,
                session=session,
                reasoning_effort=reasoning_effort,
            )
            persist_memories(
                memories=memories,
                embedding_model=embedding_model,
                memory_qdrant=memory_qdrant,
                similarity_threshold=similarity_threshold,
                model_key=model_key,
                session=session,
                source_file=file_name,
                logger=logger,
                reasoning_effort=reasoning_effort,
            )
        except Exception as e:
            logger.exception("Summary batch failed | file=%s | error=%s", file_name, e)
            break

        if mark_lines_summarized(file_path, batch_line_indices, logger):
            changed = True
        else:
            logger.warning("Failed to mark summarized lines | file=%s", file_name)
            break

        try:
            idle_seconds = int(time.time() - os.path.getmtime(file_path))
        except FileNotFoundError:
            break
        if is_active_file and idle_seconds < active_idle_seconds:
            break

    return changed


def process_once(
    *,
    history_dir: str,
    state: dict,
    summary_prompt: str,
    model_key: str,
    session: requests.Session,
    embedding_model,
    memory_qdrant: Qdrant,
    similarity_threshold: float,
    active_idle_seconds: int,
    force_flush_idle_seconds: int,
    batch_lines: int,
    min_messages: int,
    logger: logging.Logger,
    reasoning_effort: str = "high",
):
    """对所有分组后的历史文件执行一轮轮询处理。"""
    changed = False
    grouped_files = group_history_files(history_dir)
    for _, entries in grouped_files.items():
        max_group = max(group for group, _, _ in entries)
        for group, file_name, file_path in entries:
            file_changed = process_file(
                file_path=file_path,
                file_name=file_name,
                is_active_file=(group == max_group),
                state=state,
                summary_prompt=summary_prompt,
                model_key=model_key,
                session=session,
                embedding_model=embedding_model,
                memory_qdrant=memory_qdrant,
                similarity_threshold=similarity_threshold,
                active_idle_seconds=active_idle_seconds,
                force_flush_idle_seconds=force_flush_idle_seconds,
                batch_lines=batch_lines,
                min_messages=min_messages,
                logger=logger,
                reasoning_effort=reasoning_effort,
            )
            if file_changed:
                changed = True
    return changed


def build_parser():
    """构造 detached worker 的命令行参数解析器。"""
    parser = argparse.ArgumentParser(description="Background history summary worker")
    parser.add_argument("--model", default=None, help="model key used by call_LLM")
    parser.add_argument("--history-dir", default=HISTORY_DIR)
    parser.add_argument("--state-path", default=os.path.join(HISTORY_DIR, ".summary_memory_state.json"))
    parser.add_argument("--lock-path", default=os.path.join(HISTORY_DIR, ".summary_memory_worker.lock"))
    parser.add_argument("--log-path", default=DEFAULT_WORKER_LOG_PATH)
    parser.add_argument("--poll-interval", type=int, default=20)
    parser.add_argument("--active-idle-seconds", type=int, default=90)
    parser.add_argument("--force-flush-idle-seconds", type=int, default=600)
    parser.add_argument("--batch-lines", type=int, default=24)
    parser.add_argument("--min-messages", type=int, default=2)
    parser.add_argument("--similarity-threshold", type=float, default=0.9)
    parser.add_argument("--embedding-model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--memory-collection", default=DEFAULT_MEMORY_COLLECTION["name"])
    parser.add_argument("--vector-size", type=int, default=DEFAULT_MEMORY_COLLECTION["vector_size"])
    return parser


def main():
    """后台历史摘要 worker 的主循环。"""
    args = build_parser().parse_args()
    for target in (args.log_path, args.state_path, args.lock_path):
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
    logger = build_logger(args.log_path)
    logger.info("Bootstrap | executable=%s | cwd=%s", sys.executable, os.getcwd())
    lock = acquire_single_instance_lock(args.lock_path)
    if lock is None:
        logger.info("Another history summary worker is running. Exit current process.")
        return

    config = load_project_config()
    summary_task_config = get_model_select_task_config("SummaryAndMermory", config)
    model_key = args.model or summary_task_config.get("model") or get_model_select_model_key("SummaryAndMermory", config)
    reasoning_effort = summary_task_config.get("reasoning_effort", "high")
    if not model_key:
        logger.error("ModelSelect.SummaryAndMermory is not configured, worker exit.")
        return

    prompt = render_prompt_text("SummaryMermory")
    state = load_state(args.state_path)

    session = requests.Session()
    memory_qdrant = Qdrant(args.memory_collection, size=args.vector_size, session=session)
    memory_qdrant.createDB()
    logger.info("Loading embedding dependencies | model=%s", args.embedding_model)
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        append_bootstrap_log("Failed to import sentence_transformers", include_traceback=True)
        logger.error("sentence_transformers is not installed, worker exit.")
        return
    embedding_model = SentenceTransformer(args.embedding_model, local_files_only=True, device="cpu")
    embedding_model.encode(["warmup"])

    logger.info(
        "History summary worker started | model=%s | history_dir=%s | dedupe_strategy=fingerprint",
        model_key,
        args.history_dir
    )

    try:
        while True:
            changed = process_once(
                history_dir=args.history_dir,
                state=state,
                summary_prompt=prompt,
                model_key=model_key,
                session=session,
                embedding_model=embedding_model,
                memory_qdrant=memory_qdrant,
                similarity_threshold=args.similarity_threshold,
                active_idle_seconds=args.active_idle_seconds,
                force_flush_idle_seconds=args.force_flush_idle_seconds,
                batch_lines=args.batch_lines,
                min_messages=args.min_messages,
                logger=logger,
                reasoning_effort=reasoning_effort,
            )
            if changed:
                save_state(args.state_path, state)
            time.sleep(max(3, args.poll_interval))
    except KeyboardInterrupt:
        logger.info("History summary worker interrupted by keyboard.")
    finally:
        try:
            save_state(args.state_path, state)
        except Exception:
            pass
        try:
            memory_qdrant.close()
        except Exception:
            pass
        try:
            session.close()
        except Exception:
            pass
        try:
            if lock:
                lock.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        append_bootstrap_log("Unhandled exception in history_summary_worker", include_traceback=True)
        raise
