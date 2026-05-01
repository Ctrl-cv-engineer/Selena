"""Tool result rendering helpers extracted from ``DialogueSystem.main``."""

from __future__ import annotations

import os


SUMMARY_TOOL_NAME = "summarizeToolResults"
BROWSER_CONTEXT_PRIORITY_TERMS = (
    "search",
    "搜索",
    "login",
    "登录",
    "sign in",
    "submit",
    "提交",
    "confirm",
    "确认",
    "continue",
    "继续",
    "next",
    "save",
    "保存",
    "apply",
    "checkout",
    "pay",
    "download",
    "upload",
    "play",
    "pause",
    "close",
    "open",
)


def _build_visible_tool_step_title(self, tool_name: str, *, phase: str, tool_result=None) -> str:
    normalized_tool_name = str(tool_name or "").strip()
    if normalized_tool_name == "browserNavigate":
        return "正在打开页面" if phase == "tool_call" else "已打开页面"
    if normalized_tool_name == "browserOpenTab":
        return "正在打开标签页" if phase == "tool_call" else "已打开标签页"
    if normalized_tool_name == "browserSearch":
        return "正在搜索页面" if phase == "tool_call" else "已完成搜索"
    if normalized_tool_name == "browserSnapshot":
        return "正在读取页面快照" if phase == "tool_call" else "已读取页面快照"
    if normalized_tool_name == "browserExtractPage":
        return "正在提取页面内容" if phase == "tool_call" else "已提取页面内容"
    if normalized_tool_name == "browserReadLinkedPage":
        if phase == "tool_call":
            return "正在阅读链接页面"
        if isinstance(tool_result, dict) and bool(tool_result.get("requires_selection", False)):
            return "已返回链接候选"
        return "已读取链接页面"
    if normalized_tool_name == "browserClick":
        return "正在点击页面元素" if phase == "tool_call" else "已完成点击"
    if normalized_tool_name == "browserType":
        return "正在输入内容" if phase == "tool_call" else "已完成输入"
    if normalized_tool_name == "browserScroll":
        return "正在滚动页面" if phase == "tool_call" else "已滚动页面"
    if normalized_tool_name == "browserGoBack":
        return "正在返回上一页" if phase == "tool_call" else "已返回上一页"
    if normalized_tool_name == "browserListTabs":
        return "正在查看标签页" if phase == "tool_call" else "已获取标签页列表"
    if normalized_tool_name == "browserSelectTab":
        return "正在切换标签页" if phase == "tool_call" else "已切换标签页"
    if normalized_tool_name == "browserCloseTab":
        return "正在关闭标签页" if phase == "tool_call" else "已关闭标签页"
    if normalized_tool_name == "browserWait":
        return "正在等待页面变化" if phase == "tool_call" else "已等待页面变化"
    if normalized_tool_name == "browserPressKey":
        return "正在发送按键" if phase == "tool_call" else "已发送按键"
    if normalized_tool_name == "browserScreenshot":
        return "正在截取页面" if phase == "tool_call" else "已截取页面"
    if normalized_tool_name == "delegateTask":
        return "正在创建子 Agent 任务" if phase == "tool_call" else "已创建子 Agent 任务"
    if normalized_tool_name == "delegateTasksParallel":
        if phase == "tool_call":
            return "正在并行创建子 Agent 任务"
        partial_failure = bool((tool_result or {}).get("partial_failure", False)) if isinstance(tool_result, dict) else False
        return "已创建部分子 Agent 任务" if partial_failure else "已创建子 Agent 批次"
    if normalized_tool_name == "continueDelegatedTask":
        return "正在继续子 Agent 任务" if phase == "tool_call" else "已继续子 Agent 任务"
    if normalized_tool_name == "cancelDelegatedTask":
        return "正在取消子 Agent 任务" if phase == "tool_call" else "已取消子 Agent 任务"
    if normalized_tool_name == "getDelegatedTaskStatus":
        result_status = ""
        if isinstance(tool_result, dict):
            task_payload = tool_result.get("task")
            if isinstance(task_payload, dict):
                result_status = str(task_payload.get("status", "") or "").strip().lower()
            if not result_status:
                result_status = str(tool_result.get("status", "") or "").strip().lower()
        if phase == "tool_call":
            return "正在等待子 Agent 完成"
        if result_status == "completed":
            return "子 Agent 已完成"
        if result_status in {"failed", "error", "timeout", "timed_out"}:
            return "子 Agent 执行失败"
        if result_status == "waiting_approval":
            return "子 Agent 等待审批"
        if result_status == "waiting_input":
            return "子 Agent 等待补充输入"
        if result_status == "queued":
            return "子 Agent 排队中"
        if result_status == "cancelling":
            return "子 Agent 正在取消"
        if result_status == "cancelled":
            return "子 Agent 已取消"
        return "已更新子 Agent 状态"
    if normalized_tool_name == "waitForDelegatedTasks":
        if phase == "tool_call":
            return "正在等待子 Agent 批次完成"
        summary = (tool_result or {}).get("summary") if isinstance(tool_result, dict) else {}
        if not isinstance(summary, dict):
            summary = {}
        if bool(summary.get("wait_completed", False)):
            return "子 Agent 批次已完成"
        if bool(summary.get("waiting_for_external_input", False)):
            return "子 Agent 批次等待输入"
        if bool(summary.get("error_count", 0)):
            return "子 Agent 批次部分失败"
        return "已更新子 Agent 批次状态"
    if normalized_tool_name == SUMMARY_TOOL_NAME:
        return "正在整理工具结果" if phase == "tool_call" else "已整理工具结果"
    return f"正在调用 {normalized_tool_name}" if phase == "tool_call" else f"已完成 {normalized_tool_name}"


def _build_visible_tool_step_detail(self, tool_name: str, tool_result, *, function_args=None) -> str:
    normalized_tool_name = str(tool_name or "").strip()
    function_args = function_args if isinstance(function_args, dict) else {}
    if normalized_tool_name == "readAutonomousTaskArtifact" and isinstance(tool_result, dict):
        task_payload = tool_result.get("task") if isinstance(tool_result.get("task"), dict) else {}
        if task_payload:
            detail_parts = []
            task_title = str(task_payload.get("task_content", "") or "").strip()
            execution_log = str(task_payload.get("execution_log", "") or "").strip()
            if task_title:
                detail_parts.append(self._truncate_context_text(task_title, max_chars=80))
            if execution_log:
                detail_parts.append(self._truncate_multiline_context_text(execution_log, max_chars=120))
            elif task_payload.get("truncated") is True:
                detail_parts.append("已读取正文片段")
            return " · ".join(detail_parts)
    if normalized_tool_name in {"browserNavigate", "browserOpenTab"}:
        target_url = str(function_args.get("Url", "") or function_args.get("url", "") or "").strip()
        if isinstance(tool_result, dict):
            target_url = str(tool_result.get("url", "") or target_url).strip()
            page_title = str(tool_result.get("title", "") or "").strip()
            if page_title and target_url:
                return f"{page_title} · {target_url}"
        return target_url
    if normalized_tool_name == "browserSearch":
        search_query = str(function_args.get("Query", "") or function_args.get("query", "") or "").strip()
        return f"关键词：{search_query}" if search_query else ""
    if normalized_tool_name in {"browserSnapshot", "browserExtractPage"} and isinstance(tool_result, dict):
        page_title = str(tool_result.get("title", "") or "").strip()
        element_count = tool_result.get("element_count")
        snapshot_text = str(tool_result.get("snapshot", "") or tool_result.get("page_text", "") or "").strip()
        detail_parts = []
        if page_title:
            detail_parts.append(page_title)
        if element_count not in (None, ""):
            detail_parts.append(f"元素 {element_count} 个")
        if snapshot_text:
            detail_parts.append(self._truncate_context_text(snapshot_text, max_chars=120))
        return " · ".join(detail_parts)
    if normalized_tool_name == "browserReadLinkedPage":
        if isinstance(tool_result, dict):
            page_payload = tool_result.get("page")
            if isinstance(page_payload, dict):
                page_title = str(page_payload.get("title", "") or "").strip()
                page_text = str(page_payload.get("page_text", "") or page_payload.get("snapshot", "") or "").strip()
                detail_parts = []
                if page_title:
                    detail_parts.append(page_title)
                if page_text:
                    detail_parts.append(self._truncate_context_text(page_text, max_chars=120))
                return " · ".join(detail_parts)
            if bool(tool_result.get("requires_selection", False)):
                candidate_count = tool_result.get("candidate_count")
                if candidate_count not in (None, ""):
                    return f"候选链接 {candidate_count} 个"
        search_query = str(function_args.get("Query", "") or function_args.get("query", "") or "").strip()
        return f"关键词：{search_query}" if search_query else ""
    if normalized_tool_name == "browserClick":
        if isinstance(tool_result, dict):
            ref_name = str(tool_result.get("ref", "") or "").strip()
            page_title = str(tool_result.get("title", "") or "").strip()
            detail_parts = []
            if ref_name:
                detail_parts.append(f"元素 {ref_name}")
            if page_title:
                detail_parts.append(page_title)
            return " · ".join(detail_parts)
        ref_name = str(function_args.get("Ref", "") or function_args.get("ref", "") or "").strip()
        return f"元素 {ref_name}" if ref_name else ""
    if normalized_tool_name == "browserType":
        ref_name = str(function_args.get("Ref", "") or function_args.get("ref", "") or "").strip()
        text_value = str(function_args.get("Text", "") or function_args.get("text", "") or "").strip()
        detail_parts = []
        if ref_name:
            detail_parts.append(f"元素 {ref_name}")
        if text_value:
            detail_parts.append(self._truncate_context_text(text_value, max_chars=60))
        return " · ".join(detail_parts)
    if normalized_tool_name == "browserScroll":
        direction = str(function_args.get("Direction", "") or function_args.get("direction", "") or "").strip()
        amount = function_args.get("Amount", function_args.get("amount"))
        if direction and amount not in (None, ""):
            return f"{direction} {amount}px"
        return direction
    if normalized_tool_name == "browserListTabs" and isinstance(tool_result, dict):
        current_tab_id = str(tool_result.get("current_tab_id", "") or "").strip()
        tab_count = tool_result.get("tab_count")
        detail_parts = []
        if tab_count not in (None, ""):
            detail_parts.append(f"{tab_count} 个标签页")
        if current_tab_id:
            detail_parts.append(f"当前 {current_tab_id}")
        return " · ".join(detail_parts)
    if normalized_tool_name == "browserSelectTab":
        tab_id = str(
            (tool_result or {}).get("selected_tab_id", "")
            or function_args.get("TabId", "")
            or function_args.get("tab_id", "")
            or ""
        ).strip()
        page_title = str((tool_result or {}).get("title", "") or "").strip() if isinstance(tool_result, dict) else ""
        detail_parts = []
        if tab_id:
            detail_parts.append(f"标签页 {tab_id}")
        if page_title:
            detail_parts.append(page_title)
        return " · ".join(detail_parts)
    if normalized_tool_name == "browserCloseTab":
        closed_tab_id = str(
            (tool_result or {}).get("closed_tab_id", "")
            or function_args.get("TabId", "")
            or function_args.get("tab_id", "")
            or ""
        ).strip()
        tab_count = (tool_result or {}).get("tab_count") if isinstance(tool_result, dict) else None
        detail_parts = []
        if closed_tab_id:
            detail_parts.append(f"关闭 {closed_tab_id}")
        if tab_count not in (None, ""):
            detail_parts.append(f"剩余 {tab_count} 个")
        return " · ".join(detail_parts)
    if normalized_tool_name == "browserWait":
        matched = (tool_result or {}).get("matched") if isinstance(tool_result, dict) else None
        if matched:
            return " · ".join(str(item or "").strip() for item in matched if str(item or "").strip())
        wait_parts = []
        for key in ("Ref", "TextContains", "UrlContains", "TitleContains"):
            raw_value = str(function_args.get(key, "") or function_args.get(key.lower(), "") or "").strip()
            if raw_value:
                wait_parts.append(raw_value)
        return " · ".join(wait_parts)
    if normalized_tool_name == "browserPressKey":
        key_name = str(
            (tool_result or {}).get("key", "")
            or function_args.get("Key", "")
            or function_args.get("key", "")
            or ""
        ).strip()
        page_title = str((tool_result or {}).get("title", "") or "").strip() if isinstance(tool_result, dict) else ""
        detail_parts = []
        if key_name:
            detail_parts.append(key_name)
        if page_title:
            detail_parts.append(page_title)
        return " · ".join(detail_parts)
    if normalized_tool_name == "browserScreenshot":
        if isinstance(tool_result, dict):
            page_title = str(tool_result.get("title", "") or "").strip()
            screenshot_path = str(tool_result.get("screenshot_path", "") or "").strip()
            detail_parts = []
            if page_title:
                detail_parts.append(page_title)
            if screenshot_path:
                detail_parts.append(os.path.basename(screenshot_path))
            return " · ".join(detail_parts)
        return ""
    if normalized_tool_name == "delegateTask":
        if isinstance(tool_result, dict):
            task_payload = tool_result.get("task") if isinstance(tool_result.get("task"), dict) else {}
            task_text = str(task_payload.get("task", "") or function_args.get("Task", "") or "").strip()
            task_id = str(task_payload.get("task_id", "") or tool_result.get("task_id", "") or "").strip()
            task_status = str(task_payload.get("status", "") or "").strip()
            queue_position = task_payload.get("queue_position")
            cache_hit = bool(task_payload.get("cache_hit", False))
            detail_parts = []
            if task_text:
                detail_parts.append(self._truncate_context_text(task_text, max_chars=120))
            if task_id:
                detail_parts.append(f"ID {task_id}")
            if cache_hit:
                detail_parts.append("cache")
            if task_status:
                if task_status == "queued" and queue_position not in (None, ""):
                    detail_parts.append(f"queued #{queue_position}")
                else:
                    detail_parts.append(task_status)
            return " · ".join(detail_parts)
        return self._truncate_context_text(function_args.get("Task", ""), max_chars=120)
    if normalized_tool_name == "delegateTasksParallel":
        requested_count = 0
        raw_tasks = function_args.get("Tasks", [])
        if isinstance(raw_tasks, list):
            requested_count = len(raw_tasks)
        if isinstance(tool_result, dict):
            group_payload = tool_result.get("group") if isinstance(tool_result.get("group"), dict) else {}
            summary = tool_result.get("summary") if isinstance(tool_result.get("summary"), dict) else {}
            detail_parts = []
            group_label = str(group_payload.get("group_label", "") or summary.get("group_label", "") or "").strip()
            group_id = str(group_payload.get("group_id", "") or summary.get("group_id", "") or "").strip()
            created_count = tool_result.get("count")
            error_count = summary.get("error_count", 0)
            status_counts = summary.get("status_counts") if isinstance(summary.get("status_counts"), dict) else {}
            if group_label:
                detail_parts.append(group_label)
            if group_id:
                detail_parts.append(group_id)
            if created_count not in (None, ""):
                if requested_count > 0:
                    detail_parts.append(f"{created_count}/{requested_count} tasks")
                else:
                    detail_parts.append(f"{created_count} tasks")
            if status_counts:
                compact_status = ", ".join(
                    f"{key}:{value}" for key, value in sorted(status_counts.items()) if int(value or 0) > 0
                )
                if compact_status:
                    detail_parts.append(compact_status)
            if error_count not in (None, "") and int(error_count or 0) > 0:
                detail_parts.append(f"errors:{error_count}")
            return " · ".join(detail_parts)
        return f"{requested_count} tasks" if requested_count > 0 else ""
    if normalized_tool_name in {"continueDelegatedTask", "cancelDelegatedTask"}:
        task_id = str(function_args.get("TaskId", "") or "").strip()
        reason = str(function_args.get("UserReply", "") or function_args.get("ApprovalDecision", "") or function_args.get("Reason", "") or "").strip()
        detail_parts = []
        if task_id:
            detail_parts.append(f"ID {task_id}")
        if isinstance(tool_result, dict):
            task_payload = tool_result.get("task")
            if isinstance(task_payload, dict):
                task_status = str(task_payload.get("status", "") or "").strip()
                if task_status:
                    detail_parts.append(task_status)
                status_message = str(task_payload.get("status_message", "") or "").strip()
                if status_message:
                    detail_parts.append(self._truncate_context_text(status_message, max_chars=120))
        elif reason:
            detail_parts.append(self._truncate_context_text(reason, max_chars=120))
        return " · ".join(detail_parts)
    if normalized_tool_name == "getDelegatedTaskStatus":
        if isinstance(tool_result, dict):
            task_payload = tool_result.get("task")
            if isinstance(task_payload, dict):
                task_status = str(task_payload.get("status", "") or "").strip()
                task_result = str(task_payload.get("result", "") or "").strip()
                task_error = str(task_payload.get("error", "") or "").strip()
                awaiting = task_payload.get("awaiting") if isinstance(task_payload.get("awaiting"), dict) else {}
                awaiting_question = str(awaiting.get("question", "") or "").strip()
                if task_status and task_result:
                    return f"{task_status} · {self._truncate_context_text(task_result, max_chars=120)}"
                if task_status and task_error:
                    return f"{task_status} · {self._truncate_context_text(task_error, max_chars=120)}"
                if task_status and awaiting_question:
                    return f"{task_status} · {self._truncate_context_text(awaiting_question, max_chars=120)}"
                if task_status:
                    return task_status
        task_id = str(function_args.get("TaskId", "") or function_args.get("task_id", "") or "").strip()
        return f"任务 {task_id}" if task_id else ""
    if normalized_tool_name == "waitForDelegatedTasks":
        if isinstance(tool_result, dict):
            summary = tool_result.get("summary") if isinstance(tool_result.get("summary"), dict) else {}
            detail_parts = []
            group_label = str(summary.get("group_label", "") or "").strip()
            group_id = str(summary.get("group_id", "") or "").strip()
            if group_label:
                detail_parts.append(group_label)
            if group_id:
                detail_parts.append(group_id)
            requested_count = summary.get("requested_count")
            resolved_count = summary.get("resolved_count")
            if requested_count not in (None, "") and resolved_count not in (None, ""):
                detail_parts.append(f"{resolved_count}/{requested_count} tasks")
            status_counts = summary.get("status_counts") if isinstance(summary.get("status_counts"), dict) else {}
            if status_counts:
                compact_status = ", ".join(
                    f"{key}:{value}" for key, value in sorted(status_counts.items()) if int(value or 0) > 0
                )
                if compact_status:
                    detail_parts.append(compact_status)
            return " · ".join(detail_parts)
        raw_task_ids = function_args.get("TaskIds", [])
        if isinstance(raw_task_ids, list) and raw_task_ids:
            return f"{len(raw_task_ids)} tasks"
        return ""
    if normalized_tool_name == SUMMARY_TOOL_NAME:
        if isinstance(function_args, dict):
            summary_text = str(function_args.get("SummaryTools", "") or "").strip()
            return self._truncate_context_text(summary_text, max_chars=120)
        return ""
    if isinstance(tool_result, dict):
        error_text = str(tool_result.get("error", "") or "").strip()
        if error_text:
            return self._truncate_context_text(error_text, max_chars=120)
    return self._stringify_tool_result(tool_result, max_chars=120)


def _score_browser_element_for_prompt(element: dict) -> int:
    role = str(element.get("role", "") or "").strip().lower()
    tag = str(element.get("tag", "") or "").strip().lower()
    label = str(element.get("label", "") or "").strip()
    text = str(element.get("text", "") or "").strip()
    href = str(element.get("href", "") or "").strip()
    combined_text = f"{label} {text}".strip()
    score = 0
    if role in {"button", "link"}:
        score += 4
    if tag in {"button", "input", "textarea", "select"}:
        score += 3
    if href.startswith("javascript:"):
        score += 2
    if any(term.lower() in combined_text.lower() for term in BROWSER_CONTEXT_PRIORITY_TERMS):
        score += 8
    if combined_text:
        score += 1
    return score


def _compress_browser_elements_for_prompt(cls, elements, max_items: int = 24):
    """将浏览器元素列表压缩为紧凑的文本行格式，大幅减少 token 消耗。"""
    scored_elements = []
    for item in list(elements or []):
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref", "") or "").strip()
        if not ref:
            continue
        scored_elements.append((cls._score_browser_element_for_prompt(item), item))
    scored_elements.sort(key=lambda x: x[0], reverse=True)
    top_elements = [item for _, item in scored_elements[:max_items]]

    compact_lines = []
    for item in top_elements:
        ref = str(item.get("ref", "") or "").strip()
        role = str(item.get("role", "") or "").strip()
        tag = str(item.get("tag", "") or "").strip()
        label = str(item.get("label", "") or "").strip()[:80]
        text = str(item.get("text", "") or "").strip()[:60]
        href = str(item.get("href", "") or "").strip()
        disabled = bool(item.get("disabled", False))

        display_type = role or tag or "element"
        display_label = label or text or ""
        parts = [f"[{ref}] {display_type}"]
        if display_label:
            parts.append(f": {display_label}")
        if href and href != "javascript:;" and href != "javascript:void(0)" and href != "javascript:void(0);":
            short_href = href if len(href) <= 120 else href[:117] + "..."
            parts.append(f" (href='{short_href}')")
        if disabled:
            parts.append(" [disabled]")
        compact_lines.append("".join(parts))

    return compact_lines


def _compress_tool_result_payload(cls, tool_name: str, tool_result):
    if isinstance(tool_result, str):
        return cls._truncate_context_text(tool_result, max_chars=1200)
    if not isinstance(tool_result, dict):
        return tool_result

    normalized_tool_name = str(tool_name or "").strip()
    if normalized_tool_name in {"getDelegatedTaskStatus", "listDelegatedTasks", "delegateTasksParallel", "waitForDelegatedTasks"}:
        return tool_result
    if normalized_tool_name == "readAutonomousTaskArtifact":
        compressed = {}
        for key in ("ok", "error", "note"):
            if key not in tool_result:
                continue
            value = tool_result.get(key)
            if isinstance(value, str):
                compressed[key] = cls._truncate_context_text(value, max_chars=400)
            else:
                compressed[key] = value

        task_payload = tool_result.get("task")
        if isinstance(task_payload, dict):
            task_compressed = {}
            ordered_keys = (
                "task_id",
                "task_date",
                "status",
                "task_content",
                "expected_goal",
                "completed_at",
                "execution_log",
                "truncated",
                "execution_log_length",
                "attempt_count",
            )
            for key in ordered_keys:
                if key not in task_payload:
                    continue
                value = task_payload.get(key)
                if key == "execution_log" and isinstance(value, str):
                    task_compressed[key] = cls._truncate_multiline_context_text(value, max_chars=6000)
                elif isinstance(value, str):
                    max_chars = 600 if key in {"task_content", "expected_goal"} else 240
                    task_compressed[key] = cls._truncate_context_text(value, max_chars=max_chars)
                else:
                    task_compressed[key] = value
            compressed["task"] = task_compressed
        return compressed or tool_result

    if normalized_tool_name.startswith("browser"):
        important_keys = {
            "ok",
            "action",
            "url",
            "title",
            "ref",
            "clicked_ref",
            "tab_id",
            "tabId",
            "current_tab_id",
            "selected_tab_id",
            "query",
            "error",
            "errorCode",
            "element_count",
            "elements",
            "page_preview",
            "snapshot",
            "click_result",
            "page",
            "matched",
            "tabs",
            "tab_count",
            "screenshot_path",
        }
        compressed = {
            key: value
            for key, value in tool_result.items()
            if key in important_keys
        }
        original_elements = list(compressed.get("elements") or [])
        compact_element_lines = []
        if original_elements:
            compact_element_lines = cls._compress_browser_elements_for_prompt(original_elements)
            compressed["element_count"] = int(tool_result.get("element_count") or len(original_elements))
            compressed.pop("elements", None)
        snapshot_text = ""
        if isinstance(compressed.get("snapshot"), str):
            snapshot_text = cls._truncate_context_text(compressed["snapshot"], max_chars=1800)
        if compact_element_lines:
            elements_section = "\nInteractive elements:\n" + "\n".join(compact_element_lines)
            snapshot_text = (snapshot_text + elements_section) if snapshot_text else elements_section.lstrip("\n")
        if snapshot_text:
            compressed["snapshot"] = snapshot_text
        if isinstance(compressed.get("page_preview"), dict):
            preview_payload = dict(compressed["page_preview"])
            if isinstance(preview_payload.get("page_text"), str):
                preview_payload["page_text"] = cls._truncate_context_text(preview_payload["page_text"], max_chars=1600)
            compressed["page_preview"] = preview_payload
        if isinstance(compressed.get("page"), dict):
            page_payload = dict(compressed["page"])
            if isinstance(page_payload.get("page_text"), str):
                page_payload["page_text"] = cls._truncate_context_text(page_payload["page_text"], max_chars=1800)
            if isinstance(page_payload.get("snapshot"), str):
                page_payload["snapshot"] = cls._truncate_context_text(page_payload["snapshot"], max_chars=1800)
            compressed["page"] = page_payload
        if isinstance(compressed.get("click_result"), dict):
            compressed["click_result"] = cls._compress_tool_result_payload(
                normalized_tool_name,
                dict(compressed["click_result"]),
            )
        return compressed or tool_result

    max_items = 6
    compressed = {}
    for key, value in tool_result.items():
        if isinstance(value, str):
            compressed[key] = cls._truncate_context_text(value, max_chars=800)
        elif isinstance(value, list):
            compressed[key] = value[:max_items]
        elif isinstance(value, dict):
            nested_payload = {}
            nested_count = 0
            for nested_key, nested_value in value.items():
                if nested_count >= max_items:
                    nested_payload["truncated"] = True
                    break
                if isinstance(nested_value, str):
                    nested_payload[nested_key] = cls._truncate_context_text(nested_value, max_chars=400)
                elif isinstance(nested_value, list):
                    nested_payload[nested_key] = nested_value[:max_items]
                else:
                    nested_payload[nested_key] = nested_value
                nested_count += 1
            compressed[key] = nested_payload
        else:
            compressed[key] = value
    return compressed
