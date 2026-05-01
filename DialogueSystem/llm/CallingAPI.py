"""统一封装外部 LLM、Embedding 与 Rerank HTTP 调用。

项目中的其他模块不应各自手写请求逻辑，而应优先调用这里的函数。这样可以把：
- 重试策略
- 请求头与请求体结构
- Kimi 兼容行为
集中在一个地方维护。
"""

import copy
import json
import logging
import threading
import time
from collections import deque
from datetime import datetime
from typing import Iterator, Optional

import requests

from project_config import get_llm_config, get_project_config, normalize_reasoning_effort


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM call log collector – ring buffer for the LLM Inspector frontend page
# ---------------------------------------------------------------------------
_llm_call_log: deque = deque(maxlen=200)
_llm_call_log_lock = threading.Lock()
_llm_call_log_counter = 0


def record_llm_call(
    *,
    caller: str,
    messages: list,
    model_key: str = "",
    model_name: str = "",
    thinking: bool = False,
    json_mode: bool = False,
    stream: bool = False,
    reasoning_effort: Optional[str] = None,
    extra: Optional[dict] = None,
):
    """Record a single LLM API call into the in-memory ring buffer."""
    global _llm_call_log_counter
    with _llm_call_log_lock:
        _llm_call_log_counter += 1
        entry = {
            "id": _llm_call_log_counter,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "_start_time": time.monotonic(),
            "status": "running",
            "caller": caller or "unknown",
            "model_key": model_key,
            "model_name": model_name,
            "thinking": thinking,
            "json_mode": json_mode,
            "stream": stream,
            "messages": copy.deepcopy(list(messages or [])),
        }
        if thinking and reasoning_effort:
            entry["reasoning_effort"] = normalize_reasoning_effort(reasoning_effort)
        if extra:
            entry["extra"] = copy.deepcopy(extra)
        _llm_call_log.append(entry)
        return entry["id"]


def finalize_llm_call(
    call_id: int,
    *,
    response_message: Optional[dict] = None,
    error: str = "",
    extra: Optional[dict] = None,
):
    """Finalize a previously recorded LLM call with response metadata."""
    with _llm_call_log_lock:
        target_entry = None
        for entry in reversed(_llm_call_log):
            if int(entry.get("id", 0) or 0) == int(call_id or 0):
                target_entry = entry
                break
        if target_entry is None:
            return
        if response_message:
            target_entry.setdefault("messages", []).append(copy.deepcopy(response_message))
        if extra:
            merged_extra = dict(target_entry.get("extra") or {})
            merged_extra.update(copy.deepcopy(extra))
            target_entry["extra"] = merged_extra
        target_entry["status"] = "failed" if error else "completed"
        target_entry["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        start = target_entry.pop("_start_time", None)
        if start is not None:
            target_entry["duration_ms"] = round((time.monotonic() - start) * 1000)
        if error:
            target_entry["error"] = str(error)


def append_llm_call_message(call_id: int, message: dict):
    """Append an additional message (e.g. tool result) to an existing log entry."""
    if not message:
        return
    with _llm_call_log_lock:
        for entry in reversed(_llm_call_log):
            if int(entry.get("id", 0) or 0) == int(call_id or 0):
                entry.setdefault("messages", []).append(copy.deepcopy(message))
                return


def get_llm_call_logs(since_id: int = 0) -> list:
    """Return a copy of logged LLM calls, optionally filtered to entries after *since_id*."""
    with _llm_call_log_lock:
        entries = _llm_call_log if since_id <= 0 else [e for e in _llm_call_log if e["id"] > since_id]
        return [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]


def _build_json_headers(api_key: str) -> dict:
    """构造本模块所有 JSON HTTP 请求共用的请求头。"""
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _post_json_with_retry(
    *,
    session: requests.Session,
    url: str,
    headers: dict,
    payload: dict,
    request_name: str,
    max_attempts: int = 3,
):
    """发送 JSON POST 请求，并统一处理重试与日志输出。"""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = session.post(url, headers=headers, data=json.dumps(payload))
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            if attempt < max_attempts:
                logger.error("%s 调用失败(第%s/%s次): %s，将于5秒后重试", request_name, attempt, max_attempts, exc)
                time.sleep(5)
            else:
                logger.error("%s 调用失败，已达到最大重试次数: %s", request_name, exc)
    raise last_error


def _extract_text_from_content(content) -> str:
    """从 OpenAI 兼容响应的 content 字段中提取纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        return "".join(text_parts)
    return ""


def _extract_text_from_llm_result(result: dict) -> str:
    """从非流式响应中提取助手文本。"""
    choices = result.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return _extract_text_from_content(message.get("content"))


def _build_logged_response_message(result: dict) -> dict:
    """Build a compact assistant message for the inspector from a raw response."""
    choices = result.get("choices") or []
    if not choices:
        return {"role": "assistant", "content": json.dumps(result or {}, ensure_ascii=False, indent=2)}
    message = choices[0].get("message") or {}
    role = str(message.get("role") or "assistant")
    content = _extract_text_from_content(message.get("content"))
    if content:
        return {"role": role, "content": content}
    if message.get("tool_calls"):
        return {
            "role": role,
            "content": json.dumps({"tool_calls": message.get("tool_calls") or []}, ensure_ascii=False, indent=2),
        }
    return {"role": role, "content": json.dumps(message or {}, ensure_ascii=False, indent=2)}


def _extract_usage_from_llm_result(result: dict) -> dict:
    """Extract normalized token usage from an OpenAI-compatible response."""
    usage = dict((result or {}).get("usage") or {})
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", 0) or 0)
    if total_tokens <= 0 and (prompt_tokens > 0 or completion_tokens > 0):
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _extract_text_from_stream_event(event: dict) -> str:
    """从流式事件中提取当前增量文本。"""
    choices = event.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    return _extract_text_from_content(delta.get("content"))


def _iter_sse_text_chunks(response: requests.Response) -> Iterator[str]:
    """解析 OpenAI 兼容 SSE 响应，并按文本增量产出。"""
    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line:
            continue
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data:
            continue
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            logger.warning("忽略无法解析的流式事件: %s", data)
            continue
        text_chunk = _extract_text_from_stream_event(event)
        if text_chunk:
            yield text_chunk


def _post_stream_with_retry(
    *,
    session: requests.Session,
    url: str,
    headers: dict,
    payload: dict,
    request_name: str,
    max_attempts: int = 3,
) -> Iterator[str]:
    """发送流式 POST 请求；仅在首个分片到达前允许重试，避免重复输出。"""
    last_error = None
    has_yielded = False
    for attempt in range(1, max_attempts + 1):
        try:
            with session.post(url, headers=headers, data=json.dumps(payload), stream=True) as response:
                response.raise_for_status()
                for text_chunk in _iter_sse_text_chunks(response):
                    has_yielded = True
                    yield text_chunk
                return
        except Exception as exc:
            last_error = exc
            if has_yielded or attempt >= max_attempts:
                logger.error("%s 流式调用失败: %s", request_name, exc)
                break
            logger.error(
                "%s 流式调用失败(第%s/%s次): %s，将于5秒后重试",
                request_name,
                attempt,
                max_attempts,
                exc,
            )
            time.sleep(5)
    raise last_error


def _should_use_stream_by_default(model_name: str, Json: bool, stream: Optional[bool]) -> bool:
    """根据模型名决定默认是否启用流式输出。"""
    if stream is not None:
        return stream
    if Json:
        return False
    return "gpt" in str(model_name).lower()



def call_LLM(
    messages,
    Model,
    session,
    Thinking,
    Json: bool = False,
    stream: Optional[bool] = None,
    caller: str = "",
    reasoning_effort: str = "high",
):
    """调用对话模型接口。

    默认返回完整文本；当 ``stream=True`` 时，返回文本分片迭代器。
    若未显式指定 ``stream``，则模型名包含 ``gpt`` 时默认启用流式输出；
    ``Json=True`` 时始终默认走非流式，避免破坏依赖完整 JSON 字符串的调用方。
    """
    llm_config = get_llm_config(Model)
    use_stream = _should_use_stream_by_default(llm_config["modelName"], Json, stream)
    normalized_reasoning_effort = normalize_reasoning_effort(reasoning_effort)

    # ---- record this call for the LLM Inspector ----
    log_id = record_llm_call(
        caller=caller,
        messages=messages,
        model_key=str(Model),
        model_name=llm_config["modelName"],
        thinking=bool(Thinking),
        json_mode=bool(Json),
        stream=use_stream,
        reasoning_effort=normalized_reasoning_effort if Thinking else None,
    )
    if Thinking:
        think_setting = {
            "enable_thinking": True,
            "thinking": {"type": "enabled"},
            "reasoning": {"enabled": True},
        }
    else:
        think_setting = {
            "enable_thinking": False,
            "thinking": {"type": "disabled"},
            "reasoning": {"enabled": False},
        }

    payload = {
        "model": llm_config["modelName"],
        "messages": messages,
        "stream": use_stream,
        **think_setting,
    }
    if Thinking:
        payload["reasoning_effort"] = normalized_reasoning_effort
    if Json:
        payload["response_format"] = {"type": "json_object"}
    headers = _build_json_headers(llm_config["API"])
    if use_stream:
        stream_iterator = _post_stream_with_retry(
            session=session,
            url=llm_config["url"],
            headers=headers,
            payload=payload,
            request_name="当前 LLM",
        )

        def _logged_stream_iterator():
            chunks = []
            try:
                for text_chunk in stream_iterator:
                    chunks.append(text_chunk)
                    yield text_chunk
                finalize_llm_call(
                    log_id,
                    response_message={"role": "assistant", "content": "".join(chunks)},
                )
            except Exception as exc:
                finalize_llm_call(
                    log_id,
                    response_message={"role": "assistant", "content": "".join(chunks)} if chunks else None,
                    error=str(exc),
                )
                raise

        return _logged_stream_iterator()
    try:
        result = _post_json_with_retry(
        session=session,
        url=llm_config["url"],
        headers=headers,
        payload=payload,
        request_name="当前 LLM",
    )
    except Exception as exc:
        finalize_llm_call(log_id, error=str(exc))
        raise
    finalize_llm_call(
        log_id,
        response_message=_build_logged_response_message(result),
        extra={"usage": _extract_usage_from_llm_result(result)},
    )
    return _extract_text_from_llm_result(result)


def call_Embedding(text, session) -> list:
    """调用 Embedding 接口并返回向量结果。"""
    config = get_project_config()
    local_session = session or requests.Session()
    payload = {
        "model": config["Embedding_Setting"]["qwen_embedding_modelName"],
        "input": text,
        "dimensions": 1024,
        "encoding_format": "float",
    }
    result = _post_json_with_retry(
        session=local_session,
        url=config["Embedding_Setting"]["qwen_embedding_url"],
        headers=_build_json_headers(config["Embedding_Setting"]["qwen_key"]),
        payload=payload,
        request_name="当前 Embedding",
    )
    return result["data"][0]["embedding"]


def call_rerank(ResultList: list, session, text: str) -> list:
    """调用 Rerank 接口并返回标准化后的排序结果。"""
    config = get_project_config()
    local_session = session or requests.Session()
    payload = {
        "model": config["Rerank_Setting"]["qwen_rerank_modelName"],
        "documents": ResultList,
        "query": text,
        "top_n": config["VectorSetting"]["Rerank_Top_k"],
        "return_documents": True,
    }
    result = _post_json_with_retry(
        session=local_session,
        url=config["Rerank_Setting"]["qwen_rerank_url"],
        headers=_build_json_headers(config["Rerank_Setting"]["qwen_rerank_key"]),
        payload=payload,
        request_name="当前 Rerank",
    )
    return [
        {
            "relevance_score": item.get("relevance_score"),
            "text": item.get("document"),
        }
        for item in result["results"]
    ]
