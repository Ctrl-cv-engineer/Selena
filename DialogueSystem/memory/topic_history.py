"""Topic history helpers extracted from ``DialogueSystem.main``."""

from __future__ import annotations

import json
import logging
import os
import time

from .ChatContext import clear_topic_working_memory_state

logger = logging.getLogger("DialogueSystem.main")

def _topic_file_path(self, topic_group: int) -> str:
    """根据话题组编号生成对应的 jsonl 历史文件路径。"""
    return self.ContextJsonPath + "_" + str(topic_group) + ".jsonl"

def _make_message_record(self, role: str, content: str):
    """构造带编号、角色、内容和时间戳的消息记录。"""
    return {
        "message_id": self._next_message_identifier(),
        "role": role,
        "content": str(content),
        "timestamp": time.time(),
        "memory_summarized": False
    }

def _append_display_only_conversation_message(self, content: str, role: str = "assistant"):
    """追加一条仅用于前端展示的消息，不写入 live context 或 TopicSame。"""
    normalized_content = str(content or "").strip()
    if not normalized_content:
        return None
    with self._runtime_lock:
        record = self._make_message_record(role, normalized_content)
        self._display_only_topic_records.setdefault(self.topicGroup, []).append(record)
        return dict(record)

def _append_record_to_topic_file(self, topic_group: int, record: dict):
    """将单条消息记录追加写入话题文件。"""
    with open(self._topic_file_path(topic_group), "a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")

def _rewrite_topic_file(self, topic_group: int):
    """按当前内存记录重写指定话题文件。"""
    with open(self._topic_file_path(topic_group), "w", encoding="utf-8") as file:
        for record in self._topic_records.get(topic_group, []):
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

def _safe_last_topic_index(last_topic_index, total_count: int) -> int:
    """将 lastTopicIndex 规范到合法整数范围内。"""
    try:
        parsed = int(last_topic_index)
    except (TypeError, ValueError):
        parsed = 1
    return max(1, min(parsed, total_count))

def _split_current_topic_by_last_index(self, last_topic_index):
    """按 TopicSame 返回的尾部索引拆分当前话题。"""
    current_group = self.topicGroup
    current_records = self._topic_records.get(current_group, [])
    total_count = len(current_records)
    if total_count == 0:
        return None

    tail_count = self._safe_last_topic_index(last_topic_index, total_count)
    split_index = total_count - tail_count
    old_topic_records = [dict(item) for item in current_records[:split_index]]
    new_topic_records = [dict(item) for item in current_records[split_index:]]
    archive_task = None
    if old_topic_records:
        archive_task = {
            "topic_group": current_group,
            "records": [dict(item) for item in old_topic_records],
            "source_file": os.path.basename(self._topic_file_path(current_group)),
        }

    self._topic_records[current_group] = old_topic_records
    self.topicGroup = current_group + 1
    self._topic_records[self.topicGroup] = new_topic_records
    self._topic_same_messages = [
        {
            "role": str(item.get("role", "assistant") or "assistant"),
            "content": str(item.get("content", "")),
        }
        for item in new_topic_records
    ]
    retained_message_ids = {
        item.get("message_id")
        for item in new_topic_records
    }
    moved_context_count = 0
    removed_context_count = 0
    with self._persistent_context_lock:
        next_persistent_contexts = []
        for context_record in self._persistent_system_contexts:
            if context_record.get("topic_group") != current_group:
                next_persistent_contexts.append(context_record)
                continue
            if context_record.get("anchor_message_id") in retained_message_ids:
                updated_record = dict(context_record)
                updated_record["topic_group"] = self.topicGroup
                next_persistent_contexts.append(updated_record)
                moved_context_count += 1
                continue
            removed_context_count += 1
        self._persistent_system_contexts = next_persistent_contexts

    self._topic_same_messages = [
        {"role": item["role"], "content": item["content"]}
        for item in new_topic_records
    ]

    self._rewrite_topic_file(current_group)
    self._rewrite_topic_file(self.topicGroup)
    self._expire_retrieval_cache_topic(current_group)
    self._replace_context_memory_state(
        clear_topic_working_memory_state(self._context_memory_state),
        persist_persistent_core=False,
    )
    self._rebuild_live_contexts_for_active_topic(new_topic_records)
    self._context_revision += 1
    logger.info(
        "Topic switched | old_group=%s | new_group=%s | lastTopicIndex=%s | split_index=%s",
        current_group,
        self.topicGroup,
        tail_count,
        split_index
    )
    logger.info(
        "Persistent system contexts pruned after topic switch | old_group=%s | new_group=%s | moved=%s | removed=%s",
        current_group,
        self.topicGroup,
        moved_context_count,
        removed_context_count
    )
    return archive_task
