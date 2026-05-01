"""Agent orchestration helpers extracted from ``DialogueSystem.main``.

These helpers keep Selena's public methods intact while moving the large
agent-loop implementation details into a dedicated module.
"""

from __future__ import annotations

import json
import logging
import time

import requests

from project_config import normalize_reasoning_effort

from ..llm.CallingAPI import (
    _extract_usage_from_llm_result,
    append_llm_call_message,
    call_LLM,
    finalize_llm_call,
    record_llm_call,
)
from .agent_session import AgentSession
from ..security.redaction import redact_payload
from ..policy.tool_metadata import get_tool_metadata

logger = logging.getLogger("DialogueSystem.main")

SUMMARY_TOOL_NAME = "summarizeToolResults"
WEB_SEARCH_TOOL_NAME = "webSearch"
KIMI_WEB_SEARCH_TOOL_NAME = "$web_search"

def _build_agent_request_payload(
    self,
    tmp_agent_message,
    active_tools,
    current_llm: dict,
    thinking_enabled: bool,
    reasoning_effort: str = "high",
):
    """构造 Agent 请求体。"""
    payload = {
        "model": current_llm["modelName"],
        "messages": tmp_agent_message,
        "tools": active_tools,
        "tool_choice": "auto",
        "enable_thinking": thinking_enabled,
        "thinking": {"type": "enabled" if thinking_enabled else "disabled"},
        "reasoning": {"enabled": thinking_enabled},
    }
    if thinking_enabled:
        payload["reasoning_effort"] = normalize_reasoning_effort(reasoning_effort)
    return payload

def _request_agent_response(
    self,
    tmp_agent_message,
    current_llm: dict,
    active_tools,
    thinking_enabled: bool,
    reasoning_effort: str = "high",
):
    """向 Agent 模型发起请求，并带有限次重试。"""
    payload = self._build_agent_request_payload(
        tmp_agent_message,
        active_tools,
        current_llm,
        thinking_enabled,
        reasoning_effort,
    )
    caller_name = self._resolve_agent_llm_log_caller(default="Agent")
    # ---- record for LLM Inspector (Agent bypasses call_LLM) ----
    log_id = record_llm_call(
        caller=caller_name,
        messages=tmp_agent_message,
        model_key="",
        model_name=current_llm.get("modelName", ""),
        thinking=thinking_enabled,
        json_mode=False,
        stream=False,
        reasoning_effort=reasoning_effort if thinking_enabled else None,
        extra={"tools": active_tools},
    )
    for attempt in range(1, 4):
        try:
            response = self.session.post(
                current_llm["url"],
                headers={
                    "Authorization": f'Bearer {current_llm["API"]}',
                    "Content-Type": "application/json"
                },
                data=json.dumps(payload)
            )
            response.raise_for_status()
            response_json = response.json()
            usage_payload = _extract_usage_from_llm_result(response_json)
            response_message = ((response_json.get("choices") or [{}])[0].get("message") or {})
            response_content = response_message.get("content")
            if isinstance(response_content, list):
                response_content = "".join(
                    str(item.get("text", ""))
                    for item in response_content
                    if isinstance(item, dict) and item.get("type") == "text"
                )
            if not isinstance(response_content, str) or not response_content:
                response_content = json.dumps(
                    {"tool_calls": response_message.get("tool_calls") or [], "message": response_message},
                    ensure_ascii=False,
                    indent=2,
                )
            finalize_llm_call(
                log_id,
                response_message={
                    "role": str(response_message.get("role") or "assistant"),
                    "content": response_content,
                },
                extra={"usage": usage_payload},
            )
            return response, response_json, log_id
        except Exception as e:
            status_code = ""
            response_body_excerpt = ""
            if isinstance(e, requests.HTTPError):
                response_obj = getattr(e, "response", None)
                if response_obj is not None:
                    status_code = str(getattr(response_obj, "status_code", "") or "")
                    try:
                        response_body_excerpt = self._truncate_context_text(
                            response_obj.text or "",
                            max_chars=800,
                        )
                    except Exception:
                        response_body_excerpt = str(response_obj.text or "")[:800]
            try:
                payload_chars = len(json.dumps(payload, ensure_ascii=False))
            except Exception:
                payload_chars = 0
            active_tool_names = [
                str((tool.get("function") or {}).get("name", "")).strip()
                for tool in (active_tools or [])
                if str((tool.get("function") or {}).get("name", "")).strip()
            ]
            recent_roles = [
                str((message or {}).get("role", "")).strip()
                for message in list(tmp_agent_message or [])[-6:]
                if isinstance(message, dict)
            ]
            loaded_deferred_tool_names = sorted(
                str(name).strip()
                for name in (self._turn_loaded_deferred_tool_names or set())
                if str(name).strip()
            )
            logger.error(
                "LLM请求失败详情 | status=%s | body=%s | message_count=%s | payload_chars=%s | "
                "active_tool_count=%s | loaded_deferred_count=%s | active_tool_names=%s | "
                "loaded_deferred_tools=%s | recent_roles=%s",
                status_code or "?",
                response_body_excerpt or "",
                len(list(tmp_agent_message or [])),
                payload_chars,
                len(active_tool_names),
                len(loaded_deferred_tool_names),
                active_tool_names[:12],
                loaded_deferred_tool_names[:12],
                recent_roles,
            )
            if attempt < 3:
                logger.error("LLM请求失败 | attempt=%s/3 | error=%s | 5秒后重试", attempt, e)
                time.sleep(5)
            else:
                finalize_llm_call(log_id, error=str(e))
                logger.error("LLM请求失败 | error=%s", e)
                raise

def _normalize_tool_arg_keys(self, tool_definition: dict, function_args: dict) -> dict:
    """Match LLM-supplied argument keys to the canonical casing in the tool schema."""
    if not function_args:
        return function_args
    tool_name = ((tool_definition.get("function") or {}).get("name") or "").strip()
    props = ((tool_definition.get("function") or {}).get("parameters") or {}).get("properties") or {}
    if not props:
        func = self.function_map.get(tool_name)
        if func:
            import inspect
            sig = inspect.signature(func)
            props = {p: {} for p in sig.parameters if p != "self"}
    if not props:
        return function_args

    def _normalize_arg_token(value) -> str:
        return "".join(ch for ch in str(value or "").lower() if ch.isalnum())

    canonical_map = {_normalize_arg_token(k): k for k in props}
    prompt_like_keys = [
        key
        for key in props
        if _normalize_arg_token(key).endswith("prompt")
    ]
    limit_like_tokens = {
        "limit",
        "maxresults",
        "maxitems",
        "pagesize",
        "topk",
        "count",
        "maxcount",
        "numresults",
        "resultlimit",
    }
    limit_like_keys = [
        key
        for key in props
        if _normalize_arg_token(key) in limit_like_tokens
    ]
    normalized = {}
    for k, v in function_args.items():
        incoming_token = _normalize_arg_token(k)
        canonical = canonical_map.get(incoming_token)
        if canonical is None and incoming_token:
            canonical = canonical_map.get(f"{incoming_token}by")
        if (
            canonical is None
            and incoming_token == "prompt"
            and len(prompt_like_keys) == 1
        ):
            canonical = prompt_like_keys[0]
        if (
            canonical is None
            and incoming_token in limit_like_tokens
            and len(limit_like_keys) == 1
        ):
            canonical = limit_like_keys[0]
        normalized[canonical if canonical else k] = v
    return normalized

def execute_tool_call(self, tool_call: dict):
    """执行模型返回的工具调用，并兼容 skill 工具。"""
    function_name = tool_call["function"]["name"]
    builtin_proxy_name = (
        KIMI_WEB_SEARCH_TOOL_NAME
        if function_name == WEB_SEARCH_TOOL_NAME
        else function_name
    )
    function_args = self._parse_tool_call_arguments(tool_call)
    tool_definition = self._resolve_tool_definition(function_name)
    policy_result = self.tool_policy_engine.evaluate_tool_call(
        tool_definition,
        function_args,
        session_context=self._get_active_tool_session_context(),
    )
    if policy_result.get("decision") == "blocked":
        self._record_tool_security_event(
            event_type="blocked",
            tool_name=function_name,
            detail=str(policy_result.get("reason") or "Tool call blocked by policy."),
            status="blocked",
            payload={"policy": dict(policy_result)},
        )
        return {
            "ok": False,
            "error": policy_result.get("reason", "Tool call blocked by policy."),
            "policy": policy_result,
        }
    if policy_result.get("decision") == "requires_approval":
        approval_payload = self._create_pending_tool_approval(
            tool_name=function_name,
            function_args=function_args,
            policy_result=policy_result,
        )
        return {
            "ok": False,
            "error": policy_result.get("reason", "Tool call requires approval."),
            "policy": policy_result,
            "approval_required": True,
            "approval": approval_payload,
        }
    if function_name == "getDelegatedTaskStatus":
        function_args.setdefault("WaitForCompletion", True)
        function_args.setdefault("TimeoutSeconds", 20.0)
        function_args.setdefault("PollIntervalSeconds", 0.5)
    try:
        if tool_call.get("type") == "builtin_function":
            logger.info(
                "Builtin tool returned result | name=%s | payload_keys=%s",
                function_name,
                list(function_args.keys()) if isinstance(function_args, dict) else type(function_args).__name__
            )
            return function_args or {"ok": True}
        if str(builtin_proxy_name).startswith("$"):
            return self._execute_builtin_tool_via_kimi(builtin_proxy_name, function_args)
        if self.skill_tool_registry.has(function_name):
            return self.skill_tool_registry.execute(function_name, function_args)
        if self.dynamic_tool_registry.has(function_name):
            return self.dynamic_tool_registry.execute(function_name, function_args)
        if function_name not in self.function_map:
            return {"error": f"Tool not found: {function_name}"}
        if function_name == SUMMARY_TOOL_NAME:
            function_args["InputNum"] = self.input_num
        if function_name in self.function_map:
            function_args = self._normalize_tool_arg_keys(tool_definition, function_args)
            result = self.function_map[function_name](**function_args)
            if get_tool_metadata(tool_definition).get("supports_redaction", True):
                redacted_result = redact_payload(result)
                self._record_tool_security_event(
                    event_type="executed",
                    tool_name=function_name,
                    detail=f"Executed via backend {get_tool_metadata(tool_definition).get('backend', 'direct')}",
                    status="completed",
                    payload={
                        "backend": get_tool_metadata(tool_definition).get("backend", "direct"),
                        "checkpoint": redacted_result.get("checkpoint", {}) if isinstance(redacted_result, dict) else {},
                    },
                )
                return redacted_result
            self._record_tool_security_event(
                event_type="executed",
                tool_name=function_name,
                detail=f"Executed via backend {get_tool_metadata(tool_definition).get('backend', 'direct')}",
                status="completed",
                payload={"backend": get_tool_metadata(tool_definition).get("backend", "direct")},
            )
            return result
        return {"error": f"Tool not found: {function_name}"}
    except Exception as error:
        logger.exception(
            "Tool execution failed | name=%s | args=%s",
            function_name,
            self._truncate_context_text(
                self._stringify_tool_result(function_args, max_chars=240),
                max_chars=240,
            ),
        )
        self._record_tool_security_event(
            event_type="executed",
            tool_name=function_name,
            detail=f"Execution failed: {error}",
            status="failed",
            payload={"backend": get_tool_metadata(tool_definition).get("backend", "direct")},
        )
        return {
            "ok": False,
            "error": f"{function_name} failed: {error}",
            "tool_name": function_name,
        }
    return {"error": f"Tool not found: {function_name}"}

def _execute_builtin_tool_via_kimi(self, function_name: str, function_args: dict):
    """Proxy a $-prefixed tool call through a dedicated Kimi request."""
    kimi_config = self._resolve_kimi_llm_config()
    if not kimi_config:
        return {"error": f"No Kimi model available to execute builtin tool '{function_name}'."}
    query = str(function_args.get("Query") or function_args.get("query") or "").strip()
    if not query:
        return {"error": "Query is required for web search."}

    builtin_tool_def = {
        "type": "builtin_function",
        "function": {"name": function_name},
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个搜索助手。你必须使用 $web_search 工具来回答用户的问题。"
                "不要直接回答，先搜索再回答。将搜索结果原样整理返回给用户。"
            ),
        },
        {"role": "user", "content": query},
    ]
    payload = {
        "model": kimi_config["modelName"],
        "messages": messages,
        "tools": [builtin_tool_def],
        "tool_choice": "required",
        "enable_thinking": False,
        "thinking": {"type": "disabled"},
        "reasoning": {"enabled": False},
    }
    headers = {
        "Authorization": f'Bearer {kimi_config["API"]}',
        "Content-Type": "application/json",
    }
    logger.info("Kimi builtin proxy | tool=%s | query=%s", function_name, query[:80])
    try:
        for attempt in range(1, 4):
            try:
                resp = self.session.post(
                    kimi_config["url"],
                    headers=headers,
                    data=json.dumps(payload),
                    timeout=30,
                )
                resp.raise_for_status()
                break
            except Exception as exc:
                if attempt >= 3:
                    raise
                logger.warning("Kimi builtin proxy retry %s/3: %s", attempt, exc)
                time.sleep(3)
        result = resp.json()
        choice = (result.get("choices") or [{}])[0]
        finish_reason = choice.get("finish_reason", "")
        assistant_msg = choice.get("message") or {}

        if finish_reason == "tool_calls":
            messages.append(assistant_msg)
            for tc in (assistant_msg.get("tool_calls") or []):
                tc_args = json.loads(tc["function"]["arguments"])
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc["function"]["name"],
                    "content": json.dumps(tc_args),
                })
            payload["messages"] = messages
            payload.pop("tool_choice", None)
            resp2 = self.session.post(
                kimi_config["url"],
                headers=headers,
                data=json.dumps(payload),
                timeout=60,
            )
            resp2.raise_for_status()
            result2 = resp2.json()
            choice2 = (result2.get("choices") or [{}])[0]
            content = str((choice2.get("message") or {}).get("content") or "").strip()
        else:
            content = str(assistant_msg.get("content") or "").strip()

        logger.info("Kimi builtin proxy completed | tool=%s | content_length=%s", function_name, len(content))
        return {"ok": True, "result": content}
    except Exception as exc:
        logger.error("Kimi builtin proxy failed | tool=%s | error=%s", function_name, exc)
        return {"ok": False, "error": f"Web search failed: {exc}"}

def run_agent(self, model):
    """运行 Agent 循环，直到生成最终回复或触发工具限制。"""
    self._discard_pending_retrieval_cache_records()
    self.agent_cycle = True
    self._set_active_tool_session_context(self._build_tool_session_context())
    tool_trace = []
    self._current_turn_tool_trace = tool_trace
    self._current_turn_retrieval_cache_ids = []
    self._reset_browser_ephemeral_state()
    executed_tool_calls = 0
    runtime_settings = self._resolve_agent_runtime(model)
    agent_runtime_config = self._get_agent_runtime_config(browser_mode=self._turn_looks_like_browser_task())
    max_tool_calls = agent_runtime_config["max_tool_calls"]
    max_consecutive_same_calls = agent_runtime_config["max_consecutive_same_tool_calls"]
    tmp_agent_message = self._build_agent_request_messages(
        self.agent_message,
        [
            self._build_agent_runtime_prompt(
                agent_runtime_config,
                supports_vision=bool((runtime_settings.get("capabilities") or {}).get("supports_vision", False)),
            )
        ]
    )
    current_llm = runtime_settings["llm"]
    self._turn_active_tools_snapshot = list(runtime_settings["tools"])
    thinking_enabled = runtime_settings["thinking_enabled"]
    self._agent_loop_body(
        tmp_agent_message=tmp_agent_message,
        tool_trace=tool_trace,
        executed_tool_calls=executed_tool_calls,
        max_tool_calls=max_tool_calls,
        max_consecutive_same_calls=max_consecutive_same_calls,
        runtime_settings=runtime_settings,
        current_llm=current_llm,
        thinking_enabled=thinking_enabled,
        model=model,
    )

def _agent_loop_body(
    self,
    *,
    tmp_agent_message: list,
    tool_trace: list,
    executed_tool_calls: int,
    max_tool_calls: int,
    max_consecutive_same_calls: int,
    runtime_settings: dict,
    current_llm: dict,
    thinking_enabled: bool,
    model: str,
):
    """Agent 循环主体，run_agent 和 _resume_agent_loop 共用。"""
    steps = 0
    previous_tool_name = ""
    consecutive_tool_calls = 0
    while self.agent_cycle and executed_tool_calls < max_tool_calls:
        # ── 中断检查点：用户可在此注入纠正消息 ──
        if self._check_agent_interrupt(tmp_agent_message):
            self._append_visible_agent_step(
                step=steps + 1,
                phase="agent",
                title="收到用户新指令",
                detail="用户发送了新消息，Agent 将据此重新规划。",
                status="completed",
            )
        core_tools, deferred_tools = self._split_tools_core_and_deferred(
            runtime_settings["tools"],
            recent_messages=tmp_agent_message,
        )
        active_tools = core_tools
        catalog_reminder = self._build_deferred_tool_catalog_reminder(deferred_tools)

        active_tool_names = [
            str((tool.get("function") or {}).get("name", "")).strip()
            for tool in active_tools
            if str((tool.get("function") or {}).get("name", "")).strip()
        ]
        deferred_tool_names = [
            self._tool_name_from_definition(tool_definition)
            for tool_definition in deferred_tools
            if self._tool_name_from_definition(tool_definition)
        ]
        logger.info(
            "LLM请求参数 | step=%s | model=%s | message_count=%s",
            steps + 1,
            current_llm["modelName"],
            len(tmp_agent_message)
        )
        logger.info(
            "Agent runtime | step=%s | tool_count=%s | deferred_count=%s | thinking_enabled=%s",
            steps + 1,
            len(active_tools),
            len(deferred_tools),
            thinking_enabled,
        )
        logger.info(
            "Agent tools | step=%s | tool_names=%s | deferred=%s",
            steps + 1,
            active_tool_names,
            deferred_tool_names
        )

        request_messages = list(tmp_agent_message)
        browser_ephemeral_messages = self._build_browser_ephemeral_messages(
            runtime_settings,
            {
                "max_tool_calls": max_tool_calls,
                "max_consecutive_same_tool_calls": max_consecutive_same_calls,
            },
        )
        if browser_ephemeral_messages:
            request_messages.extend(browser_ephemeral_messages)
        if catalog_reminder:
            request_messages.append({"role": "system", "content": catalog_reminder})
        response, result, agent_log_id = self._request_agent_response(
            request_messages,
            current_llm,
            active_tools,
            thinking_enabled,
            runtime_settings.get("reasoning_effort", "high"),
        )
        logger.info("LLM响应 | step=%s | status=%s", steps + 1, response.status_code)
        logger.info("LLM回复 | step=%s | content=%s", steps + 1, json.dumps(result, ensure_ascii=False))
        assistant_message = result["choices"][0]["message"]
        tmp_agent_message.append(assistant_message)
        tool_calls = assistant_message.get("tool_calls", [])
        logger.info("LLM回复工具调用 | step=%s | tool_calls=%s", steps + 1, len(tool_calls))
        if not tool_calls:
            self.agent_cycle = False
            logger.warning("Agent loop ended | step=%s | reason=no_tool_calls | using_fallback", steps + 1)
            self._append_visible_agent_step(
                step=steps + 1,
                phase="agent",
                title="未获取到新的工具调用",
                detail="系统将基于已有上下文直接整理回复。",
                status="completed",
            )
            assistant_content = str(assistant_message.get("content") or "").strip()
            trace_summary = self._build_tool_summary_from_trace(tool_trace)
            if assistant_content:
                fallback_summary = f"{trace_summary}\n\nAgent 回复草稿（作为参考，如果是文献引用则不要修改文献内容）:\n{assistant_content}"
            else:
                fallback_summary = trace_summary
            append_llm_call_message(agent_log_id, {
                "role": "tool",
                "content": f"[summarizeToolResults:fallback] {json.dumps({'SummaryTools': fallback_summary, 'ReachedMaxToolCalls': False}, ensure_ascii=False)}",
            })
            self.summarizeToolResults(
                SummaryTools=fallback_summary,
                ReachedMaxToolCalls=False,
                InputNum=self.input_num,
            )
            break
        for tool_call in tool_calls:
            function_name = str((tool_call.get("function") or {}).get("name", "")).strip()
            function_args = self._parse_tool_call_arguments(tool_call)
            self._append_visible_agent_step(
                step=steps + 1,
                phase="tool_call",
                tool_name=function_name,
                title=self._build_visible_tool_step_title(
                    function_name,
                    phase="tool_call",
                ),
                detail=self._build_visible_tool_step_detail(
                    function_name,
                    None,
                    function_args=function_args,
                ),
                status="running",
            )
            tool_result = self.execute_tool_call(tool_call)
            compressed_tool_result = self._compress_tool_result_payload(function_name, tool_result)
            tool_error = ""
            approval_required = False
            if isinstance(tool_result, dict):
                tool_error = str(tool_result.get("error", "") or "").strip()
                approval_required = bool(tool_result.get("approval_required", False))
            self._finalize_visible_agent_step(
                step=steps + 1,
                phase="tool_call",
                tool_name=function_name,
                title=self._build_visible_tool_step_title(
                    function_name,
                    phase="tool_result",
                    tool_result=tool_result,
                ),
                detail=self._build_visible_tool_step_detail(
                    function_name,
                    compressed_tool_result,
                    function_args=function_args,
                ),
                status="running" if approval_required else ("failed" if tool_error else "completed"),
            )
            if approval_required:
                policy_result = tool_result.get("policy", {})
                approval_payload = dict(tool_result.get("approval") or {})
                approval_id = str(approval_payload.get("approval_id", "") or "").strip()
                approval_question = self._build_tool_approval_question(
                    function_name,
                    function_args=function_args,
                    policy_result=policy_result,
                    tmp_agent_message=tmp_agent_message,
                    runtime_settings=runtime_settings,
                )
                pending_approval_result = self._build_pending_tool_approval_result(
                    approval_id=approval_id,
                    tool_name=function_name,
                    function_args=function_args,
                    policy_result=policy_result,
                    approval_question=approval_question,
                )
                tmp_agent_message.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": function_name,
                    "content": json.dumps(pending_approval_result, ensure_ascii=False),
                })
                tmp_agent_message.append({
                    "role": "assistant",
                    "content": approval_question,
                })
                append_llm_call_message(agent_log_id, {
                    "role": "tool",
                    "content": f"[{function_name}] {self._stringify_tool_result(pending_approval_result, max_chars=500)}",
                })
                append_llm_call_message(agent_log_id, {
                    "role": "assistant",
                    "content": approval_question,
                })
                self.appendUserMessageSync(approval_question, "assistant")
                self._suspend_agent_state(
                    tmp_agent_message=tmp_agent_message,
                    tool_trace=tool_trace,
                    executed_tool_calls=executed_tool_calls,
                    model=model,
                    runtime_settings=runtime_settings,
                    suspension_type="tool_approval",
                    pending_tool_call=tool_call,
                    pending_function_name=function_name,
                    pending_function_args=function_args,
                    pending_policy_result=policy_result,
                    pending_approval_id=approval_id,
                    approval_question=approval_question,
                )
                self.agent_cycle = False
                self._append_visible_agent_step(
                    step=steps + 1,
                    phase="agent",
                    title="等待用户审批工具调用",
                    detail=f"{function_name} 需要用户审批后才能继续执行。",
                    status="running",
                )
                break
            # ── askUser 检测：暂停循环等待用户回复 ──
            is_ask_user = (
                function_name == self.ASK_USER_TOOL_NAME
                and isinstance(tool_result, dict)
                and tool_result.get("action") == "ask_user"
            )
            if is_ask_user:
                question = str(tool_result.get("question", "")).strip()
                options = tool_result.get("options") or []
                context = str(tool_result.get("context", "")).strip()
                # 生成中间回复给用户
                reply_parts = []
                if context:
                    reply_parts.append(context)
                reply_parts.append(question)
                if options:
                    reply_parts.append("选项：" + "、".join(options))
                intermediate_reply = "\n".join(reply_parts)
                self.appendUserMessageSync(intermediate_reply, "assistant")
                # 保存状态
                self._suspend_agent_state(
                    tmp_agent_message=tmp_agent_message,
                    tool_trace=tool_trace,
                    executed_tool_calls=executed_tool_calls,
                    model=model,
                    runtime_settings=runtime_settings,
                    ask_user_payload=tool_result,
                    ask_user_tool_call=tool_call,
                )
                self.agent_cycle = False
                self._append_visible_agent_step(
                    step=steps + 1,
                    phase="agent",
                    title="等待用户回复",
                    detail=question,
                    status="running",
                )
                break
            if isinstance(tool_result, dict) and tool_result.get("direct_reply", False):
                direct_reply_text = str(tool_result.get("content", "") or "").strip()
                if function_name != SUMMARY_TOOL_NAME:
                    self._store_retrieval_cache_entry(function_name, function_args, tool_result)
                    executed_tool_calls += 1
                    self._record_browser_observation(function_name, tool_result, function_args)
                    skill_name = self.tool_skill_map.get(function_name, "")
                    tool_trace.append(
                        {
                            "tool_name": function_name,
                            "skill_name": skill_name,
                            "result_excerpt": self._stringify_tool_result(tool_result),
                            "args_excerpt": self._stringify_tool_result(function_args, max_chars=200),
                        }
                    )
                    consecutive_tool_calls = consecutive_tool_calls + 1 if function_name == previous_tool_name else 1
                    previous_tool_name = function_name
                tmp_agent_message.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": function_name or tool_call["function"]["name"],
                    "content": json.dumps(compressed_tool_result, ensure_ascii=False)
                })
                append_llm_call_message(agent_log_id, {
                    "role": "tool",
                    "content": (
                        f"[{function_name}] {json.dumps(function_args, ensure_ascii=False)}"
                        if function_name == SUMMARY_TOOL_NAME
                        else f"[{function_name}] {self._stringify_tool_result(compressed_tool_result, max_chars=500)}"
                    ),
                })
                self._append_display_only_conversation_message(direct_reply_text, "assistant")
                self.agent_cycle = False
                break
            if function_name != SUMMARY_TOOL_NAME:
                self._store_retrieval_cache_entry(function_name, function_args, tool_result)
                executed_tool_calls += 1
                self._record_browser_observation(function_name, tool_result, function_args)
                skill_name = self.tool_skill_map.get(function_name, "")
                tool_trace.append(
                    {
                        "tool_name": function_name,
                        "skill_name": skill_name,
                        "result_excerpt": self._stringify_tool_result(tool_result),
                        "args_excerpt": self._stringify_tool_result(function_args, max_chars=200),
                    }
                )
                consecutive_tool_calls = consecutive_tool_calls + 1 if function_name == previous_tool_name else 1
                previous_tool_name = function_name
            tmp_agent_message.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "name": function_name or tool_call["function"]["name"],
                "content": json.dumps(compressed_tool_result, ensure_ascii=False)
            })
            append_llm_call_message(agent_log_id, {
                "role": "tool",
                "content": (
                    f"[{function_name}] {json.dumps(function_args, ensure_ascii=False)}"
                    if function_name == SUMMARY_TOOL_NAME
                    else f"[{function_name}] {self._stringify_tool_result(compressed_tool_result, max_chars=500)}"
                ),
            })
            if not self.agent_cycle:
                break
            if executed_tool_calls >= max_tool_calls:
                self._append_visible_agent_step(
                    step=steps + 1,
                    phase="agent",
                    title="达到本轮工具调用上限",
                    detail=f"普通工具最多调用 {max_tool_calls} 次，系统将直接整理回复。",
                    status="completed",
                )
                max_calls_summary = self._build_tool_summary_from_trace(
                    tool_trace,
                    extra_notice=f"已达到最大工具调用次数：本轮普通 tool / skill 最多可调用 {max_tool_calls} 次。"
                )
                append_llm_call_message(agent_log_id, {
                    "role": "tool",
                    "content": f"[summarizeToolResults:max_tool_calls] {json.dumps({'SummaryTools': max_calls_summary, 'ReachedMaxToolCalls': True}, ensure_ascii=False)}",
                })
                self._force_tool_summary_from_trace(
                    tool_trace,
                    input_num=self.input_num,
                    reached_max_tool_calls=True,
                    extra_notice=f"已达到最大工具调用次数：本轮普通 tool / skill 最多可调用 {max_tool_calls} 次。"
                )
                break
            if consecutive_tool_calls >= max_consecutive_same_calls:
                self._append_visible_agent_step(
                    step=steps + 1,
                    phase="agent",
                    title="触发同名工具连续调用限制",
                    detail=f"{function_name} 已连续调用 {consecutive_tool_calls} 次，系统将直接整理回复。",
                    status="completed",
                )
                consecutive_summary = self._build_tool_summary_from_trace(
                    tool_trace,
                    extra_notice=(
                        f"已达到最大工具调用次数：同名 tool `{function_name}` 已连续调用 {consecutive_tool_calls} 次，"
                        f"系统上限为 {max_consecutive_same_calls} 次。"
                    )
                )
                append_llm_call_message(agent_log_id, {
                    "role": "tool",
                    "content": f"[summarizeToolResults:consecutive_limit] {json.dumps({'SummaryTools': consecutive_summary, 'ReachedMaxToolCalls': True}, ensure_ascii=False)}",
                })
                self._force_tool_summary_from_trace(
                    tool_trace,
                    input_num=self.input_num,
                    reached_max_tool_calls=True,
                    extra_notice=(
                        f"已达到最大工具调用次数：同名 tool `{function_name}` 已连续调用 {consecutive_tool_calls} 次，"
                        f"系统上限为 {max_consecutive_same_calls} 次。"
                    )
                )
                break
        steps += 1
        if not self.agent_cycle:
            break

    if self.agent_cycle and executed_tool_calls >= max_tool_calls:
        self._append_visible_agent_step(
            step=steps + 1,
            phase="agent",
            title="达到本轮工具调用上限",
            detail=f"普通工具最多调用 {max_tool_calls} 次，系统将直接整理回复。",
            status="completed",
        )
        self._force_tool_summary_from_trace(
            tool_trace,
            input_num=self.input_num,
            reached_max_tool_calls=True,
            extra_notice=f"已达到最大工具调用次数：本轮普通 tool / skill 最多可调用 {max_tool_calls} 次。"
        )

def _evaluate_detached_task_lifecycle(self, task_control: dict | None = None):
    task_control = task_control if isinstance(task_control, dict) else {}
    cancel_event = task_control.get("cancel_event")
    if cancel_event is not None and getattr(cancel_event, "is_set", None) and cancel_event.is_set():
        return {
            "ok": False,
            "lifecycle": "cancelled",
            "error": str(task_control.get("cancel_reason") or "Delegated task was cancelled."),
        }
    deadline_at = task_control.get("deadline_at")
    if deadline_at not in (None, ""):
        try:
            if time.time() >= float(deadline_at):
                return {
                    "ok": False,
                    "lifecycle": "timed_out",
                    "error": str(task_control.get("timeout_message") or "Delegated task exceeded its timeout budget."),
                }
        except (TypeError, ValueError):
            pass
    return None

def _run_detached_agent_loop(
    self,
    *,
    tmp_agent_message: list,
    model: str,
    runtime_config: dict,
    runtime_settings: dict,
    tool_trace: list,
    executed_tool_calls: int,
    task_control: dict | None = None,
):
    current_llm = runtime_settings["llm"]
    thinking_enabled = runtime_settings["thinking_enabled"]
    previous_tool_name = ""
    consecutive_tool_calls = 0
    max_consecutive_same_calls = runtime_config["max_consecutive_same_tool_calls"]
    agent_log_id = 0

    while executed_tool_calls < runtime_config["max_tool_calls"]:
        lifecycle_result = self._evaluate_detached_task_lifecycle(task_control)
        if lifecycle_result:
            lifecycle_result.setdefault("tool_trace", list(tool_trace))
            lifecycle_result.setdefault("structured_output", {})
            lifecycle_result.setdefault("final_text", "")
            return lifecycle_result

        core_tools, deferred_tools = self._split_tools_core_and_deferred(
            runtime_settings["tools"],
            recent_messages=tmp_agent_message,
        )
        active_tools = core_tools
        catalog_reminder = self._build_deferred_tool_catalog_reminder(deferred_tools)

        request_messages = list(tmp_agent_message)
        browser_ephemeral_messages = self._build_browser_ephemeral_messages(runtime_settings, runtime_config)
        if browser_ephemeral_messages:
            request_messages.extend(browser_ephemeral_messages)
        if catalog_reminder:
            request_messages.append({"role": "system", "content": catalog_reminder})
        response, result, agent_log_id = self._request_agent_response(
            request_messages,
            current_llm,
            active_tools,
            thinking_enabled,
            runtime_settings.get("reasoning_effort", "high"),
        )
        logger.info(
            "Detached agent response | session=%s | status=%s",
            self._get_current_agent_session().name,
            response.status_code,
        )
        assistant_message = result["choices"][0]["message"]
        tmp_agent_message.append(assistant_message)
        tool_calls = assistant_message.get("tool_calls", [])
        if not tool_calls:
            return {
                "ok": True,
                "lifecycle": "completed",
                "final_text": str(assistant_message.get("content", "") or "").strip(),
                "tool_trace": list(tool_trace),
                "structured_output": {},
            }
        for tool_call in tool_calls:
            lifecycle_result = self._evaluate_detached_task_lifecycle(task_control)
            if lifecycle_result:
                lifecycle_result.setdefault("tool_trace", list(tool_trace))
                lifecycle_result.setdefault("structured_output", {})
                lifecycle_result.setdefault("final_text", "")
                return lifecycle_result

            function_name = str((tool_call.get("function") or {}).get("name", "")).strip()
            function_args = self._parse_tool_call_arguments(tool_call)
            if function_name == SUMMARY_TOOL_NAME:
                summary_text = str(function_args.get("SummaryTools", "") or "").strip()
                if not summary_text:
                    summary_text = self._build_tool_summary_from_trace(tool_trace)
                self._append_agent_log_tool_message(
                    agent_log_id,
                    function_name,
                    function_args=function_args,
                )
                return {
                    "ok": True,
                    "lifecycle": "completed",
                    "final_text": summary_text,
                    "tool_trace": list(tool_trace),
                    "structured_output": {
                        "summary_tools": summary_text,
                        "reached_max_tool_calls": bool(function_args.get("ReachedMaxToolCalls", False)),
                        "procedure_summary": str(function_args.get("ProcedureSummary", "") or "").strip(),
                        "previous_procedure_id": str(function_args.get("PreviousProcedureId", "") or "").strip(),
                    },
                }
            tool_result = self.execute_tool_call(tool_call)
            if isinstance(tool_result, dict) and tool_result.get("approval_required", False):
                policy_result = dict(tool_result.get("policy") or {})
                approval_payload = dict(tool_result.get("approval") or {})
                approval_id = str(approval_payload.get("approval_id", "") or "").strip()
                approval_question = self._build_tool_approval_question(
                    function_name,
                    function_args=function_args,
                    policy_result=policy_result,
                    tmp_agent_message=tmp_agent_message,
                    runtime_settings=runtime_settings,
                )
                pending_approval_result = self._build_pending_tool_approval_result(
                    approval_id=approval_id,
                    tool_name=function_name,
                    function_args=function_args,
                    policy_result=policy_result,
                    approval_question=approval_question,
                )
                tmp_agent_message.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": function_name,
                        "content": json.dumps(pending_approval_result, ensure_ascii=False),
                    }
                )
                tmp_agent_message.append({"role": "assistant", "content": approval_question})
                self._suspend_agent_state(
                    tmp_agent_message=tmp_agent_message,
                    tool_trace=tool_trace,
                    executed_tool_calls=executed_tool_calls,
                    model=model,
                    runtime_settings=runtime_settings,
                    suspension_type="tool_approval",
                    pending_tool_call=tool_call,
                    pending_function_name=function_name,
                    pending_function_args=function_args,
                    pending_policy_result=policy_result,
                    pending_approval_id=approval_id,
                    approval_question=approval_question,
                )
                return {
                    "ok": True,
                    "lifecycle": "waiting_approval",
                    "final_text": "",
                    "tool_trace": list(tool_trace),
                    "structured_output": {},
                    "awaiting": {
                        "type": "approval",
                        "question": approval_question,
                        "approval_id": approval_id,
                        "tool_name": function_name,
                        "tool_arguments": dict(function_args or {}),
                        "policy": policy_result,
                    },
                }
            is_ask_user = (
                function_name == self.ASK_USER_TOOL_NAME
                and isinstance(tool_result, dict)
                and tool_result.get("action") == "ask_user"
            )
            if is_ask_user:
                question = str(tool_result.get("question", "")).strip()
                options = [str(option).strip() for option in (tool_result.get("options") or []) if str(option).strip()]
                context = str(tool_result.get("context", "")).strip()
                self._suspend_agent_state(
                    tmp_agent_message=tmp_agent_message,
                    tool_trace=tool_trace,
                    executed_tool_calls=executed_tool_calls,
                    model=model,
                    runtime_settings=runtime_settings,
                    ask_user_payload=tool_result,
                    ask_user_tool_call=tool_call,
                )
                return {
                    "ok": True,
                    "lifecycle": "waiting_input",
                    "final_text": "",
                    "tool_trace": list(tool_trace),
                    "structured_output": {},
                    "awaiting": {
                        "type": "input",
                        "question": question,
                        "options": options,
                        "context": context,
                    },
                }
            if isinstance(tool_result, dict) and tool_result.get("direct_reply", False):
                direct_reply_text = str(tool_result.get("content", "") or "").strip()
                compressed_tool_result = self._compress_tool_result_payload(function_name, tool_result)
                skill_name = self.tool_skill_map.get(function_name, "")
                tool_trace.append(
                    {
                        "tool_name": function_name,
                        "skill_name": skill_name,
                        "result_excerpt": self._stringify_tool_result(tool_result),
                    }
                )
                tmp_agent_message.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": function_name or tool_call["function"]["name"],
                        "content": json.dumps(compressed_tool_result, ensure_ascii=False),
                    }
                )
                self._append_agent_log_tool_message(
                    agent_log_id,
                    function_name,
                    tool_result=compressed_tool_result,
                )
                return {
                    "ok": True,
                    "lifecycle": "completed",
                    "final_text": direct_reply_text,
                    "tool_trace": list(tool_trace),
                    "structured_output": {
                        "direct_reply": True,
                        "tool_name": function_name,
                        "content_length": len(direct_reply_text),
                    },
                }

            compressed_tool_result = self._compress_tool_result_payload(function_name, tool_result)
            self._store_retrieval_cache_entry(function_name, function_args, tool_result)
            executed_tool_calls += 1
            self._record_browser_observation(function_name, tool_result, function_args)
            skill_name = self.tool_skill_map.get(function_name, "")
            tool_trace.append(
                {
                    "tool_name": function_name,
                    "skill_name": skill_name,
                    "result_excerpt": self._stringify_tool_result(tool_result),
                }
            )
            tmp_agent_message.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": function_name or tool_call["function"]["name"],
                    "content": json.dumps(compressed_tool_result, ensure_ascii=False),
                }
            )
            self._append_agent_log_tool_message(
                agent_log_id,
                function_name,
                tool_result=compressed_tool_result,
            )
            consecutive_tool_calls = consecutive_tool_calls + 1 if function_name == previous_tool_name else 1
            previous_tool_name = function_name
            if executed_tool_calls >= runtime_config["max_tool_calls"]:
                summary_args = {
                    "SummaryTools": self._build_tool_summary_from_trace(
                        tool_trace,
                        extra_notice=(
                            "已达到最大工具调用次数："
                            f"本轮普通 tool / skill 最多可调用 {runtime_config['max_tool_calls']} 次。"
                        ),
                    ),
                    "ReachedMaxToolCalls": True,
                }
                self._append_agent_log_tool_message(
                    agent_log_id,
                    SUMMARY_TOOL_NAME,
                    function_args=summary_args,
                    tag="max_tool_calls",
                )
                return {
                    "ok": True,
                    "lifecycle": "completed",
                    "final_text": str(summary_args["SummaryTools"] or "").strip(),
                    "tool_trace": list(tool_trace),
                    "structured_output": {
                        "summary_tools": str(summary_args["SummaryTools"] or "").strip(),
                        "reached_max_tool_calls": True,
                        "forced_summary_reason": "max_tool_calls",
                    },
                }
            if consecutive_tool_calls >= max_consecutive_same_calls:
                summary_args = {
                    "SummaryTools": self._build_tool_summary_from_trace(
                        tool_trace,
                        extra_notice=(
                            f"已达到最大工具调用次数：同名 tool `{function_name}` 已连续调用 "
                            f"{consecutive_tool_calls} 次，系统上限为 {max_consecutive_same_calls} 次。"
                        ),
                    ),
                    "ReachedMaxToolCalls": True,
                }
                self._append_agent_log_tool_message(
                    agent_log_id,
                    SUMMARY_TOOL_NAME,
                    function_args=summary_args,
                    tag="consecutive_limit",
                )
                return {
                    "ok": True,
                    "lifecycle": "completed",
                    "final_text": str(summary_args["SummaryTools"] or "").strip(),
                    "tool_trace": list(tool_trace),
                    "structured_output": {
                        "summary_tools": str(summary_args["SummaryTools"] or "").strip(),
                        "reached_max_tool_calls": True,
                        "forced_summary_reason": "consecutive_limit",
                    },
                }

    final_summary_text = self._build_tool_summary_from_trace(tool_trace)
    if agent_log_id:
        self._append_agent_log_tool_message(
            agent_log_id,
            SUMMARY_TOOL_NAME,
            function_args={
                "SummaryTools": final_summary_text,
                "ReachedMaxToolCalls": executed_tool_calls >= runtime_config["max_tool_calls"],
            },
            tag="fallback",
        )
    return {
        "ok": True,
        "lifecycle": "completed",
        "final_text": final_summary_text,
        "tool_trace": list(tool_trace),
        "structured_output": {
            "summary_tools": final_summary_text,
            "reached_max_tool_calls": executed_tool_calls >= runtime_config["max_tool_calls"],
            "forced_summary_reason": "fallback",
        },
    }

def run_agent_detached(
    self,
    model: str,
    *,
    base_messages: list,
    max_tool_calls: int = 6,
    agent_type=None,
    agent_session: AgentSession | None = None,
    task_input: str = "",
    task_context: dict | None = None,
    task_control: dict | None = None,
):
    """Run a standalone child-agent loop and return a final text summary without mutating live contexts."""
    agent_type_str = agent_type.value if hasattr(agent_type, "value") else str(agent_type or "general")
    normalized_task_input = str(task_input or "").strip()
    detached_session = agent_session or self.create_detached_agent_session(
        session_name=f"detached-{int(time.time() * 1000)}",
        user_input=normalized_task_input,
        agent_type=agent_type_str,
    )

    with self._activate_agent_session(detached_session):
        tmp_agent_message = [dict(message) for message in base_messages]
        runtime_config = self._build_detached_agent_runtime_config(max_tool_calls)
        previous_session_context = dict(self._active_tool_session_context or {}) if self._active_tool_session_context else None
        previous_turn_snapshot = list(self._turn_active_tools_snapshot or [])
        previous_loaded_deferred = set(self._turn_loaded_deferred_tool_names or set())
        previous_subagent_depth = self._subagent_depth
        previous_turn_request = dict(self._current_turn_agent_request or {})
        previous_tool_trace = list(self._current_turn_tool_trace or [])

        task_request_input = normalized_task_input
        if not task_request_input:
            for message in reversed(tmp_agent_message):
                if str((message or {}).get("role", "")).strip().lower() == "user":
                    task_request_input = str((message or {}).get("content", "") or "").strip()
                    if task_request_input:
                        break

        self._set_current_turn_agent_request(
            user_input=task_request_input,
            route="agent",
            selected_ability="",
            reason=f"detached:{agent_type_str}",
            ranked_candidates=[],
        )
        self._subagent_depth += 1
        session_context = self._build_tool_session_context(
            is_subagent=True,
            agent_type=agent_type_str,
            workspace_root=(task_context or {}).get("workspace_root", ""),
            additional_file_roots=(task_context or {}).get("additional_file_roots") or [],
        )
        llm_caller_prefix = str((task_context or {}).get("llm_caller_prefix", "") or "").strip()
        if llm_caller_prefix:
            session_context["llm_caller_prefix"] = llm_caller_prefix
        self._set_active_tool_session_context(session_context)
        runtime_settings = self._resolve_agent_runtime(model)
        self._turn_active_tools_snapshot = list(runtime_settings["tools"])
        self._turn_loaded_deferred_tool_names = set()
        tool_trace = []
        self._current_turn_tool_trace = tool_trace

        try:
            return self._run_detached_agent_loop(
                tmp_agent_message=tmp_agent_message,
                model=model,
                runtime_config=runtime_config,
                runtime_settings=runtime_settings,
                tool_trace=tool_trace,
                executed_tool_calls=0,
                task_control=task_control,
            )
        finally:
            self._current_turn_tool_trace = previous_tool_trace
            self._current_turn_agent_request = previous_turn_request
            self._set_active_tool_session_context(previous_session_context)
            self._turn_active_tools_snapshot = previous_turn_snapshot
            self._turn_loaded_deferred_tool_names = previous_loaded_deferred
            self._subagent_depth = previous_subagent_depth

def resume_detached_agent(
    self,
    model: str,
    *,
    agent_session: AgentSession,
    max_tool_calls: int,
    agent_type=None,
    user_reply: str = "",
    approval_decision: str = "",
    task_context: dict | None = None,
    task_control: dict | None = None,
):
    """Resume a suspended detached agent after approval or user follow-up."""
    agent_type_str = agent_type.value if hasattr(agent_type, "value") else str(agent_type or "general")
    normalized_user_reply = str(user_reply or "").strip()
    normalized_approval_decision = str(approval_decision or "").strip().lower()

    with self._activate_agent_session(agent_session):
        state = self._suspended_agent_state
        if not isinstance(state, dict):
            return {"ok": False, "lifecycle": "failed", "error": "Delegated task is not waiting for input."}

        lifecycle_result = self._evaluate_detached_task_lifecycle(task_control)
        if lifecycle_result:
            lifecycle_result.setdefault("tool_trace", list(state.get("tool_trace") or []))
            lifecycle_result.setdefault("structured_output", {})
            lifecycle_result.setdefault("final_text", "")
            return lifecycle_result

        previous_session_context = dict(self._active_tool_session_context or {}) if self._active_tool_session_context else None
        previous_turn_snapshot = list(self._turn_active_tools_snapshot or [])
        previous_loaded_deferred = set(self._turn_loaded_deferred_tool_names or set())
        previous_subagent_depth = self._subagent_depth
        previous_turn_request = dict(self._current_turn_agent_request or {})
        previous_tool_trace = list(self._current_turn_tool_trace or [])

        self._suspended_agent_state = None
        self._restore_runtime_from_suspended_state(state)
        self._subagent_depth += 1
        session_context = self._build_tool_session_context(
            is_subagent=True,
            agent_type=agent_type_str,
            workspace_root=(task_context or {}).get("workspace_root", ""),
            additional_file_roots=(task_context or {}).get("additional_file_roots") or [],
        )
        llm_caller_prefix = str((task_context or {}).get("llm_caller_prefix", "") or "").strip()
        if llm_caller_prefix:
            session_context["llm_caller_prefix"] = llm_caller_prefix
        self._set_active_tool_session_context(session_context)
        runtime_settings = self._resolve_agent_runtime(model)
        self._turn_active_tools_snapshot = list(runtime_settings["tools"])
        runtime_config = self._build_detached_agent_runtime_config(max_tool_calls)

        try:
            suspension_type = str(state.get("suspension_type", "ask_user")).strip()
            tool_trace = list(state.get("tool_trace") or [])
            self._current_turn_tool_trace = tool_trace
            executed_tool_calls = int(state.get("executed_tool_calls") or 0)

            if suspension_type == "tool_approval":
                function_name = str(state.get("pending_function_name", "") or "").strip()
                if normalized_approval_decision not in {"approved", "rejected"}:
                    normalized_approval_decision = self._infer_tool_approval_decision_from_reply(normalized_user_reply)
                if normalized_approval_decision not in {"approved", "rejected"}:
                    clarification_question = self._build_tool_approval_clarification_question(state)
                    state["clarification_question"] = clarification_question
                    self._suspended_agent_state = state
                    return {
                        "ok": True,
                        "lifecycle": "waiting_approval",
                        "final_text": "",
                        "tool_trace": list(tool_trace),
                        "structured_output": {},
                        "awaiting": {
                            "type": "approval",
                            "question": clarification_question,
                            "approval_id": str(state.get("pending_approval_id", "") or "").strip(),
                            "tool_name": function_name,
                            "tool_arguments": dict(state.get("pending_function_args") or {}),
                            "policy": dict(state.get("pending_policy_result") or {}),
                            "clarification_required": True,
                        },
                    }

                resolution_result = self.resolveToolApproval(
                    ApprovalId=state.get("pending_approval_id", ""),
                    Decision=normalized_approval_decision,
                )
                if not isinstance(resolution_result, dict) or not resolution_result.get("ok", False):
                    return {
                        "ok": False,
                        "lifecycle": "failed",
                        "error": str((resolution_result or {}).get("error") or "Failed to resolve delegated tool approval."),
                        "tool_trace": list(tool_trace),
                        "structured_output": {},
                    }
                if normalized_approval_decision == "approved":
                    resolved_tool_result = resolution_result.get("tool_result", {}) if isinstance(resolution_result, dict) else {}
                else:
                    resolved_tool_result = {
                        "ok": False,
                        "approval_rejected": True,
                        "error": f"用户拒绝授权调用 {function_name or '该工具'}。",
                    }
                tmp_agent_message = self._replace_pending_tool_approval_result(
                    state["tmp_agent_message"],
                    pending_tool_call=state.get("pending_tool_call") or {},
                    function_name=function_name,
                    tool_result=resolved_tool_result,
                    approval_question=state.get("approval_question", ""),
                )
                if normalized_approval_decision == "approved" and function_name != SUMMARY_TOOL_NAME:
                    executed_tool_calls += 1
                    skill_name = self.tool_skill_map.get(function_name, "")
                    tool_trace.append(
                        {
                            "tool_name": function_name,
                            "skill_name": skill_name,
                            "result_excerpt": self._stringify_tool_result(resolved_tool_result),
                            "args_excerpt": self._stringify_tool_result(
                                state.get("pending_function_args") or {},
                                max_chars=200,
                            ),
                        }
                    )
                return self._run_detached_agent_loop(
                    tmp_agent_message=tmp_agent_message,
                    model=state.get("model", model),
                    runtime_config=runtime_config,
                    runtime_settings=runtime_settings,
                    tool_trace=tool_trace,
                    executed_tool_calls=executed_tool_calls,
                    task_control=task_control,
                )

            if not normalized_user_reply:
                question = str((state.get("ask_user_payload") or {}).get("question", "")).strip()
                self._suspended_agent_state = state
                return {
                    "ok": True,
                    "lifecycle": "waiting_input",
                    "final_text": "",
                    "tool_trace": list(tool_trace),
                    "structured_output": {},
                    "awaiting": {
                        "type": "input",
                        "question": question,
                        "options": [
                            str(option).strip()
                            for option in ((state.get("ask_user_payload") or {}).get("options") or [])
                            if str(option).strip()
                        ],
                        "context": str((state.get("ask_user_payload") or {}).get("context", "")).strip(),
                    },
                }

            tmp_agent_message = list(state["tmp_agent_message"])
            tmp_agent_message.append(
                {
                    "role": "tool",
                    "tool_call_id": state["ask_user_tool_call_id"],
                    "name": state["ask_user_tool_name"],
                    "content": json.dumps(
                        {
                            "ok": True,
                            "user_reply": normalized_user_reply,
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            return self._run_detached_agent_loop(
                tmp_agent_message=tmp_agent_message,
                model=state.get("model", model),
                runtime_config=runtime_config,
                runtime_settings=runtime_settings,
                tool_trace=tool_trace,
                executed_tool_calls=executed_tool_calls,
                task_control=task_control,
            )
        finally:
            self._current_turn_tool_trace = previous_tool_trace
            self._current_turn_agent_request = previous_turn_request
            self._set_active_tool_session_context(previous_session_context)
            self._turn_active_tools_snapshot = previous_turn_snapshot
            self._turn_loaded_deferred_tool_names = previous_loaded_deferred
            self._subagent_depth = previous_subagent_depth
