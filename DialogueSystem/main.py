"""DialogueSystem 的主运行时模块，负责对话循环、RAG、Agent 调用与摘要/提醒协同。"""

from __future__ import annotations

import asyncio
import base64
import copy
import fnmatch
import importlib
import json
import logging
import os
import queue
import re
import sys
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from datetime import date, datetime, timedelta

# Support `python DialogueSystem/main.py` by exposing the repository root
# before any package imports run.
if __package__ in {None, ""}:
    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    _PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)

import requests
from qdrant_client.models import FieldCondition, Filter, MatchValue, PointIdsList

SentenceTransformer = None
_SENTENCE_TRANSFORMER_IMPORT_ERROR = None

try:
    from DialogueSystem.memory.memory_storage import (
        PersistentCoreMemoryStore,
        RetrievalCacheRepository,
        TopicArchiveRepository,
    )
    from DialogueSystem.config.paths import (
        AUTONOMOUS_TASK_DB_PATH,
        DATA_DIR,
        HISTORY_DIR,
        PERSISTENT_CORE_MEMORY_PATH,
        PROJECT_ROOT,
        RETRIEVAL_CACHE_DB_PATH,
        SCHEDULE_DB_PATH,
        SCRIPT_DIR,
        TOPIC_ARCHIVE_DB_PATH,
    )
    from DialogueSystem.config.resources import (
        build_agent_skill_prompt,
        build_skill_activation_payload,
        invalidate_resource_caches,
        load_intention_ability_definitions,
        load_prompt_text,
        load_skill_definitions,
        render_prompt_text,
        load_tool_definitions
    )
    from DialogueSystem.services.schedule_system import (
        ScheduleRepository,
    )
    from DialogueSystem.skill_system.skill_runtime import load_skill_tool_registry
    from DialogueSystem.runtime.dynamic_tools import DynamicToolRegistry
    from DialogueSystem.runtime.mcp_runtime import MCPRuntime
    from DialogueSystem.agent.subagent_runtime import SubAgentRuntime
    from DialogueSystem.autonomous.autonomous_executor import run_daily_autonomous_session
    from DialogueSystem.autonomous.autonomous_task_log import AutonomousTaskLog
    from DialogueSystem.agent.agent_session import (
        AgentSession,
        build_default_agent_turn_request,
        normalize_agent_turn_request,
    )
    from DialogueSystem.agent.token_counter import TokenCounter
    from DialogueSystem.policy.tool_policy import ToolPolicyEngine
    from DialogueSystem.policy.tool_metadata import get_tool_metadata
    from DialogueSystem.security.redaction import redact_payload, redact_text
    from DialogueSystem.security.checkpoint import create_file_checkpoint
    from DialogueSystem.security.subagent_policy import build_subagent_policy
    from DialogueSystem.backends.isolated_terminal import run_command as run_isolated_terminal_command
    from DialogueSystem.backends.local_terminal import run_command as run_local_terminal_command
    from DialogueSystem.skill_system.skill_manager import (
        create_or_update_skill,
        delete_skill,
        list_skills as list_installed_skills,
    )
    from DialogueSystem.skill_system.skill_marketplace import (
        export_skill as marketplace_export_skill,
        import_skill as marketplace_import_skill,
        list_exportable_skills as marketplace_list_skills,
        browse_skill_registry as marketplace_browse_registry,
        install_from_registry as marketplace_install_from_registry,
    )
except ImportError:
    from DialogueSystem.memory.memory_storage import (
        PersistentCoreMemoryStore,
        RetrievalCacheRepository,
        TopicArchiveRepository,
    )
    from DialogueSystem.config.paths import (
        AUTONOMOUS_TASK_DB_PATH,
        DATA_DIR,
        HISTORY_DIR,
        PERSISTENT_CORE_MEMORY_PATH,
        PROJECT_ROOT,
        RETRIEVAL_CACHE_DB_PATH,
        SCHEDULE_DB_PATH,
        SCRIPT_DIR,
        TOPIC_ARCHIVE_DB_PATH,
    )
    from DialogueSystem.config.resources import (
        build_agent_skill_prompt,
        build_skill_activation_payload,
        invalidate_resource_caches,
        load_intention_ability_definitions,
        load_prompt_text,
        load_skill_definitions,
        render_prompt_text,
        load_tool_definitions,
    )
    from DialogueSystem.services.schedule_system import (
        ScheduleRepository,
    )
    from DialogueSystem.skill_system.skill_runtime import load_skill_tool_registry
    from DialogueSystem.runtime.dynamic_tools import DynamicToolRegistry
    from DialogueSystem.runtime.mcp_runtime import MCPRuntime
    from DialogueSystem.agent.subagent_runtime import SubAgentRuntime
    from DialogueSystem.autonomous.autonomous_executor import run_daily_autonomous_session
    from DialogueSystem.autonomous.autonomous_task_log import AutonomousTaskLog
    from DialogueSystem.agent.agent_session import (
        AgentSession,
        build_default_agent_turn_request,
        normalize_agent_turn_request,
    )
    from DialogueSystem.agent.token_counter import TokenCounter
    from DialogueSystem.policy.tool_policy import ToolPolicyEngine
    from DialogueSystem.policy.tool_metadata import get_tool_metadata
    from security.redaction import redact_payload, redact_text
    from security.checkpoint import create_file_checkpoint
    from security.subagent_policy import build_subagent_policy
    from backends.isolated_terminal import run_command as run_isolated_terminal_command
    from backends.local_terminal import run_command as run_local_terminal_command
    from DialogueSystem.skill_system.skill_manager import create_or_update_skill, delete_skill, list_skills as list_installed_skills
    from DialogueSystem.skill_system.skill_marketplace import (
        export_skill as marketplace_export_skill,
        import_skill as marketplace_import_skill,
        list_exportable_skills as marketplace_list_skills,
        browse_skill_registry as marketplace_browse_registry,
        install_from_registry as marketplace_install_from_registry,
    )

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from logging_utils import (
    build_component_log_path,
    build_qdrant_http_patterns,
    build_sibling_log_path,
    configure_logger,
    configure_root_logger,
    read_recent_log_tail
)
from project_config import (
    get_character_replacements,
    get_llm_config,
    get_llm_capabilities,
    get_model_select_model_key,
    get_model_select_task_config,
    get_project_config,
    get_qdrant_collection_config,
    iter_model_select_model_keys,
    normalize_reasoning_effort,
)
from DialogueSystem.agent import agent_runtime as agent_runtime_helpers
from DialogueSystem.agent import tool_rendering as tool_rendering_helpers
from DialogueSystem.memory import summary_worker_runtime as summary_worker_helpers
from DialogueSystem.memory import topic_history as topic_history_helpers

try:
    from DialogueSystem.llm.CallingAPI import (
        _extract_usage_from_llm_result,
        append_llm_call_message,
        call_LLM,
        finalize_llm_call,
        record_llm_call,
    )
    from DialogueSystem.memory.ChatContext import (
        SilenceContext,
        SummaryContext,
        PERSISTENT_CORE_LAYER,
        TOPIC_WORKING_MEMORY_LAYER,
        _build_core_memory_item,
        build_core_memory_system_message,
        build_recent_experiences_system_message,
        build_default_core_memory_state,
        clear_topic_working_memory_state,
        data_queue,
        extract_persistent_core_memory_state,
        get_core_memory_section_specs,
        hydrate_core_memory_state,
        is_core_memory_system_message,
        is_recent_experiences_system_message,
        normalize_core_memory_state,
        rebuild_context_from_topic,
        reset_summary_context_state,
        update_core_memory_state,
    )
except ImportError:
    from DialogueSystem.llm.CallingAPI import (
        _extract_usage_from_llm_result,
        append_llm_call_message,
        call_LLM,
        finalize_llm_call,
        record_llm_call,
    )
    from DialogueSystem.memory.ChatContext import (
        SilenceContext,
        SummaryContext,
        PERSISTENT_CORE_LAYER,
        TOPIC_WORKING_MEMORY_LAYER,
        _build_core_memory_item,
        build_core_memory_system_message,
        build_recent_experiences_system_message,
        build_default_core_memory_state,
        clear_topic_working_memory_state,
        data_queue,
        extract_persistent_core_memory_state,
        get_core_memory_section_specs,
        hydrate_core_memory_state,
        is_core_memory_system_message,
        is_recent_experiences_system_message,
        normalize_core_memory_state,
        rebuild_context_from_topic,
        reset_summary_context_state,
        update_core_memory_state,
    )
_QDRANT_IMPORT_ERROR = None
try:
    from MemorySystem.Qdrant import Qdrant
except ImportError as exc:
    Qdrant = None
    _QDRANT_IMPORT_ERROR = exc


DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_CONTEXT_MEMORY_RUNTIME_CONFIG = {
    "enabled": True,
    "update_trigger": "topic_switch",
    "update_min_new_messages": 6,
    "update_on_empty": False,
}
DEFAULT_AGENT_RUNTIME_CONFIG = {
    "max_tool_calls": 5,
    "max_consecutive_same_tool_calls": 3,
}
DEFAULT_BROWSER_AGENT_RUNTIME_CONFIG = {
    "max_tool_calls": 12,
    "max_consecutive_same_tool_calls": 8,
    "ephemeral_observation_limit": 3,
    "ephemeral_visual_limit": 1,
}
DEFAULT_SKILL_EVOLUTION_CONFIG = {
    "enabled": True,
    "min_tool_calls": 3,
    "similarity_threshold": 0.70,
    "model": "",
}
DEFAULT_INTENT_ROUTER_RUNTIME_CONFIG = {
    "method": "vector",
    "enabled": True,
    "high_confidence_threshold": 0.78,
    "low_confidence_threshold": 0.55,
    "candidate_limit": 4,
    "llm_fallback": {
        "enabled": True,
        "model": "qwen_flash",
        "thinking": False,
        "json_mode": True,
        "reasoning_effort": "high",
    },
}
DEFAULT_AGENT_RETRIEVAL_CACHE_CONFIG = {
    "enabled": True,
    "match_model": "qwen_flash",
    "match_thinking": False,
    "match_json_mode": True,
    "match_reasoning_effort": "low",
    "max_cache_inject_chars": 8000,
    "cacheable_tools": [
        "webSearch",
        "webFetch",
        "browserExtractPage",
        "readAutonomousTaskArtifact",
        "searchAutonomousTaskArtifacts",
        "searchLongTermMemory",
        "searchFullText",
        "readLocalFile",
    ],
}
ROLE_PLAY_TASK_NAME = "RolePlay"
SUMMARY_TOOL_NAME = "summarizeToolResults"
TOOL_SEARCH_TOOL_NAME = "toolSearch"
WEB_SEARCH_TOOL_NAME = "webSearch"
KIMI_WEB_SEARCH_TOOL_NAME = "$web_search"
MAX_DEFERRED_TOOL_ACTIVATIONS_PER_TURN = 6
BROWSER_TOOL_NAMES = frozenset({
    "browserClick",
    "browserCloseTab",
    "browserExtractPage",
    "browserGoBack",
    "browserListTabs",
    "browserNavigate",
    "browserOpenTab",
    "browserPressKey",
    "browserReadLinkedPage",
    "browserScroll",
    "browserScreenshot",
    "browserSearch",
    "browserSelectTab",
    "browserSnapshot",
    "browserType",
    "browserWait",
})
DEFAULT_CORE_TOOL_NAMES = frozenset({
    TOOL_SEARCH_TOOL_NAME,
    SUMMARY_TOOL_NAME,
    "activateSkill",
    "controlSelf",
    "getTime",
    "getLocation",
    "askUser",
    "resolveToolApproval",
})
VISIBLE_AGENT_STEP_MAX_COUNT = 12
HISTORICAL_AGENT_STEP_MAX_COUNT = 120
CLEARED_DIALOGUE_SNAPSHOT_PATH = os.path.join(DATA_DIR, "cleared_dialogue_snapshot.json")
DEFAULT_TOPIC_POSTPROCESS_LLM_CONFIG = {
    "enabled": True,
    "topic_same": {
        "enabled": True,
        "model": "",
        "thinking": True,
        "json_mode": True,
    },
    "topic_archive_summary": {
        "enabled": True,
        "model": "",
        "thinking": False,
        "json_mode": False,
    },
    "context_summary": {
        "enabled": True,
        "model": "",
        "thinking": False,
        "json_mode": False,
    },
    "core_memory_update": {
        "enabled": True,
        "model": "",
        "thinking": True,
        "json_mode": True,
    },
}
def _ensure_sentence_transformer_available():
    """在真正需要 embedding 功能时再校验依赖是否可用。"""
    global SentenceTransformer, _SENTENCE_TRANSFORMER_IMPORT_ERROR
    if SentenceTransformer is None and _SENTENCE_TRANSFORMER_IMPORT_ERROR is None:
        try:
            SentenceTransformer = importlib.import_module("sentence_transformers").SentenceTransformer
        except Exception as exc:
            _SENTENCE_TRANSFORMER_IMPORT_ERROR = exc
    if SentenceTransformer is None:
        raise RuntimeError(
            "sentence_transformers is required for memory/context embedding features. "
            "Please install it before initializing the dialogue runtime."
        )
    return SentenceTransformer


def _ensure_qdrant_available():
    """在真正需要向量库功能时再校验依赖是否可用。"""
    if Qdrant is None:
        detail = ""
        if _QDRANT_IMPORT_ERROR is not None:
            detail = f" Original import error: {_QDRANT_IMPORT_ERROR!r}"
        raise RuntimeError(
            "Vector storage backend is unavailable because MemorySystem.Qdrant could not be imported. "
            "Please restore the missing dependency or broken import path before initializing the dialogue runtime."
            f"{detail}"
        )
    return Qdrant

script_dir = SCRIPT_DIR
project_root = PROJECT_ROOT
config = get_project_config()
qdrant_setting = config.get("Qdrant_Setting", {})
log_path = build_component_log_path(script_dir, "dialogue_system")
db_request_log_path = build_sibling_log_path(log_path, "db_requests")
autonomous_log_path = build_component_log_path(script_dir, "autonomous_task_mode")
configure_root_logger(
    log_path,
    database_log_path=db_request_log_path,
    database_message_patterns=build_qdrant_http_patterns(
        qdrant_setting.get("host", "127.0.0.1"),
        qdrant_setting.get("port", 6333)
    )
)
configure_logger(
    "autonomous_task_mode",
    autonomous_log_path,
    with_console=False,
    propagate=True,
)
logger = logging.getLogger(__name__)
autonomous_logger = logging.getLogger("autonomous_task_mode.runtime")
def refresh_runtime_config():
    """刷新项目配置并同步运行时缓存。"""
    global config, qdrant_setting
    config = get_project_config()
    qdrant_setting = config.get("Qdrant_Setting", {})
    return config


def get_LLM(manufacturer: str = "qwen"):
    """读取指定模型键对应的 LLM 配置。"""
    return get_llm_config(manufacturer, get_project_config())


def setSystemPrompt(ChatType: str):
    """按提示词名称加载 Markdown system prompt。"""
    return render_prompt_text(ChatType)


class Selena:
    """Selena 对话主运行时，负责上下文管理、RAG、Agent 和摘要协同。"""

    def __init__(self):
        """初始化运行时状态、上下文容器与后台工作线程。"""
        self.config = refresh_runtime_config()
        self.session = self.create_session()
        self.model = self._get_model_select_task_config("Agent").get("model") or "qwen"
        self.local_embedding_model = None
        self.input_num = 0
        self.summary_num = 0  # ????????????????
        self.topicGroup = 0
        self.chat_cycle = True
        self.agent_cycle = False
        self._runtime_initialized = False
        self._runtime_lock = threading.RLock()
        self._shutdown_complete = False
        self._context_revision = 0
        self._agent_session_local = threading.local()
        self._root_agent_session = AgentSession("main")
        self._historical_agent_steps = []
        self.project_root = PROJECT_ROOT
        self.tool_policy_engine = ToolPolicyEngine(self)
        self._recent_tool_security_events = []
        self._initialize_agent_session_runtime()
        self._initialize_autonomous_mode_state()

        self._initialize_memory_storage()
        self._initialize_message_contexts()
        self._initialize_context_memory_state()
        self._initialize_vector_search_state()
        self.summary_num = 0  # 摘要计数器，由摘要线程异步维护。
        self._initialize_summary_worker_state()
        self._initialize_topic_tracking()
        self._initialize_retrieval_cache()
        self._initialize_persistent_system_contexts()
        self._initialize_append_worker()
        self._initialize_postprocess_worker()
        self._initialize_schedule_system()
        self._initialize_dynamic_extension_runtime()

        self.function_map = self._build_function_map()
        self._refresh_runtime_tools(initial=True)
        self._initialize_autonomous_mode()

    def _initialize_agent_session_runtime(self):
        """Initialize the root agent session runtime state."""
        self._current_turn_agent_request = build_default_agent_turn_request()
        self._current_turn_visible_agent_steps = []
        self._current_turn_visible_agent_step_seq = 0
        self._active_tool_session_context = None
        self._subagent_depth = 0
        self._pending_tool_approvals = []
        self._pending_tool_approval_seq = 0
        self._turn_loaded_deferred_tool_names = set()
        self._turn_active_tools_snapshot = []
        self._suspended_agent_state = None
        self._agent_interrupt_queue = queue.Queue()
        self._current_turn_tool_trace = []
        self._current_turn_retrieval_cache_ids = []
        self._pending_subagent_results = queue.Queue()
        self._run_agent_lock = threading.Lock()

    def _get_current_agent_session(self) -> AgentSession:
        active_session = getattr(self._agent_session_local, "active_session", None)
        return active_session or self._root_agent_session

    def create_detached_agent_session(
        self,
        *,
        session_name: str,
        user_input: str = "",
        agent_type: str = "general",
    ) -> AgentSession:
        """Create an isolated child session that inherits only safe parent state."""
        normalized_agent_type = str(agent_type or "general").strip().lower() or "general"
        turn_request = normalize_agent_turn_request(
            {
                "user_input": user_input,
                "route": "agent",
                "selected_ability": "",
                "reason": f"detached:{normalized_agent_type}",
                "ranked_candidates": [],
            }
        )
        return AgentSession(
            session_name,
            inherited_state=self._get_current_agent_session().state,
            turn_request=turn_request,
        )

    @contextmanager
    def _activate_agent_session(self, agent_session: AgentSession):
        previous_session = getattr(self._agent_session_local, "active_session", None)
        self._agent_session_local.active_session = agent_session
        try:
            yield agent_session
        finally:
            if previous_session is None:
                try:
                    delattr(self._agent_session_local, "active_session")
                except AttributeError:
                    pass
            else:
                self._agent_session_local.active_session = previous_session

    def _get_agent_session_state_field(self, field_name: str):
        return getattr(self._get_current_agent_session().state, field_name)

    def _set_agent_session_state_field(self, field_name: str, value):
        setattr(self._get_current_agent_session().state, field_name, value)

    @property
    def _current_turn_agent_request(self):
        return self._get_agent_session_state_field("current_turn_agent_request")

    @_current_turn_agent_request.setter
    def _current_turn_agent_request(self, value):
        self._set_agent_session_state_field(
            "current_turn_agent_request",
            normalize_agent_turn_request(value),
        )

    @property
    def _current_turn_visible_agent_steps(self):
        return self._get_agent_session_state_field("current_turn_visible_agent_steps")

    @_current_turn_visible_agent_steps.setter
    def _current_turn_visible_agent_steps(self, value):
        self._set_agent_session_state_field("current_turn_visible_agent_steps", list(value or []))

    @property
    def _current_turn_visible_agent_step_seq(self):
        return self._get_agent_session_state_field("current_turn_visible_agent_step_seq")

    @_current_turn_visible_agent_step_seq.setter
    def _current_turn_visible_agent_step_seq(self, value):
        self._set_agent_session_state_field("current_turn_visible_agent_step_seq", int(value or 0))

    @property
    def _active_tool_session_context(self):
        return self._get_agent_session_state_field("active_tool_session_context")

    @_active_tool_session_context.setter
    def _active_tool_session_context(self, value):
        self._set_agent_session_state_field("active_tool_session_context", value)

    @property
    def _subagent_depth(self):
        return self._get_agent_session_state_field("subagent_depth")

    @_subagent_depth.setter
    def _subagent_depth(self, value):
        self._set_agent_session_state_field("subagent_depth", int(value or 0))

    @property
    def _pending_tool_approvals(self):
        return self._get_agent_session_state_field("pending_tool_approvals")

    @_pending_tool_approvals.setter
    def _pending_tool_approvals(self, value):
        self._set_agent_session_state_field("pending_tool_approvals", list(value or []))

    @property
    def _pending_tool_approval_seq(self):
        return self._get_agent_session_state_field("pending_tool_approval_seq")

    @_pending_tool_approval_seq.setter
    def _pending_tool_approval_seq(self, value):
        self._set_agent_session_state_field("pending_tool_approval_seq", int(value or 0))

    @property
    def _turn_loaded_deferred_tool_names(self):
        return self._get_agent_session_state_field("turn_loaded_deferred_tool_names")

    @_turn_loaded_deferred_tool_names.setter
    def _turn_loaded_deferred_tool_names(self, value):
        self._set_agent_session_state_field(
            "turn_loaded_deferred_tool_names",
            set(value or set()),
        )

    @property
    def _turn_active_tools_snapshot(self):
        return self._get_agent_session_state_field("turn_active_tools_snapshot")

    @_turn_active_tools_snapshot.setter
    def _turn_active_tools_snapshot(self, value):
        self._set_agent_session_state_field("turn_active_tools_snapshot", list(value or []))

    @property
    def _suspended_agent_state(self):
        return self._get_agent_session_state_field("suspended_agent_state")

    @_suspended_agent_state.setter
    def _suspended_agent_state(self, value):
        self._set_agent_session_state_field("suspended_agent_state", value)

    @property
    def _agent_interrupt_queue(self):
        return self._get_agent_session_state_field("agent_interrupt_queue")

    @_agent_interrupt_queue.setter
    def _agent_interrupt_queue(self, value):
        self._set_agent_session_state_field(
            "agent_interrupt_queue",
            value if isinstance(value, queue.Queue) else queue.Queue(),
        )

    @property
    def _current_turn_tool_trace(self):
        return self._get_agent_session_state_field("current_turn_tool_trace")

    @_current_turn_tool_trace.setter
    def _current_turn_tool_trace(self, value):
        self._set_agent_session_state_field("current_turn_tool_trace", list(value or []))

    @property
    def _current_turn_retrieval_cache_ids(self):
        return self._get_agent_session_state_field("current_turn_retrieval_cache_ids")

    @_current_turn_retrieval_cache_ids.setter
    def _current_turn_retrieval_cache_ids(self, value):
        self._set_agent_session_state_field("current_turn_retrieval_cache_ids", list(value or []))

    @property
    def _current_turn_browser_observations(self):
        return self._get_agent_session_state_field("current_turn_browser_observations")

    @_current_turn_browser_observations.setter
    def _current_turn_browser_observations(self, value):
        self._set_agent_session_state_field("current_turn_browser_observations", list(value or []))

    @property
    def _current_turn_browser_visual_artifacts(self):
        return self._get_agent_session_state_field("current_turn_browser_visual_artifacts")

    @_current_turn_browser_visual_artifacts.setter
    def _current_turn_browser_visual_artifacts(self, value):
        self._set_agent_session_state_field("current_turn_browser_visual_artifacts", list(value or []))

    def _build_system_message(self, prompt_name: str) -> dict:
        """根据提示词名称构造 system 消息。"""
        return {"role": "system", "content": setSystemPrompt(prompt_name)}

    def reload_config(self):
        """重新加载配置，并在集合变化时重置检索资源。"""
        latest_config = refresh_runtime_config()
        latest_model = get_model_select_model_key("Agent", latest_config) or self.model
        latest_intention_collection = get_qdrant_collection_config("intention", latest_config)
        latest_rag_collection = get_qdrant_collection_config("rag", latest_config)
        latest_memory_collection = get_qdrant_collection_config("memory", latest_config)

        collections_changed = (
            latest_intention_collection != self._intention_collection
            or latest_rag_collection != self._rag_collection
            or latest_memory_collection != self._memory_collection
        )
        if collections_changed:
            self._close_search_resources()
            self._query_embedding_cache.clear()

        self.config = latest_config
        self.model = latest_model
        self._intention_collection = latest_intention_collection
        self._rag_collection = latest_rag_collection
        self._memory_collection = latest_memory_collection
        self._refresh_runtime_tools()
        self._refresh_autonomous_mode_runtime()
        return self.config

    def _initialize_autonomous_mode_state(self):
        """初始化自主任务模式运行时状态。"""
        self._autonomous_task_log = None
        self._token_counter = None
        self._autonomous_idle_threshold = 0
        self._last_user_interaction_at = time.time()
        self._autonomous_mode_active = False
        self._autonomous_interrupt_event = threading.Event()
        self._autonomous_state_lock = threading.RLock()
        self._autonomous_session_lock = threading.Lock()
        self._autonomous_stop_event = threading.Event()
        self._autonomous_worker_thread = None

    def _get_autonomous_mode_config(self) -> dict:
        configured = (self.config or {}).get("AutonomousTaskMode", {})
        if not isinstance(configured, dict):
            return {}
        return configured

    def _refresh_autonomous_mode_runtime(self) -> bool:
        """根据当前配置刷新自主任务模式的后台运行时。"""
        autonomous_config = self._get_autonomous_mode_config()
        self._autonomous_idle_threshold = max(
            0,
            self._safe_int(
                autonomous_config.get("idle_threshold_seconds"),
                300,
            ),
        )
        if not bool(autonomous_config.get("enabled", False)):
            return False

        if self._autonomous_task_log is None:
            self._autonomous_task_log = AutonomousTaskLog(AUTONOMOUS_TASK_DB_PATH)
        self._token_counter = TokenCounter(autonomous_config.get("token_limits", {}))

        if self._autonomous_worker_thread is None or not self._autonomous_worker_thread.is_alive():
            if self._autonomous_stop_event.is_set():
                self._autonomous_stop_event = threading.Event()
            self._autonomous_worker_thread = threading.Thread(
                target=self._run_autonomous_idle_monitor,
                name="autonomous-idle-monitor",
                daemon=True,
            )
            self._autonomous_worker_thread.start()
            autonomous_logger.info(
                "Autonomous task mode worker started | idle_threshold_seconds=%s",
                self._autonomous_idle_threshold,
            )
        return True

    def _initialize_autonomous_mode(self):
        """初始化自主任务模式后台线程。"""
        self._refresh_autonomous_mode_runtime()

    def _run_autonomous_idle_monitor(self):
        """后台线程：轮询空闲状态，满足条件时启动自主任务。"""
        while not self._autonomous_stop_event.is_set():
            try:
                autonomous_config = self._get_autonomous_mode_config()
                if not bool(autonomous_config.get("enabled", False)):
                    self._autonomous_stop_event.wait(30.0)
                    continue

                if self._autonomous_task_log is None or self._token_counter is None:
                    self._refresh_autonomous_mode_runtime()
                if self._autonomous_task_log is None or self._token_counter is None:
                    self._autonomous_stop_event.wait(30.0)
                    continue

                with self._autonomous_state_lock:
                    idle_seconds = time.time() - self._last_user_interaction_at
                    autonomous_active = self._autonomous_mode_active
                if autonomous_active:
                    self._autonomous_stop_event.wait(30.0)
                    continue

                max_daily_interrupts = max(
                    0,
                    self._safe_int(
                        autonomous_config.get("max_daily_interrupts"),
                        5,
                    ),
                )
                today = date.today().strftime("%Y-%m-%d")
                daily_interrupts = self._autonomous_task_log.get_daily_interrupt_count(today)
                if (
                    idle_seconds >= self._autonomous_idle_threshold
                    and (max_daily_interrupts <= 0 or daily_interrupts < max_daily_interrupts)
                    and self._autonomous_session_lock.acquire(blocking=False)
                ):
                    try:
                        autonomous_logger.info(
                            "Autonomous session trigger satisfied | session_date=%s | idle_seconds=%.1f | interrupt_count=%s",
                            today,
                            idle_seconds,
                            daily_interrupts,
                        )
                        self._run_autonomous_session()
                    finally:
                        self._autonomous_session_lock.release()
            except Exception:
                autonomous_logger.exception("Autonomous idle monitor error")
            self._autonomous_stop_event.wait(30.0)

    def _run_autonomous_session(self, session_date: str | None = None):
        """执行一次自主任务会话。被空闲监控线程调用。"""
        autonomous_config = self._get_autonomous_mode_config()
        if not bool(autonomous_config.get("enabled", False)):
            return None
        if self._autonomous_task_log is None or self._token_counter is None:
            if not self._refresh_autonomous_mode_runtime():
                return None
        today = str(session_date or date.today().strftime("%Y-%m-%d")).strip()
        if not today:
            today = date.today().strftime("%Y-%m-%d")
        session_record = self._autonomous_task_log.get_or_create_daily_session(today) or {}
        if session_record.get("session_finished_at"):
            autonomous_logger.info(
                "Skip autonomous session | session_date=%s | reason=already_finished | finish_reason=%s",
                today,
                session_record.get("finish_reason") or "",
            )
            return session_record

        with self._autonomous_state_lock:
            if self._autonomous_mode_active:
                autonomous_logger.info(
                    "Skip autonomous session | session_date=%s | reason=already_active",
                    today,
                )
                return None
            self._autonomous_mode_active = True
            self._autonomous_interrupt_event.clear()

        try:
            autonomous_logger.info("Autonomous session started | session_date=%s", today)
            session_result = run_daily_autonomous_session(
                dialogue_system=self,
                task_log=self._autonomous_task_log,
                token_counter=self._token_counter,
                interrupt_event=self._autonomous_interrupt_event,
                config=autonomous_config,
                session_date=today,
            )
            autonomous_logger.info(
                "Autonomous session finished | session_date=%s | finish_reason=%s | tasks_planned=%s | tasks_completed=%s | tasks_carried_over=%s | input_tokens=%s | output_tokens=%s",
                today,
                (session_result or {}).get("finish_reason") or "",
                (session_result or {}).get("tasks_planned", 0),
                (session_result or {}).get("tasks_completed", 0),
                (session_result or {}).get("tasks_carried_over", 0),
                (session_result or {}).get("total_input_tokens", 0),
                (session_result or {}).get("total_output_tokens", 0),
            )
            return session_result
        except Exception:
            autonomous_logger.exception("Autonomous session failed")
            return None
        finally:
            with self._autonomous_state_lock:
                self._autonomous_mode_active = False

    def _record_user_interaction(self):
        """记录用户交互时间，并在必要时请求中断自主任务。"""
        should_interrupt = False
        with self._autonomous_state_lock:
            self._last_user_interaction_at = time.time()
            should_interrupt = (
                self._autonomous_mode_active
                and not self._autonomous_interrupt_event.is_set()
            )
            if should_interrupt:
                self._autonomous_interrupt_event.set()

        if should_interrupt and self._autonomous_task_log is not None:
            try:
                today = date.today().strftime("%Y-%m-%d")
                interrupt_count = self._autonomous_task_log.increment_interrupt_count(today)
                autonomous_logger.info(
                    "User interaction interrupted autonomous mode | session_date=%s | interrupt_count=%s",
                    today,
                    interrupt_count,
                )
            except Exception:
                autonomous_logger.exception("Failed to increment autonomous interrupt count")

    def initialize_runtime(self):
        """完成模型预热、摘要线程启动与意图样本同步。"""
        with self._runtime_lock:
            if self._runtime_initialized:
                return
            self.reload_config()
            self.local_embedding_model = self.warmup_models(self._iter_model_select_warmup_keys())
            if self._uses_llm_intent_router():
                logger.info("Intention sync skipped | reason=llm_intent_router")
            else:
                self.ensure_intention_examples_synced(self.local_embedding_model)
            self.SummaryMemory()
            self._runtime_initialized = True

    def ensure_local_embedding_model(self):
        """Lazily initialize the local embedding model when runtime warmup was skipped."""
        with self._runtime_lock:
            if self.local_embedding_model is not None:
                return self.local_embedding_model
            sentence_transformer_cls = _ensure_sentence_transformer_available()
            os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
            os.environ["TRANSFORMERS_VERBOSITY"] = "error"
            local_embedding_model = sentence_transformer_cls(
                DEFAULT_EMBEDDING_MODEL,
                local_files_only=True,
                device="cpu",
            )
            local_embedding_model.encode(["embedding warmup"])
            self.local_embedding_model = local_embedding_model
            logger.info("LocalEmbedding on-demand warmup completed")
            return self.local_embedding_model

    def _iter_conversation_records(self):
        hidden_groups = getattr(self, "_hidden_conversation_topic_groups", set())
        display_only_topic_records = getattr(self, "_display_only_topic_records", {})
        topic_groups = sorted(set(self._topic_records.keys()) | set(display_only_topic_records.keys()))
        for topic_group in topic_groups:
            if topic_group in hidden_groups:
                continue
            merged_records = list(self._topic_records.get(topic_group, []))
            merged_records.extend(display_only_topic_records.get(topic_group, []))
            merged_records.sort(key=self._conversation_record_sort_key)
            for record in merged_records:
                yield topic_group, record

    def _serialize_history_record(self, topic_group: int, record: dict):
        return {
            "id": str(record.get("message_id", "")),
            "topic_group": int(topic_group),
            "role": str(record.get("role", "assistant") or "assistant"),
            "content": str(record.get("content", "")),
            "timestamp": self._format_readable_time(record.get("timestamp")),
        }

    def _serialize_context_messages(self, messages, prefix: str):
        serialized_messages = []
        for index, message in enumerate(messages or [], start=1):
            serialized_messages.append({
                "id": f"{prefix}-{index}",
                "role": str(message.get("role", "system") or "system"),
                "content": str(message.get("content", "")),
            })
        return serialized_messages

    def _serialize_memory_layer_sections(self, state: dict, *, layer: str):
        normalized_state = normalize_core_memory_state(state or {})
        serialized_sections = []
        for spec in get_core_memory_section_specs():
            if str(spec.get("layer", "")).strip() != str(layer or "").strip():
                continue
            raw_items = list(normalized_state.get(spec["key"]) or [])
            text_items = [
                str(item.get("text", "") or "")
                for item in raw_items
                if isinstance(item, dict) and str(item.get("text", "") or "").strip()
            ]
            detailed_items = [
                {
                    "id": str(item.get("id", "") or ""),
                    "text": str(item.get("text", "") or ""),
                    "created_at": self._format_readable_time(item.get("created_at")),
                    "updated_at": self._format_readable_time(item.get("updated_at")),
                }
                for item in raw_items
                if isinstance(item, dict)
            ]
            serialized_sections.append(
                {
                    "key": spec["key"],
                    "label": spec["label"],
                    "description": spec["description"],
                    "layer": spec.get("layer", ""),
                    "items": text_items,
                    "item_count": len(text_items),
                    "items_detail": detailed_items,
                }
            )
        return serialized_sections

    def _serialize_core_memory_history(self, history, *, layer: str = PERSISTENT_CORE_LAYER, limit: int = 30):
        """把 supersede 历史裁剪成调试面板用的字符串列表。"""
        layer_keys = {
            spec["key"]
            for spec in get_core_memory_section_specs()
            if str(spec.get("layer", "")).strip() == str(layer or "").strip()
        }
        serialized = []
        for entry in history or []:
            if not isinstance(entry, dict):
                continue
            section = str(entry.get("section", "") or "")
            if layer_keys and section not in layer_keys:
                continue
            serialized.append(
                {
                    "id": str(entry.get("id", "") or ""),
                    "section": section,
                    "text": str(entry.get("text", "") or ""),
                    "superseded_at": self._format_readable_time(entry.get("superseded_at")),
                    "superseded_by": str(entry.get("superseded_by") or ""),
                    "superseded_reason": str(entry.get("superseded_reason", "") or ""),
                }
            )
        serialized.sort(
            key=lambda e: e.get("superseded_at", ""),
            reverse=True,
        )
        return serialized[: max(1, int(limit or 30))]

    @staticmethod
    def _coerce_sort_timestamp(value):
        if value in (None, ""):
            return 0.0
        if isinstance(value, (int, float)):
            numeric_value = float(value)
            if numeric_value > 1e12:
                numeric_value /= 1000
            return numeric_value
        if isinstance(value, datetime):
            return value.timestamp()
        if isinstance(value, str):
            raw_value = value.strip()
            if not raw_value:
                return 0.0
            try:
                return float(raw_value)
            except (TypeError, ValueError):
                pass
            try:
                parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
                return parsed.timestamp()
            except ValueError:
                return 0.0
        return 0.0

    def _build_topic_archive_debug_state(self):
        recent_archives = self.topic_archive_repository.list_recent_archives(limit=6)
        serialized_archives = []
        for archive in recent_archives:
            topic_records = list(archive.get("topic_records") or [])
            serialized_archives.append(
                {
                    "archive_id": archive.get("archive_id"),
                    "source_file": archive.get("source_file", ""),
                    "source_session_prefix": archive.get("source_session_prefix", ""),
                    "source_topic_group": archive.get("source_topic_group"),
                    "topic_message_count": archive.get("topic_message_count", 0),
                    "summary_text": archive.get("summary_text", ""),
                    "archived_at": self._format_readable_time(archive.get("archived_at")),
                    "updated_at": self._format_readable_time(archive.get("updated_at")),
                    "topic_excerpt": self._build_topic_archive_excerpt(topic_records),
                }
            )
        return {
            "label": "Topic Archive / Episodic Memory",
            "source_kind": "sqlite",
            "source_label": "SQLite topic archive",
            "source_path": TOPIC_ARCHIVE_DB_PATH,
            "total_archives": self.topic_archive_repository.count_archives(),
            "recent_archives": serialized_archives,
        }

    def _build_atomic_memory_debug_state(self):
        now = time.time()
        cache_age = now - float(self._memory_debug_snapshot_cached_at or 0.0)
        if self._memory_debug_snapshot_cache is not None and cache_age < 10:
            return json.loads(json.dumps(self._memory_debug_snapshot_cache, ensure_ascii=False))

        debug_state = {
            "label": "Atomic Semantic Memory",
            "source_kind": "qdrant",
            "source_label": f"Qdrant / {self._memory_collection['name']}",
            "source_collection": self._memory_collection["name"],
            "runtime_initialized": bool(self._runtime_initialized),
            "total_records": 0,
            "atomic_records": 0,
            "active_records": 0,
            "historical_records": 0,
            "topic_summary_records": 0,
            "recent_atomic_memories": [],
            "error": "",
        }

        if Qdrant is None:
            debug_state["error"] = "qdrant_client is unavailable."
            return debug_state

        try:
            memory_qdrant = self._get_or_create_search_qdrant("_memory_qdrant", self._memory_collection)
            records = []
            offset = None
            while True:
                batch, offset = memory_qdrant.client.scroll(
                    collection_name=memory_qdrant.CollectionName,
                    limit=256,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                if not batch:
                    break
                records.extend(batch)
                if offset is None:
                    break
        except Exception as error:
            debug_state["error"] = str(error)
            self._memory_debug_snapshot_cache = debug_state
            self._memory_debug_snapshot_cached_at = now
            return json.loads(json.dumps(debug_state, ensure_ascii=False))

        atomic_candidates = []
        for record in records:
            payload = record.payload or {}
            memory_kind = str(payload.get("memory_kind", "") or "atomic_memory").strip().lower()
            memory_status = str(payload.get("memory_status", "active") or "active").strip().lower()
            memory_status_detail = str(payload.get("memory_status_detail", "") or "").strip()
            debug_state["total_records"] += 1
            if memory_kind == "topic_summary":
                debug_state["topic_summary_records"] += 1
                continue
            debug_state["atomic_records"] += 1
            if memory_status == "historical":
                debug_state["historical_records"] += 1
            else:
                debug_state["active_records"] += 1
            atomic_candidates.append(
                {
                    "id": getattr(record, "id", None),
                    "text": str(payload.get("text", "")).strip(),
                    "personalizedText": str(payload.get("personalizedText", "")).strip(),
                    "textType": str(payload.get("textType", "Fact") or "Fact"),
                    "memory_status": memory_status,
                    "memory_status_detail": memory_status_detail,
                    "timestamp": self._format_readable_time(payload.get("timestamp")),
                    "updated_at": self._format_readable_time(payload.get("updated_at") or payload.get("UpdateTime")),
                    "valid_from": self._format_readable_time(payload.get("valid_from")),
                    "valid_to": self._format_readable_time(payload.get("valid_to")),
                    "source": str(payload.get("source", "") or "").strip(),
                    "source_file": str(payload.get("source_file", "") or "").strip(),
                    "source_topic_group": payload.get("source_topic_group"),
                    "memory_kind": str(payload.get("memory_kind", "") or "atomic_memory"),
                    "_sort_key": max(
                        self._coerce_sort_timestamp(payload.get("updated_at")),
                        self._coerce_sort_timestamp(payload.get("UpdateTime")),
                        self._coerce_sort_timestamp(payload.get("timestamp")),
                        self._coerce_sort_timestamp(payload.get("created_at")),
                    ),
                }
            )

        atomic_candidates.sort(
            key=lambda item: (
                float(item.get("_sort_key", 0.0)),
                int(item.get("id")) if isinstance(item.get("id"), int) else 0,
            ),
            reverse=True,
        )
        debug_state["recent_atomic_memories"] = [
            {
                key: value
                for key, value in item.items()
                if key != "_sort_key"
            }
            for item in atomic_candidates[:6]
        ]
        self._memory_debug_snapshot_cache = debug_state
        self._memory_debug_snapshot_cached_at = now
        return json.loads(json.dumps(debug_state, ensure_ascii=False))

    def _export_memory_layer_state(self):
        persistent_payload = self.persistent_core_memory_store.load_payload()
        persistent_state = extract_persistent_core_memory_state(persistent_payload.get("state") or {})
        working_state = normalize_core_memory_state(self._context_memory_state)
        persistent_history = self._serialize_core_memory_history(
            persistent_payload.get("history") or [],
            layer=PERSISTENT_CORE_LAYER,
        )
        return {
            "persistent_core": {
                "label": "Persistent Core Memory",
                "source_kind": "json",
                "source_label": "Local JSON persistent core store",
                "source_path": PERSISTENT_CORE_MEMORY_PATH,
                "updated_at": self._format_readable_time(persistent_payload.get("updated_at")),
                "sections": self._serialize_memory_layer_sections(
                    persistent_state,
                    layer="persistent_core",
                ),
                "history": persistent_history,
            },
            "topic_working_memory": {
                "label": "Session / Topic Working Memory",
                "source_kind": "runtime",
                "source_label": f"Active topic group #{self.topicGroup}",
                "source_path": self._topic_file_path(self.topicGroup),
                "updated_at": "",
                "sections": self._serialize_memory_layer_sections(
                    working_state,
                    layer="topic_working_memory",
                ),
            },
            "topic_archive": self._build_topic_archive_debug_state(),
            "atomic_semantic_memory": self._build_atomic_memory_debug_state(),
        }

    def export_runtime_state(self):
        """导出当前会话、上下文和提醒快照。"""
        with self._runtime_lock:
            conversation = [
                self._serialize_history_record(topic_group, record)
                for topic_group, record in self._iter_conversation_records()
            ]
            due_tasks = self._get_due_reminder_snapshot()
            role_play_context = self._serialize_context_messages(
                self._build_messages_with_persistent_contexts(self.role_play_message),
                "role-play",
            )
            agent_context = self._serialize_context_messages(
                self._build_agent_request_messages(),
                "agent",
            )
            simple_context = self._serialize_context_messages(
                self._build_messages_with_persistent_contexts(self.simple_message),
                "simple",
            )
            return {
                "conversation": conversation,
                "agent_steps": [
                    dict(item)
                    for item in (
                        (self._historical_agent_steps or [])
                        + (self._current_turn_visible_agent_steps or [])
                    )
                    if isinstance(item, dict)
                ],
                "pending_tool_approvals": [
                    dict(item)
                    for item in (self._pending_tool_approvals or [])
                    if isinstance(item, dict)
                ],
                "recent_tool_security_events": [
                    dict(item)
                    for item in (self._recent_tool_security_events or [])
                    if isinstance(item, dict)
                ],
                "contexts": {
                    "agent": agent_context,
                    "role_play": role_play_context,
                    "simple": simple_context,
                },
                "memory_layers": self._export_memory_layer_state(),
                "due_tasks": due_tasks,
                "meta": {
                    "topic_group": self.topicGroup,
                    "input_count": self.input_num,
                    "runtime_initialized": self._runtime_initialized,
                    "agent_suspended": self._suspended_agent_state is not None,
                    "agent_suspended_question": (
                        self._get_suspended_question()
                        if self._suspended_agent_state else ""
                    ),
                },
            }

    def process_user_input(self, user_input: str):
        """处理单次用户输入，并返回最新运行时状态。"""
        normalized_input = str(user_input or "").strip()
        if not normalized_input:
            raise ValueError("User input is required.")
        injection_check = self.tool_policy_engine.evaluate_context_text(normalized_input, source="user_input")
        if injection_check.get("blocked"):
            raise ValueError(str(injection_check.get("reason") or "Potential prompt injection detected."))

        self.initialize_runtime()
        self.reload_config()

        # ── 检查是否有暂停的 Agent 等待用户回复 ──
        if self._suspended_agent_state is not None:
            logger.info("Resuming suspended Agent with user reply | input=%s", normalized_input[:80])
            self.input_num += 1
            data_queue.put(self.input_num)
            self.appendUserMessageSync(normalized_input, "user")
            with self._run_agent_lock:
                self._resume_agent_from_suspension(normalized_input)
            return self.export_runtime_state()

        # ── 正常流程 ──
        if True:
            self._set_current_turn_agent_request()
            self._reset_visible_agent_steps()
            logger.info("用户输入: %s", normalized_input)
            self.input_num += 1
            data_queue.put(self.input_num)
            self.appendUserMessageSync(normalized_input, "user")
            if not self.IntentionRAG(
                normalized_input,
                self.local_embedding_model,
                append_async=False
            ):
                with self._run_agent_lock:
                    self.run_agent(model=self.model)
            return self.export_runtime_state()

    def _save_cleared_dialogue_snapshot(self, snapshot: dict):
        """把清空前的对话快照写入磁盘，供下次启动后继续。"""
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CLEARED_DIALOGUE_SNAPSHOT_PATH, "w", encoding="utf-8") as file:
            json.dump(snapshot, file, ensure_ascii=False, indent=2)

    def _load_cleared_dialogue_snapshot(self):
        """读取最近一次清空前保存的对话快照。"""
        if self._cleared_dialogue_snapshot:
            return self._cleared_dialogue_snapshot
        if not os.path.exists(CLEARED_DIALOGUE_SNAPSHOT_PATH):
            return None
        try:
            with open(CLEARED_DIALOGUE_SNAPSHOT_PATH, "r", encoding="utf-8") as file:
                snapshot = json.load(file)
            if isinstance(snapshot, dict):
                self._cleared_dialogue_snapshot = snapshot
                return snapshot
        except Exception:
            logger.exception("Failed to load cleared dialogue snapshot")
        return None

    @staticmethod
    def _delete_cleared_dialogue_snapshot():
        try:
            if os.path.exists(CLEARED_DIALOGUE_SNAPSHOT_PATH):
                os.remove(CLEARED_DIALOGUE_SNAPSHOT_PATH)
        except Exception:
            logger.exception("Failed to delete cleared dialogue snapshot")

    @staticmethod
    def _conversation_record_sort_key(record: dict):
        try:
            message_id = int(record.get("message_id") or 0)
        except (TypeError, ValueError):
            message_id = 0
        try:
            timestamp = float(record.get("timestamp") or 0.0)
        except (TypeError, ValueError):
            timestamp = 0.0
        return message_id, timestamp

    @staticmethod
    def _next_message_id_from_topic_records(*record_sets: dict) -> int:
        max_message_id = 0
        for record_set in record_sets or ({},):
            for records in (record_set or {}).values():
                for record in records or []:
                    try:
                        max_message_id = max(max_message_id, int(record.get("message_id") or 0))
                    except (TypeError, ValueError):
                        continue
        return max_message_id + 1

    def clear_dialogue_context(self):
        """清空当前可见对话和 Agent/RolePlay 上下文，并保留一次恢复快照。"""
        with self._runtime_lock:
            with self._persistent_context_lock:
                persistent_contexts_snapshot = [
                    dict(item)
                    for item in getattr(self, "_persistent_system_contexts", [])
                ]

            hidden_groups = set(getattr(self, "_hidden_conversation_topic_groups", set()))
            topic_records_snapshot = {
                str(topic_group): [dict(record) for record in records]
                for topic_group, records in self._topic_records.items()
            }
            display_only_records_snapshot = {
                str(topic_group): [dict(record) for record in records]
                for topic_group, records in getattr(self, "_display_only_topic_records", {}).items()
            }
            has_visible_records = any(
                records
                for topic_group, records in self._topic_records.items()
                if topic_group not in hidden_groups
            )
            has_visible_records = has_visible_records or any(
                records
                for topic_group, records in getattr(self, "_display_only_topic_records", {}).items()
                if topic_group not in hidden_groups
            )
            snapshot = {
                "snapshot_version": 1,
                "saved_at": time.time(),
                "source_session": self.ContextJsonName,
                "topic_group": self.topicGroup,
                "topic_records": topic_records_snapshot,
                "display_only_topic_records": display_only_records_snapshot,
                "hidden_topic_groups": sorted(hidden_groups),
                "topic_same_messages": [dict(item) for item in self._topic_same_messages],
                "agent_message": [dict(message) for message in self.agent_message],
                "simple_message": [dict(message) for message in self.simple_message],
                "role_play_message": [dict(message) for message in self.role_play_message],
                "context_memory_state": json.loads(json.dumps(self._context_memory_state, ensure_ascii=False)),
                "core_memory_history": [dict(item) for item in self._core_memory_history],
                "context_memory_pending_message_count": self._context_memory_pending_message_count,
                "persistent_system_contexts": persistent_contexts_snapshot,
                "next_persistent_context_id": self._next_persistent_context_id,
                "historical_agent_steps": [dict(item) for item in self._historical_agent_steps],
                "current_turn_visible_agent_steps": [dict(item) for item in self._current_turn_visible_agent_steps],
            }
            if has_visible_records or not self._load_cleared_dialogue_snapshot():
                self._cleared_dialogue_snapshot = snapshot
                self._save_cleared_dialogue_snapshot(snapshot)

            self.input_num += 1
            data_queue.put(self.input_num)
            existing_groups = set(self._topic_records.keys()) | set(
                getattr(self, "_display_only_topic_records", {}).keys()
            )
            self._hidden_conversation_topic_groups = set(existing_groups)
            self.topicGroup = (max(existing_groups) + 1) if existing_groups else 0
            self._topic_records.setdefault(self.topicGroup, [])
            self._display_only_topic_records.setdefault(self.topicGroup, [])
            self._topic_same_messages = []
            self._suspended_agent_state = None
            self._pending_tool_approvals = []
            self._reset_visible_agent_steps()
            self._historical_agent_steps = []
            self._replace_context_memory_state(
                clear_topic_working_memory_state(self._context_memory_state),
                persist_persistent_core=False,
            )
            (
                self.agent_message,
                self.simple_message,
                self.role_play_message,
            ) = self._build_fixed_live_contexts()
            reset_summary_context_state()
            self._context_revision += 1
            logger.info("Dialogue context cleared | new_topic_group=%s", self.topicGroup)
            return self.export_runtime_state()

    def continue_last_dialogue(self):
        """恢复最近一次清空前保存的对话快照。"""
        with self._runtime_lock:
            snapshot = self._load_cleared_dialogue_snapshot()
            if not snapshot:
                logger.info("Continue dialogue skipped | reason=no_snapshot")
                return self.export_runtime_state()

            self.input_num += 1
            data_queue.put(self.input_num)
            self.topicGroup = int(snapshot.get("topic_group", self.topicGroup))
            self._topic_records = {
                int(topic_group): [dict(record) for record in records]
                for topic_group, records in snapshot.get("topic_records", {}).items()
            }
            self._display_only_topic_records = {
                int(topic_group): [dict(record) for record in records]
                for topic_group, records in snapshot.get("display_only_topic_records", {}).items()
            }
            self._next_message_id = self._next_message_id_from_topic_records(
                self._topic_records,
                self._display_only_topic_records,
            )
            self._hidden_conversation_topic_groups = {
                int(topic_group)
                for topic_group in snapshot.get("hidden_topic_groups", [])
                if str(topic_group).strip()
            }
            self._topic_same_messages = [
                dict(item)
                for item in snapshot.get("topic_same_messages", [])
            ]
            self.agent_message = [dict(message) for message in snapshot.get("agent_message", [])]
            self.simple_message = [dict(message) for message in snapshot.get("simple_message", [])]
            self.role_play_message = [dict(message) for message in snapshot.get("role_play_message", [])]
            self._context_memory_state = json.loads(
                json.dumps(snapshot.get("context_memory_state", self._context_memory_state), ensure_ascii=False)
            )
            self._core_memory_history = [
                dict(item)
                for item in snapshot.get("core_memory_history", [])
            ]
            self._context_memory_pending_message_count = int(
                snapshot.get("context_memory_pending_message_count", 0) or 0
            )
            with self._persistent_context_lock:
                self._persistent_system_contexts = [
                    dict(item)
                    for item in snapshot.get("persistent_system_contexts", [])
                ]
                self._next_persistent_context_id = int(
                    snapshot.get("next_persistent_context_id", self._next_persistent_context_id)
                    or self._next_persistent_context_id
                )
            self._historical_agent_steps = [
                dict(item)
                for item in snapshot.get("historical_agent_steps", [])
            ]
            self._current_turn_visible_agent_steps = [
                dict(item)
                for item in snapshot.get("current_turn_visible_agent_steps", [])
            ]
            self._suspended_agent_state = None
            self._cleared_dialogue_snapshot = None
            self._delete_cleared_dialogue_snapshot()
            self._context_revision += 1
            logger.info("Last dialogue restored | topic_group=%s", self.topicGroup)
            return self.export_runtime_state()

    def _flush_context_memory_on_shutdown(self):
        """在退出前把当前会话里可沉淀的稳定信息刷入持久核心层。"""
        if self.session is None:
            return
        if not self._get_context_memory_runtime_config().get("enabled", True):
            self._persist_context_memory_state()
            return
        core_memory_task_config = self._get_topic_postprocess_llm_task_config("core_memory_update")
        if not core_memory_task_config.get("enabled", True):
            self._persist_context_memory_state()
            return
        model_key = self._resolve_context_memory_model_key()
        if not model_key:
            return
        with self._runtime_lock:
            if not any(self._topic_records.get(self.topicGroup, [])):
                self._persist_context_memory_state()
                return
            context_memory_snapshot = json.loads(
                json.dumps(self._context_memory_state, ensure_ascii=False)
            )
            core_memory_history_snapshot = list(self._core_memory_history or [])
            topic_records_snapshot = [
                dict(item)
                for item in self._topic_records.get(self.topicGroup, [])
            ]
        try:
            updated_context_memory, updated_core_memory_history = asyncio.run(
                update_core_memory_state(
                    context_memory_snapshot,
                    topic_records_snapshot,
                    self.session,
                    model_key,
                    history=core_memory_history_snapshot,
                    thinking=core_memory_task_config.get("thinking", True),
                    json_mode=core_memory_task_config.get("json_mode", True),
                    reasoning_effort=core_memory_task_config.get("reasoning_effort", "high"),
                )
            )
            updated_context_memory = self._maintain_recent_experiences_with_core_memory(
                updated_context_memory,
                topic_records_snapshot,
            )
        except Exception:
            logger.exception("Failed to flush context memory on shutdown")
            self._persist_context_memory_state()
            return
        with self._runtime_lock:
            self._replace_context_memory_state(
                updated_context_memory,
                persist_persistent_core=True,
                new_history=updated_core_memory_history,
            )

    def shutdown(self):
        """停止后台线程并释放会话与检索资源。"""
        with self._runtime_lock:
            if self._shutdown_complete:
                return
            self.chat_cycle = False
        self._flush_context_memory_on_shutdown()
        with self._runtime_lock:
            self._autonomous_stop_event.set()
            self._autonomous_interrupt_event.set()
            if (
                self._autonomous_worker_thread is not None
                and self._autonomous_worker_thread is not threading.current_thread()
                and self._autonomous_worker_thread.is_alive()
            ):
                self._autonomous_worker_thread.join(timeout=1)
            self._schedule_stop_event.set()
            if self._schedule_worker_thread.is_alive():
                self._schedule_worker_thread.join(timeout=1)
            self._append_queue.put(None)
            if self._append_worker_thread.is_alive():
                self._append_worker_thread.join(timeout=1)
            self._postprocess_queue.put(None)
            if self._postprocess_worker_thread.is_alive():
                self._postprocess_worker_thread.join(timeout=1)
            self._close_search_resources()
            try:
                self.mcp_runtime.close()
            except Exception:
                logger.exception("Failed to close MCP runtime")
            try:
                self.session.close()
            except Exception:
                logger.exception("Failed to close session")
            self._shutdown_complete = True

    def _initialize_message_contexts(self):
        """初始化 Agent、Simple、RolePlay 与 Topic 的消息上下文。"""
        self.agent_message = [self._build_system_message("Agent")]
        self.simple_message = [self._build_system_message("Simple")]
        self.role_play_message = [self._build_system_message(ROLE_PLAY_TASK_NAME)]
        self.topicEnded_message = [self._build_system_message("Topic")]

    def _initialize_memory_storage(self):
        """初始化持久核心层与话题归档的本地存储。"""
        self.persistent_core_memory_store = PersistentCoreMemoryStore(PERSISTENT_CORE_MEMORY_PATH)
        self.topic_archive_repository = TopicArchiveRepository(TOPIC_ARCHIVE_DB_PATH)
        self._memory_debug_snapshot_cache = None
        self._memory_debug_snapshot_cached_at = 0.0
        self._core_memory_history = []

    def _initialize_context_memory_state(self):
        """初始化 RolePlay / Simple 共享的关键记忆块，并加载持久记忆状态。"""
        persisted_core_state = self._load_persistent_core_memory_state()
        self._core_memory_history = self._load_persistent_core_memory_history()
        hydrated_state = hydrate_core_memory_state(persisted_core_state)
        bootstrap_persist = False
        if not list(hydrated_state.get("recent_experiences") or []):
            try:
                hydrated_state = self._maintain_recent_experiences_with_core_memory(
                    hydrated_state,
                    [],
                )
                bootstrap_persist = bool(hydrated_state.get("recent_experiences"))
            except Exception:
                logger.exception("Failed to bootstrap recent experiences during runtime initialization")
        self._replace_context_memory_state(
            hydrated_state,
            persist_persistent_core=bootstrap_persist,
        )
        self._context_memory_pending_message_count = 0

    def _load_persistent_core_memory_state(self):
        """从本地存储加载持久记忆状态，working memory 其余部分默认为空。"""
        try:
            return self.persistent_core_memory_store.load()
        except Exception:
            logger.exception("Failed to load persistent core memory state")
            return build_default_core_memory_state()

    def _load_persistent_core_memory_history(self):
        """加载持久核心层的 supersede 历史记录。"""
        try:
            return list(self.persistent_core_memory_store.load_history() or [])
        except Exception:
            logger.exception("Failed to load persistent core memory history")
            return []

    def _persist_context_memory_state(self):
        """把当前上下文记忆中的持久状态和 supersede 历史写回本地存储。"""
        try:
            self.persistent_core_memory_store.save(
                extract_persistent_core_memory_state(self._context_memory_state),
                history=list(self._core_memory_history or []),
            )
        except Exception:
            logger.exception("Failed to persist core memory state")

    def _invalidate_memory_debug_snapshot(self):
        self._memory_debug_snapshot_cache = None
        self._memory_debug_snapshot_cached_at = 0.0

    def _replace_context_memory_state(self, new_state, *, persist_persistent_core: bool, new_history=None):
        """统一更新关键记忆状态，并在需要时立刻保存持久核心层。

        `new_history` 非 None 时，同步替换 supersede 历史记录；为 None 则保持不变。
        """
        self._context_memory_state = normalize_core_memory_state(new_state or {})
        if new_history is not None:
            self._core_memory_history = list(new_history or [])
        self._sync_core_memory_system_messages()
        self._invalidate_memory_debug_snapshot()
        if persist_persistent_core:
            self._persist_context_memory_state()

    def _get_context_memory_runtime_config(self):
        """读取关键记忆刷新策略配置，并补齐默认值。"""
        configured = dict(self.config.get("ContextMemory", {}))
        merged = {**DEFAULT_CONTEXT_MEMORY_RUNTIME_CONFIG, **configured}
        merged["enabled"] = bool(merged.get("enabled", True))
        merged["update_on_empty"] = bool(merged.get("update_on_empty", True))

        update_trigger = str(merged.get("update_trigger", "") or "").strip().lower()
        if update_trigger not in {"assistant_turn", "topic_switch", "topic_switch_or_interval"}:
            update_trigger = DEFAULT_CONTEXT_MEMORY_RUNTIME_CONFIG["update_trigger"]
        merged["update_trigger"] = update_trigger

        try:
            merged["update_min_new_messages"] = int(merged.get("update_min_new_messages", 6))
        except (TypeError, ValueError):
            merged["update_min_new_messages"] = DEFAULT_CONTEXT_MEMORY_RUNTIME_CONFIG["update_min_new_messages"]
        merged["update_min_new_messages"] = max(2, merged["update_min_new_messages"])
        return merged

    def _get_agent_retrieval_cache_config(self):
        configured = dict((self.config or {}).get("AgentRetrievalCache", {}))
        merged = copy.deepcopy(DEFAULT_AGENT_RETRIEVAL_CACHE_CONFIG)
        merged.update(configured)
        merged["enabled"] = bool(merged.get("enabled", True))
        merged["match_model"] = str(
            merged.get("match_model", DEFAULT_AGENT_RETRIEVAL_CACHE_CONFIG["match_model"]) or ""
        ).strip() or DEFAULT_AGENT_RETRIEVAL_CACHE_CONFIG["match_model"]
        merged["match_thinking"] = bool(merged.get("match_thinking", False))
        merged["match_json_mode"] = bool(merged.get("match_json_mode", True))
        merged["match_reasoning_effort"] = str(
            merged.get(
                "match_reasoning_effort",
                DEFAULT_AGENT_RETRIEVAL_CACHE_CONFIG["match_reasoning_effort"],
            )
            or ""
        ).strip() or DEFAULT_AGENT_RETRIEVAL_CACHE_CONFIG["match_reasoning_effort"]
        try:
            merged["max_cache_inject_chars"] = int(
                merged.get(
                    "max_cache_inject_chars",
                    DEFAULT_AGENT_RETRIEVAL_CACHE_CONFIG["max_cache_inject_chars"],
                )
            )
        except (TypeError, ValueError):
            merged["max_cache_inject_chars"] = DEFAULT_AGENT_RETRIEVAL_CACHE_CONFIG["max_cache_inject_chars"]
        merged["max_cache_inject_chars"] = max(1000, merged["max_cache_inject_chars"])
        raw_tool_names = merged.get("cacheable_tools", DEFAULT_AGENT_RETRIEVAL_CACHE_CONFIG["cacheable_tools"])
        if not isinstance(raw_tool_names, (list, tuple, set)):
            raw_tool_names = DEFAULT_AGENT_RETRIEVAL_CACHE_CONFIG["cacheable_tools"]
        merged["cacheable_tools"] = [
            str(tool_name or "").strip()
            for tool_name in raw_tool_names
            if str(tool_name or "").strip()
        ]
        return merged

    def _get_agent_runtime_config(self, *, browser_mode: bool = False):
        configured = dict(self.config.get("AgentRuntime", {}))
        merged = {**DEFAULT_AGENT_RUNTIME_CONFIG, **configured}
        try:
            merged["max_tool_calls"] = int(
                merged.get("max_tool_calls", DEFAULT_AGENT_RUNTIME_CONFIG["max_tool_calls"])
            )
        except (TypeError, ValueError):
            merged["max_tool_calls"] = DEFAULT_AGENT_RUNTIME_CONFIG["max_tool_calls"]
        try:
            merged["max_consecutive_same_tool_calls"] = int(
                merged.get(
                    "max_consecutive_same_tool_calls",
                    merged.get(
                        "max_consecutive_same_tool_or_skill_calls",
                        DEFAULT_AGENT_RUNTIME_CONFIG["max_consecutive_same_tool_calls"],
                    ),
                )
            )
        except (TypeError, ValueError):
            merged["max_consecutive_same_tool_calls"] = DEFAULT_AGENT_RUNTIME_CONFIG["max_consecutive_same_tool_calls"]
        merged["max_tool_calls"] = max(1, merged["max_tool_calls"])
        merged["max_consecutive_same_tool_calls"] = max(
            1,
            merged["max_consecutive_same_tool_calls"],
        )
        if browser_mode:
            merged = self._apply_browser_runtime_overrides(merged)
        return merged

    def _build_detached_agent_runtime_config(self, max_tool_calls: int):
        runtime_config = self._get_agent_runtime_config()
        runtime_config["max_tool_calls"] = max(1, int(max_tool_calls or runtime_config["max_tool_calls"]))
        return runtime_config

    def _apply_browser_runtime_overrides(self, runtime_config: dict) -> dict:
        configured = dict(self.config.get("BrowserAgentRuntime", {}))
        merged = {
            **dict(runtime_config or {}),
            **DEFAULT_BROWSER_AGENT_RUNTIME_CONFIG,
            **configured,
        }
        try:
            merged["max_tool_calls"] = max(
                int(runtime_config.get("max_tool_calls", DEFAULT_AGENT_RUNTIME_CONFIG["max_tool_calls"])),
                int(merged.get("max_tool_calls", DEFAULT_BROWSER_AGENT_RUNTIME_CONFIG["max_tool_calls"])),
            )
        except (TypeError, ValueError, AttributeError):
            merged["max_tool_calls"] = DEFAULT_BROWSER_AGENT_RUNTIME_CONFIG["max_tool_calls"]
        try:
            merged["max_consecutive_same_tool_calls"] = max(
                int(
                    runtime_config.get(
                        "max_consecutive_same_tool_calls",
                        DEFAULT_AGENT_RUNTIME_CONFIG["max_consecutive_same_tool_calls"],
                    )
                ),
                int(
                    merged.get(
                        "max_consecutive_same_tool_calls",
                        DEFAULT_BROWSER_AGENT_RUNTIME_CONFIG["max_consecutive_same_tool_calls"],
                    )
                ),
            )
        except (TypeError, ValueError, AttributeError):
            merged["max_consecutive_same_tool_calls"] = DEFAULT_BROWSER_AGENT_RUNTIME_CONFIG[
                "max_consecutive_same_tool_calls"
            ]
        try:
            merged["ephemeral_observation_limit"] = max(
                1,
                int(
                    merged.get(
                        "ephemeral_observation_limit",
                        DEFAULT_BROWSER_AGENT_RUNTIME_CONFIG["ephemeral_observation_limit"],
                    )
                ),
            )
        except (TypeError, ValueError):
            merged["ephemeral_observation_limit"] = DEFAULT_BROWSER_AGENT_RUNTIME_CONFIG[
                "ephemeral_observation_limit"
            ]
        try:
            merged["ephemeral_visual_limit"] = max(
                0,
                int(
                    merged.get(
                        "ephemeral_visual_limit",
                        DEFAULT_BROWSER_AGENT_RUNTIME_CONFIG["ephemeral_visual_limit"],
                    )
                ),
            )
        except (TypeError, ValueError):
            merged["ephemeral_visual_limit"] = DEFAULT_BROWSER_AGENT_RUNTIME_CONFIG[
                "ephemeral_visual_limit"
            ]
        return merged

    def _turn_looks_like_browser_task(self) -> bool:
        selected_ability, ranked_candidates = self._get_current_turn_tool_candidates()
        if selected_ability in BROWSER_TOOL_NAMES:
            return True
        for candidate in ranked_candidates[:2]:
            registered_tools = {
                str(tool_name or "").strip()
                for tool_name in (candidate.get("registered_tools") or [])
                if str(tool_name or "").strip()
            }
            if registered_tools & BROWSER_TOOL_NAMES:
                return True
        active_skills = dict((self._get_active_tool_session_context() or {}).get("active_skills") or {})
        if {"chrome-browser-agent", "browser-enhancements"} & set(active_skills.keys()):
            return True
        return any(
            str((item or {}).get("tool_name", "") or "").strip() in BROWSER_TOOL_NAMES
            for item in (self._current_turn_tool_trace or [])
            if isinstance(item, dict)
        )

    @staticmethod
    def _safe_float(value, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _safe_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    def _get_intent_router_config(self):
        configured = self.config.get("IntentRouter", {})
        if not isinstance(configured, dict):
            configured = {}

        merged = copy.deepcopy(DEFAULT_INTENT_ROUTER_RUNTIME_CONFIG)
        configured_llm_fallback = configured.get("llm_fallback", {})
        if isinstance(configured_llm_fallback, dict):
            merged["llm_fallback"].update(configured_llm_fallback)
        for key, value in configured.items():
            if key == "llm_fallback":
                continue
            merged[key] = value

        merged["enabled"] = bool(merged.get("enabled", True))
        merged["high_confidence_threshold"] = max(
            0.0,
            min(1.0, self._safe_float(merged.get("high_confidence_threshold"), 0.78))
        )
        merged["low_confidence_threshold"] = max(
            0.0,
            min(1.0, self._safe_float(merged.get("low_confidence_threshold"), 0.55))
        )
        if merged["low_confidence_threshold"] > merged["high_confidence_threshold"]:
            merged["low_confidence_threshold"] = merged["high_confidence_threshold"]
        merged["candidate_limit"] = max(1, self._safe_int(merged.get("candidate_limit"), 4))
        merged["llm_fallback"]["enabled"] = bool(merged["llm_fallback"].get("enabled", True))
        merged["llm_fallback"]["model"] = str(merged["llm_fallback"].get("model") or "").strip()
        merged["llm_fallback"]["thinking"] = bool(merged["llm_fallback"].get("thinking", False))
        merged["llm_fallback"]["json_mode"] = bool(merged["llm_fallback"].get("json_mode", True))
        merged["llm_fallback"]["reasoning_effort"] = normalize_reasoning_effort(
            merged["llm_fallback"].get("reasoning_effort")
        )
        return merged

    def _get_intent_router_method(self) -> str:
        """返回当前意图识别模式。"""
        router_config = self._get_intent_router_config()
        return str(router_config.get("method", "vector")).strip().lower() or "vector"

    def _uses_llm_intent_router(self) -> bool:
        """当前是否启用了纯 LLM 意图识别。"""
        return self._get_intent_router_method() == "llm"

    def _resolve_context_memory_model_key(self):
        """解析关键记忆维护所使用的模型键。"""
        task_config = self._get_topic_postprocess_llm_task_config("core_memory_update")
        if task_config.get("model"):
            return task_config["model"]
        return self._get_model_select_model_key("SummaryAndMermory") or self._get_model_select_model_key(ROLE_PLAY_TASK_NAME)

    def _get_model_select_task_config(self, task_name: str, fallback_task_names=None):
        return get_model_select_task_config(
            task_name,
            config_data=self.config,
            fallback_task_names=fallback_task_names,
        )

    def _get_model_select_model_key(self, task_name: str, fallback_task_names=None) -> str:
        return get_model_select_model_key(
            task_name,
            config_data=self.config,
            fallback_task_names=fallback_task_names,
        )

    def _iter_model_select_warmup_keys(self):
        return list(iter_model_select_model_keys(self.config))

    def _get_topic_postprocess_llm_config(self):
        return {
            "enabled": True,
            **{
                task_name: self._get_model_select_task_config(task_name)
                for task_name, default_value in DEFAULT_TOPIC_POSTPROCESS_LLM_CONFIG.items()
                if isinstance(default_value, dict)
            },
        }

    def _get_topic_postprocess_llm_task_config(self, task_name: str):
        return self._get_model_select_task_config(task_name)

    def _should_refresh_core_memory(self, *, role: str, topic_switched: bool):
        """根据配置与当前状态判断是否需要刷新关键记忆层。"""
        runtime_config = self._get_context_memory_runtime_config()
        if not runtime_config["enabled"]:
            return False, "disabled"
        core_memory_task_config = self._get_topic_postprocess_llm_task_config("core_memory_update")
        if not core_memory_task_config.get("enabled", True):
            return False, "core_memory_llm_disabled"
        if not core_memory_task_config.get("model"):
            return False, "core_memory_model_not_configured"
        if role != "assistant":
            return False, "non_assistant_turn"

        if runtime_config["update_on_empty"] and not any(self._context_memory_state.values()):
            if self._context_memory_pending_message_count >= 2:
                return True, "bootstrap_empty_memory"

        update_trigger = runtime_config["update_trigger"]
        if update_trigger == "assistant_turn":
            return True, "assistant_turn"
        if update_trigger == "topic_switch":
            if topic_switched:
                return True, "topic_switch"
            return False, "waiting_topic_switch"
        if topic_switched:
            return True, "topic_switch"
        if self._context_memory_pending_message_count >= runtime_config["update_min_new_messages"]:
            return True, "pending_message_threshold"
        return False, "pending_below_threshold"

    def _build_context_memory_system_messages(self, core_memory_state=None) -> list:
        state = self._context_memory_state if core_memory_state is None else core_memory_state
        memory_messages = [build_core_memory_system_message(state)]
        recent_experiences_message = build_recent_experiences_system_message(state)
        if recent_experiences_message:
            memory_messages.append(recent_experiences_message)
        return [dict(message) for message in memory_messages if isinstance(message, dict)]

    @staticmethod
    def _replace_context_memory_system_messages(messages, memory_messages):
        normalized_messages = [dict(message) for message in (messages or [])]
        normalized_memory_messages = [
            dict(message)
            for message in (memory_messages or [])
            if isinstance(message, dict) and str(message.get("content", "")).strip()
        ]
        insert_index = 0
        if normalized_messages and str(normalized_messages[0].get("role", "")).strip().lower() == "system":
            insert_index = 1
        remove_end = insert_index
        while remove_end < len(normalized_messages):
            candidate = normalized_messages[remove_end]
            if (
                is_core_memory_system_message(candidate)
                or is_recent_experiences_system_message(candidate)
            ):
                remove_end += 1
                continue
            break
        return (
            normalized_messages[:insert_index]
            + normalized_memory_messages
            + normalized_messages[remove_end:]
        )

    @staticmethod
    def _get_fixed_context_system_prefix_count(messages) -> int:
        normalized_messages = list(messages or [])
        if not normalized_messages:
            return 0
        count = 0
        if str(normalized_messages[0].get("role", "")).strip().lower() == "system":
            count = 1
        index = count
        while index < len(normalized_messages):
            candidate = normalized_messages[index]
            if (
                is_core_memory_system_message(candidate)
                or is_recent_experiences_system_message(candidate)
            ):
                count += 1
                index += 1
                continue
            break
        return count

    def _sync_core_memory_system_messages(self):
        """把当前关键记忆与近期经历 system prompt 同步到 Simple / RolePlay。"""
        context_memory_messages = self._build_context_memory_system_messages()
        for attr_name in ("simple_message", "role_play_message"):
            messages = getattr(self, attr_name)
            messages[:] = self._replace_context_memory_system_messages(
                messages,
                context_memory_messages,
            )

    def _build_fixed_live_contexts(self):
        """构造三条活跃上下文的固定 system prompt 前缀。"""
        agent_messages = [self._build_system_message("Agent")]
        context_memory_messages = self._build_context_memory_system_messages()
        current_skill_prompt = self._build_agent_skill_prompt_for_current_turn()
        if str(current_skill_prompt).strip():
            agent_messages.append({"role": "system", "content": current_skill_prompt})
        simple_messages = [
            self._build_system_message("Simple"),
        ] + [dict(message) for message in context_memory_messages]
        role_play_messages = [
            self._build_system_message(ROLE_PLAY_TASK_NAME),
        ] + [dict(message) for message in context_memory_messages]
        return agent_messages, simple_messages, role_play_messages

    def _match_relevant_procedures(
        self, procedures: list, user_input: str, *, threshold: float = 0.35
    ) -> list:
        """用语义相似度筛选与当前用户输入相关的操作经验。

        threshold 较低是因为操作经验描述（如"播放网易云..."）与用户指令
        （如"放音乐"）的表述差异较大，需要宽松匹配。
        如果 embedding 模型不可用，退回全量返回。
        """
        normalized_input = str(user_input or "").strip()
        if not normalized_input or self.local_embedding_model is None:
            return list(procedures)
        try:
            procedure_texts = []
            for item in procedures:
                text = item.get("text", "") if isinstance(item, dict) else str(item or "")
                # 只取 → 前面的触发场景描述做匹配，不拿 URL 做匹配
                trigger_part = text.split("→")[0].strip() if "→" in text else text
                procedure_texts.append(trigger_part)
            if not procedure_texts:
                return list(procedures)
            input_vector = self.local_embedding_model.encode(normalized_input)
            procedure_vectors = self.local_embedding_model.encode(procedure_texts)
            # 计算余弦相似度
            import numpy as np
            input_norm = input_vector / (np.linalg.norm(input_vector) + 1e-9)
            procedure_norms = procedure_vectors / (
                np.linalg.norm(procedure_vectors, axis=1, keepdims=True) + 1e-9
            )
            similarities = procedure_norms @ input_norm
            matched = [
                procedures[i]
                for i in range(len(procedures))
                if similarities[i] >= threshold
            ]
            if matched:
                logger.info(
                    "Learned procedures matched | input=%s | matched=%s/%s | scores=%s",
                    normalized_input[:60],
                    len(matched),
                    len(procedures),
                    [f"{s:.3f}" for s in similarities],
                )
            return matched
        except Exception:
            logger.exception("Failed to match learned procedures, falling back to all")
            return list(procedures)

    def _rebuild_live_contexts_for_active_topic(self, active_topic_records):
        """按当前话题记录重建三条 live context，并清空旧话题内容。"""
        (
            self.agent_message,
            self.simple_message,
            self.role_play_message,
        ) = self._build_fixed_live_contexts()
        self.agent_message = rebuild_context_from_topic(self.agent_message, active_topic_records)
        self.simple_message = rebuild_context_from_topic(self.simple_message, active_topic_records)
        self.role_play_message = rebuild_context_from_topic(self.role_play_message, active_topic_records)
        reset_summary_context_state()

    def _initialize_vector_search_state(self):
        """初始化向量检索集合配置、实例句柄与查询缓存。"""
        self._intention_collection = get_qdrant_collection_config("intention", self.config)
        self._rag_collection = get_qdrant_collection_config("rag", self.config)
        self._memory_collection = get_qdrant_collection_config("memory", self.config)
        self._intention_qdrant = None
        self._rag_qdrant = None
        self._memory_qdrant = None
        self._query_embedding_cache = OrderedDict()
        self._query_embedding_cache_limit = 256

    def _initialize_summary_worker_state(self):
        """初始化摘要 worker 的脚本、锁文件和日志路径。"""
        self._summary_worker_started = False
        self._summary_worker_script = os.path.join(script_dir, "memory", "history_summary_worker.py")
        self._summary_worker_lock_path = os.path.join(HISTORY_DIR, ".summary_memory_worker.lock")
        self._summary_worker_log_component = "history_summary_worker"
        self._summary_worker_bootstrap_component = "history_summary_worker_bootstrap"
        self._summary_worker_conda_env = "workspace"
        self._refresh_summary_worker_log_paths()

    def _initialize_topic_tracking(self):
        """初始化话题分组、原始消息记录与 TopicSame 缓存。"""
        timestamp_prefix = datetime.now().strftime("%Y%m%d_%H%M%S_")
        millisecond_suffix = f"{datetime.now().microsecond // 1000:05d}"
        self.ContextJsonName = f"raw_dialogue_{timestamp_prefix}{millisecond_suffix}"
        self.ContextJsonPath = os.path.join(HISTORY_DIR, self.ContextJsonName)
        self._next_message_id = 1
        self._next_topic_group_id = 1
        self._topic_records = {self.topicGroup: []}
        self._display_only_topic_records = {self.topicGroup: []}
        self._topic_same_messages = []
        self._hidden_conversation_topic_groups = set()
        self._cleared_dialogue_snapshot = None


    def _initialize_persistent_system_contexts(self):
        """初始化可跨轮保留的系统上下文容器。"""
        self._persistent_system_contexts = []
        self._next_persistent_context_id = 1
        self._persistent_context_lock = threading.RLock()

    def _initialize_retrieval_cache(self):
        """初始化 Agent 检索缓存仓库，并在新运行时启动时清空旧缓存。"""
        self.retrieval_cache_repository = None
        try:
            repository = RetrievalCacheRepository(RETRIEVAL_CACHE_DB_PATH)
            repository.clear_all()
            self.retrieval_cache_repository = repository
        except Exception:
            logger.exception("Failed to initialize retrieval cache repository")

    def _initialize_schedule_system(self):
        """Initialize the local schedule repository and reminder polling worker."""
        self.schedule_repository = ScheduleRepository(SCHEDULE_DB_PATH)
        self._due_reminder_cache = OrderedDict()
        self._due_reminder_lock = threading.RLock()
        self._schedule_stop_event = threading.Event()
        self._schedule_worker_thread = threading.Thread(
            target=self._run_schedule_worker,
            name="schedule-reminder-worker",
            daemon=True,
        )
        self._schedule_worker_thread.start()

    def _initialize_dynamic_extension_runtime(self):
        self.dynamic_tool_registry = DynamicToolRegistry()
        self.mcp_runtime = MCPRuntime(self)
        self.subagent_runtime = SubAgentRuntime(self)

    def _next_message_identifier(self) -> int:
        message_id = self._next_message_id
        self._next_message_id += 1
        return message_id

    def _current_persistent_context_anchor(self):
        current_records = self._topic_records.get(self.topicGroup, [])
        anchor_message_id = None
        if current_records:
            anchor_message_id = current_records[-1].get("message_id")
        return self.topicGroup, anchor_message_id

    def _add_persistent_system_context(
        self,
        context_type: str,
        content: str,
        topic_group: int = None,
        anchor_message_id: int = None
    ):
        normalized_content = str(content or "").strip()
        if not normalized_content:
            return None

        if topic_group is None or anchor_message_id is None:
            default_topic_group, default_anchor_message_id = self._current_persistent_context_anchor()
            if topic_group is None:
                topic_group = default_topic_group
            if anchor_message_id is None:
                anchor_message_id = default_anchor_message_id

        with self._persistent_context_lock:
            context_record = {
                "context_id": self._next_persistent_context_id,
                "context_type": str(context_type or "").strip().lower(),
                "content": normalized_content,
                "topic_group": topic_group,
                "anchor_message_id": anchor_message_id,
                "created_at": time.time(),
            }
            self._persistent_system_contexts.append(context_record)
            self._next_persistent_context_id += 1

        logger.info(
            "Persistent system context added | id=%s | type=%s | topic_group=%s | anchor_message_id=%s",
            context_record["context_id"],
            context_record["context_type"],
            context_record["topic_group"],
            context_record["anchor_message_id"]
        )
        return context_record

    @staticmethod
    def _message_matches_topic_record(message: dict, record: dict) -> bool:
        """判断上下文消息是否与某条话题记录对应。"""
        return (
            str(message.get("role", "")).strip().lower()
            == str(record.get("role", "")).strip().lower()
            and str(message.get("content", "")) == str(record.get("content", ""))
        )

    @staticmethod
    def _count_leading_system_messages(messages) -> int:
        """统计消息开头连续的 system 消息数量。"""
        count = 0
        for message in messages or []:
            if str(message.get("role", "")).strip().lower() != "system":
                break
            count += 1
        return count

    @staticmethod
    def _find_insertion_index_after_system_block(messages, anchor_index: int) -> int:
        """找到锚点消息及其后连续 system 块之后的位置。"""
        insert_after_index = anchor_index
        while insert_after_index + 1 < len(messages):
            next_message = messages[insert_after_index + 1]
            if str(next_message.get("role", "")).strip().lower() != "system":
                break
            insert_after_index += 1
        return insert_after_index

    def _build_visible_topic_record_index_map(self, messages):
        """将当前话题中仍可在上下文里看见的消息记录映射到消息索引。"""
        topic_records = [
            dict(item)
            for item in self._topic_records.get(self.topicGroup, [])
        ]
        record_order_by_message_id = {
            record.get("message_id"): index
            for index, record in enumerate(topic_records)
            if record.get("message_id") is not None
        }
        visible_message_index_by_message_id = {}
        record_index = len(topic_records) - 1
        for message_index in range(len(messages) - 1, -1, -1):
            if record_index < 0:
                break
            message = messages[message_index]
            if str(message.get("role", "")).strip().lower() == "system":
                continue
            if self._message_matches_topic_record(message, topic_records[record_index]):
                message_id = topic_records[record_index].get("message_id")
                if message_id is not None:
                    visible_message_index_by_message_id[message_id] = message_index
                record_index -= 1

        earliest_visible_record_order = None
        if visible_message_index_by_message_id:
            earliest_visible_record_order = min(
                record_order_by_message_id[message_id]
                for message_id in visible_message_index_by_message_id
                if message_id in record_order_by_message_id
            )
        return (
            record_order_by_message_id,
            visible_message_index_by_message_id,
            earliest_visible_record_order,
        )

    def _inject_persistent_system_contexts(self, messages, persistent_contexts):
        """按锚点把持久 system context 插入到对应对话位置。"""
        if not persistent_contexts:
            return [dict(message) for message in messages]

        normalized_messages = [dict(message) for message in messages]
        (
            record_order_by_message_id,
            visible_message_index_by_message_id,
            earliest_visible_record_order,
        ) = self._build_visible_topic_record_index_map(normalized_messages)

        leading_contexts = []
        grouped_insertions = OrderedDict()
        trailing_contexts = []
        for context_record in persistent_contexts:
            normalized_content = str(context_record.get("content", "")).strip()
            if not normalized_content:
                continue
            topic_group = context_record.get("topic_group")
            anchor_message_id = context_record.get("anchor_message_id")
            if topic_group != self.topicGroup:
                trailing_contexts.append({"role": "system", "content": normalized_content})
                continue

            visible_anchor_index = visible_message_index_by_message_id.get(anchor_message_id)
            if visible_anchor_index is not None:
                insertion_index = self._find_insertion_index_after_system_block(
                    normalized_messages,
                    visible_anchor_index,
                )
                grouped_insertions.setdefault(insertion_index, []).append(
                    {"role": "system", "content": normalized_content}
                )
                continue

            anchor_record_order = record_order_by_message_id.get(anchor_message_id)
            if earliest_visible_record_order is None or (
                anchor_record_order is not None
                and anchor_record_order < earliest_visible_record_order
            ):
                leading_contexts.append({"role": "system", "content": normalized_content})
            else:
                trailing_contexts.append({"role": "system", "content": normalized_content})

        if not leading_contexts and not grouped_insertions and not trailing_contexts:
            return normalized_messages

        rebuilt_messages = []
        leading_insert_after_index = self._count_leading_system_messages(normalized_messages) - 1
        if leading_insert_after_index < 0 and leading_contexts:
            rebuilt_messages.extend(leading_contexts)
        for index, message in enumerate(normalized_messages):
            rebuilt_messages.append(message)
            if index == leading_insert_after_index and leading_contexts:
                rebuilt_messages.extend(leading_contexts)
            if index in grouped_insertions:
                rebuilt_messages.extend(grouped_insertions[index])
        rebuilt_messages.extend(trailing_contexts)
        return rebuilt_messages

    def _build_messages_with_persistent_contexts(
        self,
        base_messages,
        extra_system_contents=None,
    ):
        request_message = [dict(message) for message in base_messages]
        with self._persistent_context_lock:
            persistent_contexts = [
                dict(item)
                for item in self._persistent_system_contexts
                if str(item.get("content", "")).strip()
            ]
        request_message = self._inject_persistent_system_contexts(
            request_message,
            persistent_contexts,
        )

        for content in extra_system_contents or []:
            normalized_content = str(content or "").strip()
            if normalized_content:
                request_message.append({"role": "system", "content": normalized_content})
        return request_message

    @staticmethod
    def _insert_system_messages_after_leading_systems(messages, system_messages):
        normalized_messages = [dict(message) for message in messages]
        normalized_system_messages = [
            {"role": "system", "content": str(item.get("content", ""))}
            for item in system_messages or []
            if str(item.get("content", "")).strip()
        ]
        if not normalized_system_messages:
            return normalized_messages
        insert_index = 0
        for message in normalized_messages:
            if str(message.get("role", "")).strip().lower() != "system":
                break
            insert_index += 1
        return (
            normalized_messages[:insert_index]
            + normalized_system_messages
            + normalized_messages[insert_index:]
        )

    def _get_role_play_summary_system_messages(self):
        fixed_system_prefix_count = self._get_fixed_context_system_prefix_count(
            self.role_play_message
        ) or 1
        summary_messages = []
        for message in self.role_play_message[fixed_system_prefix_count:]:
            if str(message.get("role", "")).strip().lower() != "system":
                break
            summary_messages.append(dict(message))
        return summary_messages

    @staticmethod
    def _looks_like_file_read_request(user_input: str) -> bool:
        normalized_input = str(user_input or "").strip().lower()
        if not normalized_input:
            return False
        file_keywords = (
            "打开这个文件",
            "打开文件",
            "看看这个文件",
            "读取文件",
            "读一下文件",
            "read file",
            "open file",
            "show file",
            "file content",
        )
        path_markers = (
            ":\\",
            "./",
            "../",
            "/",
            ".json",
            ".md",
            ".txt",
            ".py",
            ".log",
        )
        has_file_keyword = any(keyword in normalized_input for keyword in file_keywords)
        has_path_marker = any(marker in normalized_input for marker in path_markers)
        return has_file_keyword or has_path_marker

    @staticmethod
    def _is_agent_skill_map_message(message: dict) -> bool:
        if str((message or {}).get("role", "")).strip().lower() != "system":
            return False
        content = str((message or {}).get("content", "") or "")
        return "Skill map:" in content and "activateSkill" in content

    def _build_agent_request_messages(self, base_messages=None, extra_system_contents=None):
        if base_messages is None:
            base_messages = self.agent_message
        normalized_base_messages = [
            dict(message)
            for message in (base_messages or [])
            if not self._is_agent_skill_map_message(message)
        ]
        normalized_extra_system_contents = list(extra_system_contents or [])
        current_skill_prompt = self._build_agent_skill_prompt_for_current_turn()
        if str(current_skill_prompt).strip():
            normalized_extra_system_contents.insert(0, current_skill_prompt)
        request_message = self._build_messages_with_persistent_contexts(
            normalized_base_messages,
            normalized_extra_system_contents,
        )
        return request_message

    def _build_retrieval_cache_topic_id(self, topic_group: int | None = None) -> str:
        normalized_topic_group = self.topicGroup if topic_group is None else topic_group
        try:
            return f"topic:{int(normalized_topic_group)}"
        except (TypeError, ValueError):
            return "topic:0"

    @staticmethod
    def _serialize_retrieval_cache_raw_result(tool_result) -> str:
        if isinstance(tool_result, str):
            return str(tool_result)
        try:
            return json.dumps(tool_result, ensure_ascii=False, indent=2, default=str)
        except Exception:
            return str(tool_result)

    def _build_retrieval_cache_query_text(self, function_name: str, function_args: dict, tool_result) -> str:
        function_args = function_args if isinstance(function_args, dict) else {}
        normalized_tool_name = str(function_name or "").strip()

        if normalized_tool_name == "searchAutonomousTaskArtifacts":
            parts = []
            for key in ("Query", "query"):
                value = str(function_args.get(key, "") or "").strip()
                if value:
                    parts.append(value)
                    break
            for key in ("SinceDate", "UntilDate"):
                value = str(function_args.get(key, "") or "").strip()
                if value:
                    parts.append(f"{key}={value}")
            return " | ".join(parts)

        if normalized_tool_name == "readAutonomousTaskArtifact":
            task_id = function_args.get("TaskId", function_args.get("taskId"))
            return f"TaskId={task_id}" if task_id not in (None, "") else ""

        if normalized_tool_name == "browserExtractPage":
            if isinstance(tool_result, dict):
                page_url = str(tool_result.get("url", "") or "").strip()
                if page_url:
                    return page_url
                page_title = str(tool_result.get("title", "") or "").strip()
                if page_title:
                    return page_title
            return str((self._current_turn_agent_request or {}).get("user_input", "") or "").strip()

        for key in ("Query", "query", "Url", "url", "Path", "path"):
            value = str(function_args.get(key, "") or "").strip()
            if value:
                return value

        if normalized_tool_name == "webSearch" and isinstance(tool_result, dict):
            query_text = str(tool_result.get("query", "") or "").strip()
            if query_text:
                return query_text

        return str((self._current_turn_agent_request or {}).get("user_input", "") or "").strip()

    def _should_cache_read_local_file_result(self, function_args: dict, tool_result) -> bool:
        function_args = function_args if isinstance(function_args, dict) else {}
        tool_result = tool_result if isinstance(tool_result, dict) else {}
        file_path = str(tool_result.get("path", "") or function_args.get("Path", "") or "").strip()
        if not file_path:
            return False
        normalized_basename = os.path.basename(file_path).strip().lower()
        _, extension = os.path.splitext(normalized_basename)
        if extension in {".md", ".txt", ".rst", ".csv", ".log", ".html", ".htm", ".xml"}:
            return True
        keyword_hits = ("readme", "changelog", "license", "notice", "faq", "manual", "guide", "doc", "prd")
        return any(keyword in normalized_basename for keyword in keyword_hits)

    def _is_retrieval_cacheable_tool_result(self, function_name: str, function_args: dict, tool_result) -> bool:
        cache_config = self._get_agent_retrieval_cache_config()
        if not cache_config.get("enabled", True):
            return False
        if self.retrieval_cache_repository is None:
            return False
        if self._get_current_agent_session().name != "main":
            return False
        normalized_tool_name = str(function_name or "").strip()
        if normalized_tool_name not in set(cache_config.get("cacheable_tools", [])):
            return False
        if isinstance(tool_result, dict):
            if bool(tool_result.get("approval_required", False)):
                return False
            if str(tool_result.get("error", "") or "").strip():
                return False
            if "ok" in tool_result and not bool(tool_result.get("ok", False)):
                return False
        elif not str(tool_result or "").strip():
            return False
        if normalized_tool_name == "readLocalFile":
            return self._should_cache_read_local_file_result(function_args, tool_result)
        return True

    def _store_retrieval_cache_entry(self, function_name: str, function_args: dict, tool_result):
        if not self._is_retrieval_cacheable_tool_result(function_name, function_args, tool_result):
            return None
        try:
            record = self.retrieval_cache_repository.add_record(
                session_id=self.ContextJsonName,
                tool_name=str(function_name or "").strip(),
                query_text=self._build_retrieval_cache_query_text(function_name, function_args, tool_result),
                raw_result=self._serialize_retrieval_cache_raw_result(tool_result),
                summary_text="",
                topic_id=self._build_retrieval_cache_topic_id(),
            )
        except Exception:
            logger.exception("Failed to store retrieval cache entry | tool=%s", function_name)
            return None
        record_id = int((record or {}).get("id") or 0)
        if record_id > 0:
            pending_ids = list(self._current_turn_retrieval_cache_ids or [])
            if record_id not in pending_ids:
                pending_ids.append(record_id)
                self._current_turn_retrieval_cache_ids = pending_ids
        logger.info(
            "Retrieval cache stored | id=%s | tool=%s | topic_id=%s",
            record_id,
            function_name,
            self._build_retrieval_cache_topic_id(),
        )
        return record

    def _discard_pending_retrieval_cache_records(self):
        pending_ids = [
            int(cache_id)
            for cache_id in (self._current_turn_retrieval_cache_ids or [])
            if str(cache_id).strip()
        ]
        self._current_turn_retrieval_cache_ids = []
        if not pending_ids or self.retrieval_cache_repository is None:
            return
        try:
            expired_count = self.retrieval_cache_repository.mark_records_expired(pending_ids)
            logger.info(
                "Discarded pending retrieval cache entries | ids=%s | expired=%s",
                pending_ids,
                expired_count,
            )
        except Exception:
            logger.exception("Failed to discard pending retrieval cache entries")

    def _backfill_retrieval_cache_summary(self, summary_text: str):
        pending_ids = [
            int(cache_id)
            for cache_id in (self._current_turn_retrieval_cache_ids or [])
            if str(cache_id).strip()
        ]
        self._current_turn_retrieval_cache_ids = []
        if not pending_ids or self.retrieval_cache_repository is None:
            return
        normalized_summary = str(summary_text or "").strip()
        if not normalized_summary:
            return
        try:
            updated_count = self.retrieval_cache_repository.update_summary_text(pending_ids, normalized_summary)
            logger.info(
                "Retrieval cache summary backfilled | ids=%s | updated=%s",
                pending_ids,
                updated_count,
            )
        except Exception:
            logger.exception("Failed to backfill retrieval cache summary")

    def _expire_retrieval_cache_topic(self, topic_group: int):
        if self.retrieval_cache_repository is None:
            return
        try:
            expired_count = self.retrieval_cache_repository.mark_topic_expired(
                self.ContextJsonName,
                self._build_retrieval_cache_topic_id(topic_group),
            )
            logger.info(
                "Retrieval cache expired for topic switch | topic_group=%s | expired=%s",
                topic_group,
                expired_count,
            )
        except Exception:
            logger.exception("Failed to expire retrieval cache for topic_group=%s", topic_group)

    def _build_retrieval_cache_match_payload(self, user_input: str, cached_records: list) -> dict:
        candidates = []
        for record in cached_records:
            raw_result = str((record or {}).get("raw_result", "") or "")
            summary_text = str((record or {}).get("summary_text", "") or "").strip()
            if not summary_text:
                summary_text = self._truncate_context_text(raw_result, max_chars=240)
            candidates.append(
                {
                    "id": int((record or {}).get("id") or 0),
                    "tool_name": str((record or {}).get("tool_name", "") or "").strip(),
                    "query_text": str((record or {}).get("query_text", "") or "").strip(),
                    "summary_text": summary_text,
                    "created_at": str((record or {}).get("created_at", "") or "").strip(),
                }
            )
        return {
            "user_input": str(user_input or "").strip(),
            "recent_dialogue": self._truncate_multiline_context_text(self._build_llm_intent_context(), max_chars=1200),
            "cache_records": candidates,
        }

    def _match_retrieval_cache(self, user_input: str) -> dict:
        cache_config = self._get_agent_retrieval_cache_config()
        if not cache_config.get("enabled", True):
            return {"is_cache_hit": False, "cache_ids": [], "records": [], "reason": "disabled"}
        if self.retrieval_cache_repository is None:
            return {"is_cache_hit": False, "cache_ids": [], "records": [], "reason": "repository_unavailable"}

        cached_records = self.retrieval_cache_repository.list_active_records(self.ContextJsonName, limit=12)
        if not cached_records:
            return {"is_cache_hit": False, "cache_ids": [], "records": [], "reason": "no_active_cache"}

        prompt_messages = [
            {"role": "system", "content": setSystemPrompt("RetrievalCacheMatch")},
            {
                "role": "user",
                "content": json.dumps(
                    self._build_retrieval_cache_match_payload(user_input, cached_records),
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]
        try:
            result_text = call_LLM(
                prompt_messages,
                cache_config.get("match_model") or "qwen_flash",
                self.session,
                cache_config.get("match_thinking", False),
                cache_config.get("match_json_mode", True),
                caller="RetrievalCacheMatch",
                reasoning_effort=cache_config.get("match_reasoning_effort", "low"),
            )
            parsed_result = json.loads(result_text)
        except Exception as exc:
            logger.warning("Retrieval cache match failed | error=%s", exc)
            return {"is_cache_hit": False, "cache_ids": [], "records": [], "reason": "llm_error"}

        candidate_ids = {
            int((record or {}).get("id") or 0)
            for record in cached_records
            if int((record or {}).get("id") or 0) > 0
        }
        matched_ids = []
        for raw_cache_id in parsed_result.get("cache_ids") or []:
            try:
                normalized_cache_id = int(raw_cache_id)
            except (TypeError, ValueError):
                continue
            if normalized_cache_id in candidate_ids and normalized_cache_id not in matched_ids:
                matched_ids.append(normalized_cache_id)
        if not bool(parsed_result.get("is_cache_hit", False)) or not matched_ids:
            return {
                "is_cache_hit": False,
                "cache_ids": [],
                "records": [],
                "reason": str(parsed_result.get("reason", "") or "").strip(),
            }

        matched_records = self.retrieval_cache_repository.get_active_records_by_ids(
            self.ContextJsonName,
            matched_ids,
        )
        if not matched_records:
            return {"is_cache_hit": False, "cache_ids": [], "records": [], "reason": "records_not_found"}

        return {
            "is_cache_hit": True,
            "cache_ids": matched_ids,
            "records": matched_records,
            "reason": str(parsed_result.get("reason", "") or "").strip(),
        }

    def _build_retrieval_cache_context(self, cached_records: list) -> str:
        cache_config = self._get_agent_retrieval_cache_config()
        max_chars = int(cache_config.get("max_cache_inject_chars", 8000) or 8000)
        header = "[Agent 此前检索到的原始信息]\n"
        footer = "请基于以上原始信息直接回答用户这一次的追问；如果信息仍然不足，再明确说明缺少哪些细节。"
        if max_chars <= len(header) + len(footer) + 32:
            return f"{header}{footer}"

        remaining = max_chars - len(header) - len(footer)
        blocks = []
        truncated = False
        for index, record in enumerate(cached_records or [], start=1):
            tool_name = str((record or {}).get("tool_name", "") or "").strip() or "unknown_tool"
            query_text = str((record or {}).get("query_text", "") or "").strip()
            raw_result = str((record or {}).get("raw_result", "") or "").strip()
            source_line = f"[{index}] 来源：{tool_name}"
            if query_text:
                source_line += f"（查询：{query_text}）"
            source_line += "\n"
            if not raw_result:
                continue
            block = f"{source_line}{raw_result}\n\n"
            if len(block) <= remaining:
                blocks.append(block)
                remaining -= len(block)
                continue
            truncated = True
            truncation_note = "\n[内容因长度限制已截断]\n\n"
            allowed_raw_chars = max(0, remaining - len(source_line) - len(truncation_note))
            if allowed_raw_chars > 0:
                blocks.append(
                    f"{source_line}{raw_result[:allowed_raw_chars].rstrip()}{truncation_note}"
                )
            break

        note = "注：以上原始信息已按长度限制裁剪。\n" if truncated else ""
        return f"{header}{''.join(blocks)}{note}{footer}".strip()

    def _try_answer_from_retrieval_cache(self, user_input: str, *, append_async: bool = True) -> bool:
        match_result = self._match_retrieval_cache(user_input)
        if not match_result.get("is_cache_hit", False):
            return False

        cached_records = list(match_result.get("records") or [])
        cache_context = self._build_retrieval_cache_context(cached_records)
        if not cache_context:
            return False

        request_message = self._build_messages_with_persistent_contexts(self.role_play_message)
        self._insert_after_last_user_message(request_message, cache_context)
        reply_task_config = self._get_model_select_task_config(ROLE_PLAY_TASK_NAME)
        self._set_current_turn_agent_request(
            user_input=user_input,
            route="rag",
            reason="retrieval_cache_hit",
        )
        logger.info(
            "Retrieval cache hit | ids=%s | reason=%s",
            match_result.get("cache_ids") or [],
            str(match_result.get("reason", "") or "").strip(),
        )
        with self._temporary_due_reminder_contexts(request_message) as due_tasks:
            result = call_LLM(
                request_message,
                reply_task_config.get("model") or "kimi",
                self.session,
                reply_task_config.get("thinking", True),
                reply_task_config.get("json_mode", False),
                caller="RetrievalCacheReply",
                reasoning_effort=reply_task_config.get("reasoning_effort", "high"),
            )
        self._acknowledge_due_reminders(due_tasks)
        if append_async:
            self.appendUserMessageAsync(result, "assistant")
        else:
            self.appendUserMessageSync(result, "assistant")
        return True

    def _run_schedule_worker(self):
        """Keep an in-memory snapshot of due reminders so final replies can inject them quickly."""
        while not self._schedule_stop_event.is_set():
            try:
                self._refresh_due_reminder_cache()
            except Exception:
                logger.exception("Failed to refresh due reminder cache")
            self._schedule_stop_event.wait(1.0)

    def _refresh_due_reminder_cache(self):
        due_tasks = self.schedule_repository.get_due_unreminded_tasks(limit=20)
        new_cache = OrderedDict(
            (task["task_id"], task)
            for task in due_tasks
        )
        with self._due_reminder_lock:
            self._due_reminder_cache = new_cache

    def _get_due_reminder_snapshot(self):
        with self._due_reminder_lock:
            cached_tasks = [dict(task) for task in self._due_reminder_cache.values()]
        if cached_tasks:
            return cached_tasks
        return self.schedule_repository.get_due_unreminded_tasks(limit=20)

    @staticmethod
    def _build_due_reminder_context(task: dict):
        reminder_time = str(task.get("reminder_time", "")).strip()
        task_content = str(task.get("task_content", "")).strip()
        return {
            "role": "system",
            "context": f"Due reminder at {reminder_time}: {task_content}"
        }

    @contextmanager
    def _temporary_due_reminder_contexts(self, messages):
        due_tasks = self._get_due_reminder_snapshot()
        appended_messages = []
        if due_tasks:
            logger.info(
                "Inject due reminders into final reply context | reminder_ids=%s",
                [task["task_id"] for task in due_tasks]
            )
        for task in due_tasks:
            reminder_context = self._build_due_reminder_context(task)
            appended_message = {
                "role": reminder_context["role"],
                "content": reminder_context["context"]
            }
            messages.append(appended_message)
            appended_messages.append(appended_message)
        try:
            yield due_tasks
        finally:
            for appended_message in reversed(appended_messages):
                for index in range(len(messages) - 1, -1, -1):
                    if messages[index] is appended_message:
                        del messages[index]
                        break

    def _query_experience_memory_candidates(self):
        """从 Qdrant 中查询可用于当前 topic 的近期经历候选记忆。"""
        autonomous_config = (self.config or {}).get("AutonomousTaskMode", {})
        if not bool(autonomous_config.get("enabled", False)):
            return []
        sharing_config = autonomous_config.get("sharing", {})
        min_score = max(0, self._safe_int(sharing_config.get("min_score"), 8))
        cooldown_days = max(0, self._safe_int(sharing_config.get("cooldown_days"), 3))
        max_candidates = max(0, self._safe_int(sharing_config.get("max_inject_count"), 5))
        if max_candidates <= 0 or Qdrant is None:
            return []

        cooldown_threshold = time.time() - (cooldown_days * 86400)
        try:
            memory_qdrant = self._get_or_create_search_qdrant("_memory_qdrant", self._memory_collection)
        except Exception:
            logger.exception("Failed to initialize memory qdrant for experience memory query")
            return []

        filters = Filter(
            must=[
                FieldCondition(key="source", match=MatchValue(value="autonomous_task")),
                FieldCondition(key="memory_status", match=MatchValue(value="active")),
            ]
        )
        candidates = []
        offset = None
        try:
            while True:
                batch, offset = memory_qdrant.client.scroll(
                    collection_name=memory_qdrant.CollectionName,
                    scroll_filter=filters,
                    limit=128,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                if not batch:
                    break
                candidates.extend(batch)
                if offset is None:
                    break
        except TypeError:
            try:
                while True:
                    batch, offset = memory_qdrant.client.scroll(
                        collection_name=memory_qdrant.CollectionName,
                        filter=filters,
                        limit=128,
                        offset=offset,
                        with_payload=True,
                        with_vectors=False,
                    )
                    if not batch:
                        break
                    candidates.extend(batch)
                    if offset is None:
                        break
            except Exception:
                logger.exception("Failed to query experience memories from qdrant")
                return []
        except Exception:
            logger.exception("Failed to query experience memories from qdrant")
            return []

        filtered_memories = []
        for record in candidates:
            payload = record.payload or {}
            sharing_score = max(0, self._safe_int(payload.get("sharing_score"), 0))
            if sharing_score < min_score:
                continue
            last_mentioned_at = payload.get("last_mentioned_at")
            if last_mentioned_at not in (None, ""):
                try:
                    if float(last_mentioned_at) >= cooldown_threshold:
                        continue
                except (TypeError, ValueError):
                    pass
            filtered_memories.append(record)

        filtered_memories.sort(
            key=lambda record: (
                -max(0, self._safe_int((record.payload or {}).get("sharing_score"), 0)),
                -self._safe_float((record.payload or {}).get("updated_at"), 0.0),
            )
        )
        selected_memories = filtered_memories[:max_candidates]
        if selected_memories:
            logger.info(
                "Experience memory candidates selected | count=%s | ids=%s",
                len(selected_memories),
                [getattr(record, "id", None) for record in selected_memories],
            )
        return selected_memories

    @staticmethod
    def _build_recent_experience_dialogue(topic_records, *, limit: int = 6) -> list:
        recent_records = list(topic_records or [])[-max(1, int(limit or 1)):]
        recent_dialogue = []
        for record in recent_records:
            content = str((record or {}).get("content", "") or "").strip()
            if not content:
                continue
            recent_dialogue.append(
                {
                    "role": str((record or {}).get("role", "assistant") or "assistant"),
                    "content": content,
                }
            )
        return recent_dialogue

    @staticmethod
    def _normalize_recent_experience_match_text(text: str) -> str:
        normalized = re.sub(r"\s+", "", str(text or "").strip().lower())
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)

    def _build_recent_experience_core_memory_item(self, record, *, now: float) -> dict | None:
        payload = record.payload or {}
        personalized_text = str(payload.get("personalizedText", "") or "").strip()
        canonical_text = str(payload.get("text", "") or "").strip()
        normalized_text = personalized_text or canonical_text
        if not normalized_text:
            return None
        topic_keywords = [
            str(keyword).strip()
            for keyword in list(payload.get("topic_keywords") or [])
            if str(keyword).strip()
        ][:5]
        return _build_core_memory_item(
            "recent_experiences",
            normalized_text,
            now=now,
            extra_fields={
                "source_memory_id": str(getattr(record, "id", "") or ""),
                "source_text": canonical_text or normalized_text,
                "sharing_score": max(0, self._safe_int(payload.get("sharing_score"), 0)),
                "topic_keywords": topic_keywords,
                "mention_count": max(0, self._safe_int(payload.get("mention_count"), 0)),
            },
        )

    def _collect_recent_experience_match_keywords(self, item: dict) -> list[str]:
        keywords = []
        seen = set()
        for raw_keyword in list((item or {}).get("topic_keywords") or []):
            normalized_keyword = self._normalize_recent_experience_match_text(raw_keyword)
            if len(normalized_keyword) < 2 or normalized_keyword in seen:
                continue
            seen.add(normalized_keyword)
            keywords.append(normalized_keyword)
        for raw_text in (
            (item or {}).get("source_text"),
            (item or {}).get("text"),
        ):
            normalized_text = self._normalize_recent_experience_match_text(raw_text)
            if len(normalized_text) < 8 or normalized_text in seen:
                continue
            seen.add(normalized_text)
            keywords.append(normalized_text)
        return keywords

    def _is_recent_experience_mentioned(self, item: dict, recent_dialogue_text: str) -> bool:
        if not recent_dialogue_text or not isinstance(item, dict):
            return False
        match_keywords = self._collect_recent_experience_match_keywords(item)
        if not match_keywords:
            return False
        keyword_hits = [keyword for keyword in match_keywords if keyword in recent_dialogue_text]
        if len(keyword_hits) >= 2:
            return True
        if len(keyword_hits) == 1 and len(keyword_hits[0]) >= 4:
            return True
        return False

    def _mark_recent_experience_items_mentioned(self, items) -> None:
        mentioned_items = [
            dict(item)
            for item in list(items or [])
            if isinstance(item, dict)
            and str(item.get("source_memory_id", "") or "").strip()
        ]
        if not mentioned_items or Qdrant is None:
            return
        try:
            memory_qdrant = self._get_or_create_search_qdrant("_memory_qdrant", self._memory_collection)
        except Exception:
            logger.exception("Failed to initialize memory qdrant for recent experience mention updates")
            return

        timestamp = time.time()
        for item in mentioned_items:
            memory_id = str(item.get("source_memory_id", "") or "").strip()
            next_mention_count = max(0, self._safe_int(item.get("mention_count"), 0)) + 1
            try:
                memory_qdrant.client.set_payload(
                    collection_name=memory_qdrant.CollectionName,
                    payload={
                        "last_mentioned_at": timestamp,
                        "mention_count": next_mention_count,
                        "updated_at": timestamp,
                    },
                    points=[memory_id],
                )
            except Exception:
                logger.exception(
                    "Failed to update recent experience mention payload | memory_id=%s",
                    memory_id,
                )

    def _maintain_recent_experiences_with_core_memory(
        self,
        core_memory_state: dict,
        topic_records,
    ) -> dict:
        normalized_state = normalize_core_memory_state(core_memory_state or {})
        next_state = json.loads(json.dumps(normalized_state, ensure_ascii=False))
        recent_experience_spec = next(
            (
                spec
                for spec in get_core_memory_section_specs()
                if spec.get("key") == "recent_experiences"
            ),
            {},
        )
        max_items = max(1, self._safe_int(recent_experience_spec.get("preferred_items"), 3))
        current_items = [
            dict(item)
            for item in list(next_state.get("recent_experiences") or [])
            if isinstance(item, dict)
        ]
        recent_dialogue = self._build_recent_experience_dialogue(topic_records)
        recent_dialogue_text = self._normalize_recent_experience_match_text(
            "\n".join(
                str(item.get("content", "") or "").strip()
                for item in recent_dialogue
                if str(item.get("content", "") or "").strip()
            )
        )

        mentioned_items = []
        retained_items = []
        for item in current_items:
            if self._is_recent_experience_mentioned(item, recent_dialogue_text):
                mentioned_items.append(item)
                continue
            retained_items.append(item)

        if mentioned_items:
            self._mark_recent_experience_items_mentioned(mentioned_items)

        candidate_memories = self._query_experience_memory_candidates()
        existing_memory_ids = {
            str(item.get("source_memory_id", "") or "").strip()
            for item in retained_items
            if str(item.get("source_memory_id", "") or "").strip()
        }
        existing_texts = {
            str(item.get("text", "") or "").strip()
            for item in retained_items
            if str(item.get("text", "") or "").strip()
        }
        timestamp = time.time()
        added_count = 0
        for record in candidate_memories:
            if len(retained_items) >= max_items:
                break
            new_item = self._build_recent_experience_core_memory_item(record, now=timestamp)
            if not new_item:
                continue
            memory_id = str(new_item.get("source_memory_id", "") or "").strip()
            text = str(new_item.get("text", "") or "").strip()
            if memory_id and memory_id in existing_memory_ids:
                continue
            if text in existing_texts:
                continue
            retained_items.append(new_item)
            if memory_id:
                existing_memory_ids.add(memory_id)
            if text:
                existing_texts.add(text)
            added_count += 1

        next_state["recent_experiences"] = retained_items
        updated_state = normalize_core_memory_state(next_state)
        logger.info(
            "Recent experiences maintained | final_count=%s | removed=%s | added=%s | candidate_count=%s",
            len(updated_state.get("recent_experiences") or []),
            len(mentioned_items),
            added_count,
            len(candidate_memories),
        )
        return updated_state

    def _acknowledge_due_reminders(self, due_tasks):
        reminder_ids = [
            int(task["task_id"])
            for task in due_tasks or []
            if task.get("task_id") is not None
        ]
        if not reminder_ids:
            return
        updated_count = self.schedule_repository.mark_tasks_reminded(reminder_ids)
        with self._due_reminder_lock:
            for reminder_id in reminder_ids:
                self._due_reminder_cache.pop(reminder_id, None)
        logger.info(
            "Due reminders acknowledged | reminder_ids=%s | updated_count=%s",
            reminder_ids,
            updated_count
        )

    def _capture_topic_eval_state(self):
        current_records = self._topic_records.get(self.topicGroup, [])
        return {
            "topic_group": self.topicGroup,
            "message_ids": tuple(item.get("message_id") for item in current_records),
        }

    def _is_topic_eval_state_current(self, eval_state: dict) -> bool:
        if not eval_state:
            return False
        if eval_state.get("topic_group") != self.topicGroup:
            return False
        current_ids = tuple(
            item.get("message_id")
            for item in self._topic_records.get(self.topicGroup, [])
        )
        return current_ids == tuple(eval_state.get("message_ids", ()))

    def _initialize_append_worker(self):
        """初始化消息追加队列及后台 worker。"""
        self._append_queue = queue.Queue()
        self._append_worker_thread = threading.Thread(
            target=self._append_worker,
            name="append-user-message-worker",
            daemon=True,
        )
        self._append_worker_thread.start()

    def _initialize_postprocess_worker(self):
        """初始化消息后处理队列及后台 worker。"""
        self._postprocess_queue = queue.Queue()
        self._postprocess_worker_thread = threading.Thread(
            target=self._postprocess_worker,
            name="message-postprocess-worker",
            daemon=True,
        )
        self._postprocess_worker_thread.start()

    def _build_function_map(self):
        """构建函数工具名到实例方法的映射。"""
        return {
            "controlSelf": self.controlSelf,
            "getLocation": self.getLocation,
            "getTime": self.getTime,
            "getWeather": self.getWeather,
            "literaryCreation": self.literaryCreation,
            "resolveToolApproval": self.resolveToolApproval,
            "searchLongTermMemory": self.searchLongTermMemory,
            "searchFullText": self.searchFullText,
            "storeLongTermMemory": self.storeLongTermMemory,
            "listTopicsByDate": self.listTopicsByDate,
            "searchAutonomousTaskArtifacts": self.searchAutonomousTaskArtifacts,
            "readAutonomousTaskArtifact": self.readAutonomousTaskArtifact,
            "refreshMcpTools": self.refreshMcpTools,
            "listMcpTools": self.listMcpTools,
            "listLocalDirectory": self.listLocalDirectory,
            "readLocalFile": self.readLocalFile,
            "writeLocalFile": self.writeLocalFile,
            "runTerminalCommand": self.runTerminalCommand,
            "searchLearnedProcedures": self.searchLearnedProcedures,
            "promoteLearnedProcedureToSkill": self.promoteLearnedProcedureToSkill,
            "activateSkill": self.activateSkill,
            "askUser": self.askUser,
            TOOL_SEARCH_TOOL_NAME: self.toolSearch,
            SUMMARY_TOOL_NAME: self.summarizeToolResults,
        }

    def _refresh_runtime_tools(self, *, initial: bool = False):
        invalidate_resource_caches()
        self.tools = self.load_tools()
        self.dynamic_tool_registry.clear()
        self._register_dynamic_builtin_tools()
        self.skill_tool_registry = load_skill_tool_registry(self)
        self._refresh_mcp_dynamic_tools()
        self.tool_skill_map = self._build_tool_skill_map()
        self.tool_skill_map.update(self.dynamic_tool_registry.get_tool_skill_map())
        self.agent_skill_prompt = build_agent_skill_prompt()
        if hasattr(self, "agent_message") and hasattr(self, "_topic_records"):
            active_topic_records = [
                dict(item)
                for item in self._topic_records.get(self.topicGroup, [])
            ]
            self._rebuild_live_contexts_for_active_topic(active_topic_records)
        if not initial:
            logger.info(
                "Runtime tools refreshed | static=%s | dynamic=%s",
                len(self.tools),
                len(self.dynamic_tool_registry.list_tool_definitions()),
            )

    def _build_tool_session_context(
        self,
        *,
        is_subagent: bool = False,
        agent_type: str = "general",
        workspace_root: str = "",
        additional_file_roots=None,
    ):
        session_context = self.tool_policy_engine.build_session_context()
        normalized_workspace_root = str(workspace_root or "").strip()
        if normalized_workspace_root:
            normalized_workspace_root = os.path.abspath(normalized_workspace_root)
            session_context["workspace_root"] = normalized_workspace_root
            session_context["file_roots"] = [normalized_workspace_root]
        else:
            session_context["workspace_root"] = str(session_context.get("workspace_root") or self.project_root)

        extra_roots = []
        for candidate_root in list(additional_file_roots or []):
            normalized_root = str(candidate_root or "").strip()
            if not normalized_root:
                continue
            extra_roots.append(os.path.abspath(normalized_root))
        if extra_roots:
            merged_roots = []
            seen_roots = set()
            for root in [*(session_context.get("file_roots") or []), *extra_roots]:
                normalized_root = os.path.abspath(str(root or "").strip())
                if not normalized_root or normalized_root in seen_roots:
                    continue
                seen_roots.add(normalized_root)
                merged_roots.append(normalized_root)
            session_context["file_roots"] = merged_roots

        if is_subagent:
            subagent_policy = build_subagent_policy(
                (self.config or {}).get("SubAgentPolicy", {}),
                agent_type=agent_type,
                registry=getattr(self, "subagent_runtime", None) and self.subagent_runtime.agent_registry,
            )
            session_context["enabled_toolsets"] = {
                toolset
                for toolset in session_context.get("enabled_toolsets", set())
                if toolset in subagent_policy["allowed_toolsets"]
            }
            if not subagent_policy.get("allow_admin_tools", False):
                session_context["is_admin"] = False

            if self._subagent_depth >= subagent_policy["max_depth"]:
                session_context["enabled_toolsets"].discard("subagent")

            session_context["resource_limits"] = subagent_policy.get("resource_limits", {})
            session_context["allowed_tools"] = subagent_policy.get("allowed_tools", set())
            session_context["disallowed_tools"] = subagent_policy.get("disallowed_tools", set())
        return session_context

    def _set_active_tool_session_context(self, session_context: dict | None):
        self._active_tool_session_context = dict(session_context or {}) if session_context else None

    def _get_active_tool_session_context(self):
        return dict(self._active_tool_session_context or self._build_tool_session_context())

    def _resolve_session_workspace_root(self) -> str:
        session_context = dict(self._active_tool_session_context or {})
        workspace_root = str(session_context.get("workspace_root") or "").strip()
        if workspace_root:
            return os.path.abspath(workspace_root)
        return os.path.abspath(self.project_root)

    @staticmethod
    def _normalize_skill_lookup_key(skill_name: str) -> str:
        return str(skill_name or "").strip().lower().replace("_", "-")

    def _get_current_turn_user_input(self) -> str:
        turn_request = dict(getattr(self, "_current_turn_agent_request", {}) or {})
        return str(turn_request.get("user_input", "") or "").strip()

    def _reset_browser_ephemeral_state(self):
        self._current_turn_browser_observations = []
        self._current_turn_browser_visual_artifacts = []

    @staticmethod
    def _safe_encode_image_as_data_url(image_path: str, *, max_bytes: int = 1_500_000) -> str:
        normalized_path = str(image_path or "").strip()
        if not normalized_path or not os.path.exists(normalized_path):
            return ""
        try:
            if os.path.getsize(normalized_path) > max_bytes:
                return ""
            with open(normalized_path, "rb") as file:
                image_bytes = file.read()
        except OSError:
            return ""
        if not image_bytes:
            return ""
        file_ext = os.path.splitext(normalized_path)[1].strip().lower()
        mime_type = "image/png" if file_ext != ".jpg" and file_ext != ".jpeg" else "image/jpeg"
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _build_browser_observation_record(self, tool_name: str, tool_result, function_args: dict | None = None) -> dict:
        normalized_tool_name = str(tool_name or "").strip()
        function_args = dict(function_args or {})
        summary_text = self._stringify_tool_result(tool_result, max_chars=1600)
        image_path = ""
        title = ""
        url = ""
        tab_id = ""

        if isinstance(tool_result, dict):
            title = str(tool_result.get("title") or "").strip()
            url = str(tool_result.get("url") or "").strip()
            tab_id = str(
                tool_result.get("tab_id")
                or tool_result.get("current_tab_id")
                or tool_result.get("selected_tab_id")
                or ""
            ).strip()
            if normalized_tool_name in {"browserSnapshot", "browserExtractPage"}:
                summary_text = str(tool_result.get("snapshot") or "").strip() or summary_text
            elif normalized_tool_name == "browserReadLinkedPage":
                page_payload = dict(tool_result.get("page") or {})
                title = str(page_payload.get("title") or title).strip()
                url = str(page_payload.get("url") or url).strip()
                summary_text = (
                    str(page_payload.get("snapshot") or "").strip()
                    or str(page_payload.get("page_text") or "").strip()
                    or summary_text
                )
            elif normalized_tool_name == "browserListTabs":
                current_tab_id = str(tool_result.get("current_tab_id") or tab_id).strip()
                tab_lines = []
                for index, item in enumerate(list(tool_result.get("tabs") or [])[:8], start=1):
                    item_tab_id = str(item.get("tab_id") or "").strip()
                    item_title = str(item.get("title") or "").strip() or "(untitled)"
                    item_url = str(item.get("url") or "").strip()
                    prefix = "*" if item_tab_id and item_tab_id == current_tab_id else "-"
                    tab_lines.append(
                        f"{prefix} {index}. {item_title}"
                        + (f" · {item_url}" if item_url else "")
                        + (f" · {item_tab_id}" if item_tab_id else "")
                    )
                summary_text = "\n".join(
                    line
                    for line in [
                        "Visible browser tabs:",
                        f"Current tab: {current_tab_id}" if current_tab_id else "",
                        *tab_lines,
                    ]
                    if line
                ).strip() or summary_text
            elif normalized_tool_name == "browserScreenshot":
                image_path = str(tool_result.get("screenshot_path") or "").strip()
                screenshot_lines = [
                    "Browser screenshot captured.",
                    f"Path: {image_path}" if image_path else "",
                    f"Title: {title}" if title else "",
                    f"URL: {url}" if url else "",
                ]
                summary_text = "\n".join(line for line in screenshot_lines if line).strip() or summary_text
            else:
                preview_payload = dict(tool_result.get("page_preview") or {})
                preview_text = str(preview_payload.get("page_text") or "").strip()
                preview_url = str(preview_payload.get("url") or url).strip()
                preview_title = str(preview_payload.get("title") or title).strip()
                title = preview_title or title
                url = preview_url or url
                preview_lines = [
                    f"Action: {normalized_tool_name}" if normalized_tool_name else "",
                    f"Title: {title}" if title else "",
                    f"URL: {url}" if url else "",
                    f"Tab: {tab_id}" if tab_id else "",
                    f"Matched: {json.dumps(tool_result.get('matched'), ensure_ascii=False)}"
                    if tool_result.get("matched")
                    else "",
                    preview_text,
                ]
                summary_text = "\n".join(line for line in preview_lines if line).strip() or summary_text

        return {
            "tool_name": normalized_tool_name,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "summary_text": self._truncate_context_text(summary_text, max_chars=3400),
            "url": url,
            "title": title,
            "tab_id": tab_id,
            "args_text": self._stringify_tool_result(function_args, max_chars=200),
            "image_path": image_path,
        }

    def _record_browser_observation(self, tool_name: str, tool_result, function_args: dict | None = None):
        normalized_tool_name = str(tool_name or "").strip()
        if normalized_tool_name not in BROWSER_TOOL_NAMES:
            return
        runtime_config = self._apply_browser_runtime_overrides(self._get_agent_runtime_config())
        observation_limit = int(
            runtime_config.get(
                "ephemeral_observation_limit",
                DEFAULT_BROWSER_AGENT_RUNTIME_CONFIG["ephemeral_observation_limit"],
            )
        )
        visual_limit = int(
            runtime_config.get(
                "ephemeral_visual_limit",
                DEFAULT_BROWSER_AGENT_RUNTIME_CONFIG["ephemeral_visual_limit"],
            )
        )
        record = self._build_browser_observation_record(normalized_tool_name, tool_result, function_args)
        observations = list(self._current_turn_browser_observations or [])
        observations.append(record)
        if observation_limit > 0:
            observations = observations[-observation_limit:]
        self._current_turn_browser_observations = observations

        image_path = str(record.get("image_path") or "").strip()
        if image_path:
            visuals = list(self._current_turn_browser_visual_artifacts or [])
            visuals.append(
                {
                    "tool_name": normalized_tool_name,
                    "timestamp": record.get("timestamp", ""),
                    "title": record.get("title", ""),
                    "url": record.get("url", ""),
                    "image_path": image_path,
                }
            )
            if visual_limit > 0:
                visuals = visuals[-visual_limit:]
            self._current_turn_browser_visual_artifacts = visuals

    def _build_browser_ephemeral_messages(self, runtime_settings: dict, runtime_config: dict | None = None) -> list[dict]:
        observations = list(self._current_turn_browser_observations or [])
        visual_artifacts = list(self._current_turn_browser_visual_artifacts or [])
        if not observations and not visual_artifacts:
            return []

        browser_runtime = self._apply_browser_runtime_overrides(
            runtime_config or self._get_agent_runtime_config(browser_mode=True)
        )
        observation_limit = int(
            browser_runtime.get(
                "ephemeral_observation_limit",
                DEFAULT_BROWSER_AGENT_RUNTIME_CONFIG["ephemeral_observation_limit"],
            )
        )
        text_messages = []
        if observations:
            lines = [
                "【Recent browser state | ephemeral】",
                "优先参考这里的最近浏览器状态，而不是更早轮次里被压缩过的 browser tool 日志。",
                "这些状态只服务当前任务，不会被长期记忆化。",
                "",
            ]
            for index, observation in enumerate(observations[-observation_limit:], start=1):
                header_parts = [f"{index}. {observation.get('tool_name', 'browser')}"]
                args_text = str(observation.get("args_text") or "").strip()
                if args_text and args_text != "{}":
                    header_parts.append(f"args={args_text}")
                timestamp_text = str(observation.get("timestamp") or "").strip()
                if timestamp_text:
                    header_parts.append(timestamp_text)
                lines.append(" | ".join(header_parts))
                lines.append(str(observation.get("summary_text") or "").strip())
                lines.append("")
            text_messages.append({"role": "system", "content": "\n".join(lines).strip()})

        if not bool((runtime_settings.get("capabilities") or {}).get("supports_vision", False)):
            return text_messages

        visual_limit = int(
            browser_runtime.get(
                "ephemeral_visual_limit",
                DEFAULT_BROWSER_AGENT_RUNTIME_CONFIG["ephemeral_visual_limit"],
            )
        )
        for artifact in visual_artifacts[-visual_limit:]:
            data_url = self._safe_encode_image_as_data_url(artifact.get("image_path", ""))
            if not data_url:
                continue
            description_lines = [
                "Supplementary browser screenshot for the current page.",
                "If your model can interpret images, use this as auxiliary evidence; otherwise ignore it and rely on browserSnapshot/browserExtractPage text.",
                f"Title: {artifact.get('title', '')}" if artifact.get("title") else "",
                f"URL: {artifact.get('url', '')}" if artifact.get("url") else "",
            ]
            text_messages.append(
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": "\n".join(line for line in description_lines if line).strip(),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        },
                    ],
                }
            )
        return text_messages

    def _collect_referenced_workspace_paths(self, text: str = "", *, additional_paths=None) -> list[str]:
        candidates = []
        normalized_text = str(text or "")
        for pattern in (r"`([^`]+)`", r"'([^']+)'", r"\"([^\"]+)\""):
            candidates.extend(re.findall(pattern, normalized_text))
        candidates.extend(re.findall(r"[A-Za-z0-9_\-./\\]+", normalized_text))
        candidates.extend(str(item or "") for item in (additional_paths or []))

        referenced_paths = []
        seen_paths = set()
        for candidate in candidates:
            cleaned = str(candidate or "").strip().strip(".,:;()[]{}")
            if not cleaned or ("/" not in cleaned and "\\" not in cleaned and "." not in cleaned):
                continue
            try:
                absolute_path = self._normalize_workspace_path(cleaned)
            except Exception:
                continue
            if not os.path.exists(absolute_path):
                continue
            try:
                relative_path = os.path.relpath(absolute_path, self.project_root).replace("\\", "/")
            except ValueError:
                continue
            if relative_path in seen_paths:
                continue
            seen_paths.add(relative_path)
            referenced_paths.append(relative_path)
        return referenced_paths

    @staticmethod
    def _skill_path_pattern_matches(pattern: str, relative_path: str) -> bool:
        normalized_pattern = str(pattern or "").strip().replace("\\", "/")
        normalized_path = str(relative_path or "").strip().replace("\\", "/")
        if not normalized_pattern or not normalized_path:
            return False
        if fnmatch.fnmatchcase(normalized_path, normalized_pattern):
            return True
        if not any(token in normalized_pattern for token in "*?[]"):
            normalized_prefix = normalized_pattern.rstrip("/")
            return normalized_path == normalized_prefix or normalized_path.startswith(f"{normalized_prefix}/")
        return False

    def _skill_paths_match(self, patterns, referenced_paths: list[str]) -> bool:
        normalized_patterns = [str(item or "").strip() for item in (patterns or []) if str(item or "").strip()]
        normalized_paths = [str(item or "").strip() for item in (referenced_paths or []) if str(item or "").strip()]
        if not normalized_patterns:
            return True
        if not normalized_paths:
            return False
        for relative_path in normalized_paths:
            if any(self._skill_path_pattern_matches(pattern, relative_path) for pattern in normalized_patterns):
                return True
        return False

    def _is_skill_explicitly_requested(self, skill_name: str, user_input: str = "") -> bool:
        normalized_user_input = str(user_input or "").strip().lower()
        normalized_skill_name = self._normalize_skill_lookup_key(skill_name)
        if not normalized_user_input or not normalized_skill_name:
            return False
        variants = {
            normalized_skill_name,
            normalized_skill_name.replace("-", " "),
            normalized_skill_name.replace("-", "_"),
        }
        for variant in variants:
            if not variant:
                continue
            if f"/{variant}" in normalized_user_input:
                return True
            if re.search(rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])", normalized_user_input):
                return True
        return False

    def _build_agent_skill_prompt_for_current_turn(self, *, additional_paths=None) -> str:
        skills = load_skill_definitions()
        if not skills:
            return ""
        current_user_input = self._get_current_turn_user_input()
        referenced_paths = self._collect_referenced_workspace_paths(
            current_user_input,
            additional_paths=additional_paths,
        )
        visible_skills = []
        for skill in skills:
            governance = dict(skill.get("governance") or {})
            skill_name = str(skill.get("name") or skill.get("skill_name") or "").strip()
            explicit_request = self._is_skill_explicitly_requested(skill_name, current_user_input)
            if governance.get("disable_model_invocation", False):
                continue
            if governance.get("paths") and not explicit_request:
                if not self._skill_paths_match(governance.get("paths") or [], referenced_paths):
                    continue
            visible_skills.append(skill)
        return build_agent_skill_prompt(visible_skills)

    def _evaluate_skill_activation_request(self, skill_payload: dict) -> dict:
        skill = dict((skill_payload or {}).get("skill") or {})
        governance = dict(skill.get("governance") or {})
        skill_name = str(skill.get("name") or "").strip()
        current_user_input = self._get_current_turn_user_input()
        explicit_user_request = self._is_skill_explicitly_requested(skill_name, current_user_input)
        referenced_paths = self._collect_referenced_workspace_paths(current_user_input)
        path_patterns = list(governance.get("paths") or [])
        path_scope_matched = self._skill_paths_match(path_patterns, referenced_paths) if path_patterns else True

        if governance.get("disable_model_invocation", False) and not explicit_user_request:
            return {
                "ok": False,
                "decision": "blocked",
                "reason": (
                    f"Skill '{skill_name}' 已配置为禁止模型自动调用，"
                    f"只有用户明确点名该 skill 时才允许激活。"
                ),
                "skill_name": skill_name,
                "explicit_user_request": explicit_user_request,
                "referenced_paths": referenced_paths,
                "path_scope_matched": path_scope_matched,
            }
        if path_patterns and not explicit_user_request and not path_scope_matched:
            return {
                "ok": False,
                "decision": "blocked",
                "reason": (
                    f"Skill '{skill_name}' 仅会在匹配这些路径时自动激活："
                    f"{', '.join(path_patterns)}。当前请求没有引用匹配文件。"
                ),
                "skill_name": skill_name,
                "explicit_user_request": explicit_user_request,
                "referenced_paths": referenced_paths,
                "path_scope_matched": path_scope_matched,
            }
        return {
            "ok": True,
            "decision": "allowed",
            "skill_name": skill_name,
            "explicit_user_request": explicit_user_request,
            "referenced_paths": referenced_paths,
            "path_scope_matched": path_scope_matched,
        }

    def _apply_skill_activation_session_context(self, skill_payload: dict, activation_policy: dict) -> dict:
        session_context = self._get_active_tool_session_context()
        skill = dict((skill_payload or {}).get("skill") or {})
        governance = dict(skill.get("governance") or {})
        skill_name = str(skill.get("name") or "").strip()
        approved_tools = {
            str(item or "").strip()
            for item in (session_context.get("approved_tools") or set())
            if str(item or "").strip()
        }
        approved_tools.update(
            str(item or "").strip()
            for item in (governance.get("allowed_tools") or [])
            if str(item or "").strip()
        )
        active_skills = {
            str(name or "").strip(): dict(payload or {})
            for name, payload in dict(session_context.get("active_skills") or {}).items()
            if str(name or "").strip()
        }
        active_skills[skill_name] = {
            "activated_at": self._format_readable_time(time.time()),
            "allowed_tools": list(governance.get("allowed_tools") or []),
            "disable_model_invocation": bool(governance.get("disable_model_invocation", False)),
            "user_invocable": bool(governance.get("user_invocable", True)),
            "paths": list(governance.get("paths") or []),
            "explicit_user_request": bool(activation_policy.get("explicit_user_request", False)),
            "path_scope_matched": bool(activation_policy.get("path_scope_matched", True)),
        }
        session_context["approved_tools"] = approved_tools
        session_context["active_skills"] = active_skills
        self._set_active_tool_session_context(session_context)
        return session_context

    def _resolve_tool_definition(self, function_name: str):
        normalized_function_name = str(function_name or "").strip()
        if not normalized_function_name:
            return {}
        candidates = (
            list(self.tools)
            + self.dynamic_tool_registry.list_tool_definitions()
            + list(self._turn_active_tools_snapshot or [])
        )
        for tool_definition in candidates:
            if self._tool_name_from_definition(tool_definition) == normalized_function_name:
                return tool_definition
        return {
            "type": "function",
            "function": {
                "name": normalized_function_name,
                "description": "",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": True},
            },
        }

    def _normalize_workspace_path(self, path_value: str) -> str:
        normalized_path = str(path_value or "").strip()
        if not normalized_path:
            raise ValueError("Path is required.")
        if os.path.isabs(normalized_path):
            return os.path.abspath(normalized_path)
        return os.path.abspath(os.path.join(self._resolve_session_workspace_root(), normalized_path))

    def readLocalFile(self, Path: str, MaxChars: int = 4000):
        resolved_path = ""
        try:
            resolved_path = self._normalize_workspace_path(Path)
            if not os.path.exists(resolved_path):
                return {"ok": False, "error": "Path does not exist.", "path": resolved_path}
            if not os.path.isfile(resolved_path):
                return {"ok": False, "error": "Path is not a file.", "path": resolved_path}
            with open(resolved_path, "r", encoding="utf-8", errors="replace") as file:
                content = file.read()
            normalized_max_chars = max(1, int(MaxChars or 4000))
            return {
                "ok": True,
                "path": resolved_path,
                "content": content[:normalized_max_chars],
                "truncated": len(content) > normalized_max_chars,
            }
        except Exception as error:
            logger.exception("readLocalFile failed | path=%s", Path)
            return {"ok": False, "error": str(error), "path": resolved_path or str(Path or "")}

    def listLocalDirectory(self, Path: str, Limit: int = 100):
        resolved_path = ""
        try:
            resolved_path = self._normalize_workspace_path(Path)
            if not os.path.exists(resolved_path):
                return {"ok": False, "error": "Path does not exist.", "path": resolved_path}
            if not os.path.isdir(resolved_path):
                return {"ok": False, "error": "Path is not a directory.", "path": resolved_path}
            normalized_limit = max(1, min(int(Limit or 100), 500))
            entries = []
            with os.scandir(resolved_path) as iterator:
                for entry in iterator:
                    entries.append(
                        {
                            "name": entry.name,
                            "path": entry.path,
                            "is_dir": entry.is_dir(),
                            "is_file": entry.is_file(),
                        }
                    )
            entries.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
            return {
                "ok": True,
                "path": resolved_path,
                "entries": entries[:normalized_limit],
                "truncated": len(entries) > normalized_limit,
            }
        except Exception as error:
            logger.exception("listLocalDirectory failed | path=%s", Path)
            return {"ok": False, "error": str(error), "path": resolved_path or str(Path or "")}

    def writeLocalFile(self, Path: str, Content: str):
        resolved_path = ""
        try:
            resolved_path = self._normalize_workspace_path(Path)
            checkpoint = create_file_checkpoint(resolved_path)
            parent_dir = os.path.dirname(resolved_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(resolved_path, "w", encoding="utf-8") as file:
                file.write(str(Content or ""))
            return {
                "ok": True,
                "path": resolved_path,
                "bytes_written": len(str(Content or "").encode("utf-8")),
                "checkpoint": checkpoint,
            }
        except Exception as error:
            logger.exception("writeLocalFile failed | path=%s", Path)
            return {"ok": False, "error": str(error), "path": resolved_path or str(Path or "")}

    def runTerminalCommand(self, Command: str, TimeoutSeconds: float = 20.0):
        terminal_config = dict((self.config or {}).get("ExecutionBackends", {}).get("terminal", {}))
        backend_name = str(terminal_config.get("default_backend") or "isolated").strip().lower() or "isolated"
        workspace_root = self._resolve_session_workspace_root()
        if backend_name == "local":
            return run_local_terminal_command(Command, timeout_seconds=TimeoutSeconds, cwd=workspace_root)
        return run_isolated_terminal_command(Command, timeout_seconds=TimeoutSeconds, cwd=workspace_root)

    def _register_dynamic_builtin_tools(self):
        """Register runtime-discovered tools only.

        Fixed tools and skills should go through the existing static `tools/` and
        `skills/` loading path so they participate in the same metadata, prompt,
        and handler lifecycle as the rest of the project.
        """
        return

    def _refresh_mcp_dynamic_tools(self):
        try:
            discovered_count = self.mcp_runtime.refresh_dynamic_tools(self.dynamic_tool_registry)
            logger.info("MCP dynamic tools refreshed | count=%s", discovered_count)
            return discovered_count
        except Exception:
            logger.exception("Failed to refresh MCP dynamic tools")
            return 0

    @staticmethod
    def _should_skip_silence_context_by_topic_result(topic_same, last_topic_index) -> bool:
        logger.info(
            "Silence follow-up topic gate | isTopicSame=%s | lastTopicIndex=%s",
            topic_same,
            last_topic_index,
        )
        try:
            normalized_last_topic_index = int(last_topic_index or 0)
        except (TypeError, ValueError):
            normalized_last_topic_index = 0
        return (not topic_same) and normalized_last_topic_index == 0

    def _build_silence_follow_up_messages(self):
        request_message = self._build_messages_with_persistent_contexts(self.role_play_message)
        return self._insert_system_messages_after_leading_systems(
            request_message,
            self._get_role_play_summary_system_messages(),
        )

    def _record_silence_follow_up(self, payload: dict, *, expected_input_num: int):
        """把静默追问的触发提示词和模型回复写回运行时上下文。"""
        payload = payload or {}
        normalized_prompt = str(payload.get("system_prompt", "") or "").strip()
        normalized_reply = str(payload.get("assistant_reply", "") or "").strip()
        seconds = payload.get("seconds")
        if not normalized_prompt and not normalized_reply:
            return

        with self._runtime_lock:
            if int(expected_input_num or 0) != int(self.input_num or 0):
                logger.info(
                    "Discard stale silence follow-up | expected_input_num=%s | current_input_num=%s | seconds=%s",
                    expected_input_num,
                    self.input_num,
                    seconds,
                )
                return

            if normalized_prompt:
                system_payload = {"role": "system", "content": normalized_prompt}
                self.agent_message.append(dict(system_payload))
                self.role_play_message.append(dict(system_payload))

            if normalized_reply:
                self._append_message_immediately(normalized_reply, "assistant")
            elif normalized_prompt:
                self._context_revision += 1

    def _maybe_start_silence_context(self, *, input_num: int, topic_same=None, last_topic_index=None):
        if topic_same is None or last_topic_index is None:
            try:
                topic_same, last_topic_index = asyncio.run(
                    self.TopicSame([dict(item) for item in self._topic_same_messages])
                )
            except Exception:
                logger.exception("Silence follow-up topic gate failed, fallback to enable silence context")
                topic_same, last_topic_index = True, 0
        if self._should_skip_silence_context_by_topic_result(topic_same, last_topic_index):
            logger.info("Silence follow-up skipped | reason=topic_same_false_and_last_index_zero")
            return
        SilenceContext(
            Message=self._build_silence_follow_up_messages(),
            InputNum=input_num,
            session=self.session,
            on_follow_up=lambda payload: self._record_silence_follow_up(
                payload,
                expected_input_num=input_num,
            ),
        )

    # ------------------------------------------------------------------
    # 消息追加后台 worker 任务
    # ------------------------------------------------------------------
    def _append_worker(self):
        """串行执行消息追加任务，并在需要时回传异常。"""
        while True:
            task = self._append_queue.get()
            if task is None:
                self._append_queue.task_done()
                break
            user_input, role, done_event, err_holder = task
            try:
                asyncio.run(self.appendUserMessage(user_input, role))
            except Exception as e:
                logger.exception("appendUserMessage failed | role=%s", role)
                if err_holder is not None:
                    err_holder["error"] = e
            finally:
                if done_event is not None:
                    done_event.set()
                self._append_queue.task_done()

    def _postprocess_worker(self):
        """后台执行 TopicSame 与摘要更新等后处理任务。"""
        while True:
            task = self._postprocess_queue.get()
            if task is None:
                self._postprocess_queue.task_done()
                break
            try:
                self._run_postprocess_task(task)
            except Exception:
                logger.exception("Message postprocess failed")
            finally:
                self._postprocess_queue.task_done()

    def _append_message_immediately(self, user_input: str, role: str):
        """在锁内立即写入消息记录并同步更新上下文。"""
        with self._runtime_lock:
            message_record = self._make_message_record(role, user_input)
            self._append_to_live_contexts(role, user_input)
            self._topic_records.setdefault(self.topicGroup, []).append(message_record)
            self._topic_same_messages.append({"role": role, "content": str(user_input)})
            self._append_record_to_topic_file(self.topicGroup, message_record)
            if role in {"user", "assistant"}:
                self._context_memory_pending_message_count += 1
            self._context_revision += 1
            return {
                "role": role,
                "context_revision": self._context_revision,
                "topic_eval_state": self._capture_topic_eval_state() if role == "assistant" else None,
                "topic_same_messages": (
                    [dict(item) for item in self._topic_same_messages]
                    if role == "assistant"
                    else None
                ),
            }

    def _run_postprocess_task(self, task: dict):
        """执行单次消息后处理任务。"""
        if not task:
            return

        role = task.get("role")
        context_revision = task.get("context_revision")
        archive_task = None
        topic_same = True
        last_topic_index = 0

        if role == "assistant" and task.get("topic_same_messages"):
            topic_same, last_topic_index = asyncio.run(
                self.TopicSame(task["topic_same_messages"])
            )
            with self._runtime_lock:
                if self._is_topic_eval_state_current(task.get("topic_eval_state")):
                    logger.info("TopicSame: %s | lastTopicIndex: %s", topic_same, last_topic_index)
                    if not topic_same:
                        archive_task = self._split_current_topic_by_last_index(last_topic_index)
                        context_revision = self._context_revision
                else:
                    logger.info(
                        "Discard stale TopicSame result | topic_same=%s | lastTopicIndex=%s | snapshot_group=%s | current_group=%s",
                        topic_same,
                        last_topic_index,
                        (task.get("topic_eval_state") or {}).get("topic_group"),
                        self.topicGroup
                    )

        if archive_task:
            self._archive_completed_topic(archive_task)

        with self._runtime_lock:
            if context_revision != self._context_revision:
                return
            simple_snapshot = [dict(message) for message in self.simple_message]
            role_play_snapshot = [dict(message) for message in self.role_play_message]
            topic_records_snapshot = [
                dict(item)
                for item in self._topic_records.get(self.topicGroup, [])
            ]
            context_memory_snapshot = json.loads(
                json.dumps(self._context_memory_state, ensure_ascii=False)
            )
            core_memory_history_snapshot = list(self._core_memory_history or [])
            context_memory_model_key = self._resolve_context_memory_model_key()
            context_memory_pending_snapshot = self._context_memory_pending_message_count
            context_summary_task_config = self._get_topic_postprocess_llm_task_config("context_summary")
            core_memory_task_config = self._get_topic_postprocess_llm_task_config("core_memory_update")
            should_refresh_core_memory, context_memory_reason = self._should_refresh_core_memory(
                role=role,
                topic_switched=archive_task is not None,
            )

        summary_simple, summary_role_play = asyncio.run(
            SummaryContext(
                simple_snapshot,
                role_play_snapshot,
                self.session,
                enabled=context_summary_task_config.get("enabled", True),
                model_key=context_summary_task_config.get("model"),
                thinking=context_summary_task_config.get("thinking", False),
                json_mode=context_summary_task_config.get("json_mode", False),
                reasoning_effort=context_summary_task_config.get("reasoning_effort", "high"),
                summary_limits=self.config.get("Summary", {}),
                fixed_system_prefix_count=self._get_fixed_context_system_prefix_count(
                    role_play_snapshot
                ),
            )
        )
        if should_refresh_core_memory:
            updated_context_memory, updated_core_memory_history = asyncio.run(
                update_core_memory_state(
                    context_memory_snapshot,
                    topic_records_snapshot,
                    self.session,
                    context_memory_model_key,
                    history=core_memory_history_snapshot,
                    thinking=core_memory_task_config.get("thinking", True),
                    json_mode=core_memory_task_config.get("json_mode", True),
                    reasoning_effort=core_memory_task_config.get("reasoning_effort", "high"),
                )
            )
            updated_context_memory = self._maintain_recent_experiences_with_core_memory(
                updated_context_memory,
                topic_records_snapshot,
            )
        else:
            updated_context_memory = context_memory_snapshot
            updated_core_memory_history = core_memory_history_snapshot

        with self._runtime_lock:
            if context_revision != self._context_revision:
                return
            logger.info("AgentMessage Len :%s", len(self.agent_message))
            self._trim_agent_context()
            self.simple_message[:] = summary_simple
            self.role_play_message[:] = summary_role_play
            self._replace_context_memory_state(
                updated_context_memory,
                persist_persistent_core=should_refresh_core_memory,
                new_history=updated_core_memory_history if should_refresh_core_memory else None,
            )
            if should_refresh_core_memory:
                self._context_memory_pending_message_count = 0
            logger.info(
                "Core memory refresh decision | refreshed=%s | reason=%s | pending_messages_before=%s | pending_messages_after=%s",
                should_refresh_core_memory,
                context_memory_reason,
                context_memory_pending_snapshot,
                self._context_memory_pending_message_count,
            )
            should_start_silence_context = role == "assistant"
            current_input_num = self.input_num
        if should_start_silence_context:
            self._maybe_start_silence_context(
                input_num=current_input_num,
                topic_same=topic_same,
                last_topic_index=last_topic_index,
            )

    def _enqueue_append(self, user_input: str, role: str, wait: bool):
        """将消息追加任务放入队列，并按需等待结果。"""
        if str(role or "").strip().lower() == "user":
            self._record_user_interaction()
        done_event = threading.Event() if wait else None
        err_holder = {"error": None} if wait else None
        self._append_queue.put((user_input, role, done_event, err_holder))
        if not wait:
            return
        done_event.wait()
        if err_holder["error"] is not None:
            raise err_holder["error"]

    def appendUserMessageSync(self, user_input: str, role: str):
        """同步追加一条消息。"""
        self._enqueue_append(user_input, role, wait=True)

    def appendUserMessageAsync(self, user_input: str, role: str):
        """异步追加一条消息。"""
        self._enqueue_append(user_input, role, wait=False)

    @staticmethod
    def _read_log_tail(file_path: str, max_lines: int = 40) -> str:
        """读取日志文件末尾若干行。"""
        if not os.path.exists(file_path):
            return ""
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as file:
                lines = file.readlines()
            return "".join(lines[-max_lines:]).strip()
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # 消息追加后台 worker 任务相关
    # ------------------------------------------------------------------
    def _refresh_summary_worker_log_paths(self):
        return summary_worker_helpers._refresh_summary_worker_log_paths(self)

    @staticmethod
    def _resolve_conda_executable() -> str:
        return summary_worker_helpers._resolve_conda_executable()

    def _build_summary_worker_command(self, model_key: str):
        return summary_worker_helpers._build_summary_worker_command(self, model_key)

    def _start_summary_memory_worker(self):
        return summary_worker_helpers._start_summary_memory_worker(self)
            
    def _topic_file_path(self, topic_group: int) -> str:
        return topic_history_helpers._topic_file_path(self, topic_group)

    def _make_message_record(self, role: str, content: str):
        return topic_history_helpers._make_message_record(self, role, content)

    def _append_display_only_conversation_message(self, content: str, role: str = "assistant"):
        return topic_history_helpers._append_display_only_conversation_message(self, content, role)

    def _append_record_to_topic_file(self, topic_group: int, record: dict):
        return topic_history_helpers._append_record_to_topic_file(self, topic_group, record)

    def _rewrite_topic_file(self, topic_group: int):
        return topic_history_helpers._rewrite_topic_file(self, topic_group)

    @staticmethod
    def _safe_last_topic_index(last_topic_index, total_count: int) -> int:
        return topic_history_helpers._safe_last_topic_index(last_topic_index, total_count)

    def _split_current_topic_by_last_index(self, last_topic_index):
        return topic_history_helpers._split_current_topic_by_last_index(self, last_topic_index)

    @staticmethod
    def create_session(pool_connections: int = 20, pool_maxsize: int = 20):
        """创建带连接池配置的 requests.Session。"""
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    @staticmethod
    def load_tools():
        """加载 Agent 可用的工具定义。"""
        return load_tool_definitions()

    @staticmethod
    def _build_tool_skill_map():
        tool_skill_map = {}
        for skill_definition in load_skill_definitions():
            skill_name = str(skill_definition.get("skill_name", "") or "").strip()
            if not skill_name:
                continue
            for tool_definition in skill_definition.get("tools", []) or []:
                function_name = str((tool_definition.get("function") or {}).get("name", "")).strip()
                if function_name:
                    tool_skill_map[function_name] = skill_name
        return tool_skill_map

    def _set_current_turn_agent_request(
        self,
        *,
        user_input: str = "",
        route: str = "",
        selected_ability: str = "",
        reason: str = "",
        ranked_candidates=None,
    ):
        self._current_turn_agent_request = {
            "user_input": str(user_input or "").strip(),
            "route": str(route or "").strip().lower(),
            "selected_ability": str(selected_ability or "").strip(),
            "reason": str(reason or "").strip(),
            "ranked_candidates": [
                dict(item)
                for item in (ranked_candidates or [])
                if isinstance(item, dict)
            ],
        }
        self._turn_loaded_deferred_tool_names = set()
        self._turn_active_tools_snapshot = []

    @staticmethod
    def _tool_name_from_definition(tool_definition: dict) -> str:
        return str((tool_definition.get("function") or {}).get("name", "")).strip()

    @staticmethod
    def _truncate_context_text(value: str, max_chars: int = 320) -> str:
        normalized = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(normalized) > max_chars:
            return normalized[: max_chars - 3] + "..."
        return normalized

    @staticmethod
    def _truncate_multiline_context_text(value: str, max_chars: int = 320) -> str:
        normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        if len(normalized) > max_chars:
            return normalized[: max_chars - 3].rstrip() + "..."
        return normalized

    def _reset_visible_agent_steps(self):
        with self._runtime_lock:
            if self._current_turn_visible_agent_steps:
                self._historical_agent_steps.extend(self._current_turn_visible_agent_steps)
                if len(self._historical_agent_steps) > HISTORICAL_AGENT_STEP_MAX_COUNT:
                    self._historical_agent_steps = self._historical_agent_steps[
                        -HISTORICAL_AGENT_STEP_MAX_COUNT:
                    ]
            self._current_turn_visible_agent_steps = []

    def _record_tool_security_event(self, *, event_type: str, tool_name: str = "", detail: str = "", status: str = "", payload=None):
        with self._runtime_lock:
            event_payload = {
                "id": f"tool-security-{len(self._recent_tool_security_events) + 1}",
                "event_type": str(event_type or "").strip(),
                "tool_name": str(tool_name or "").strip(),
                "detail": self._truncate_context_text(detail, max_chars=240),
                "status": str(status or "").strip(),
                "timestamp": self._format_readable_time(time.time()),
                "input_count": self.input_num,
                "payload": dict(payload or {}) if isinstance(payload, dict) else {},
            }
            self._recent_tool_security_events.append(event_payload)
            if len(self._recent_tool_security_events) > 20:
                self._recent_tool_security_events = self._recent_tool_security_events[-20:]

    def _create_pending_tool_approval(self, *, tool_name: str, function_args: dict, policy_result: dict):
        with self._runtime_lock:
            self._pending_tool_approval_seq += 1
            approval_id = f"approval-{self._pending_tool_approval_seq}"
            approval_payload = {
                "approval_id": approval_id,
                "tool_name": str(tool_name or "").strip(),
                "arguments": dict(function_args or {}),
                "policy": dict(policy_result or {}),
                "status": "pending",
                "created_at": self._format_readable_time(time.time()),
                "updated_at": self._format_readable_time(time.time()),
                "resolved_at": "",
                "decision": "",
                "input_count": self.input_num,
                "result": {},
            }
            self._pending_tool_approvals.append(approval_payload)
        self._record_tool_security_event(
            event_type="approval_requested",
            tool_name=tool_name,
            detail=str(policy_result.get("reason") or "该工具调用需要授权。"),
            status="pending",
            payload={"approval_id": approval_id},
        )
        return approval_payload

    def listPendingToolApprovals(self):
        with self._runtime_lock:
            return {
                "ok": True,
                "count": len(self._pending_tool_approvals),
                "approvals": [dict(item) for item in self._pending_tool_approvals],
            }

    def resolveToolApproval(self, ApprovalId: str, Decision: str):
        normalized_approval_id = str(ApprovalId or "").strip()
        normalized_decision = str(Decision or "").strip().lower()
        if normalized_decision not in {"approved", "rejected"}:
            return {"ok": False, "error": "Decision must be approved or rejected."}
        with self._runtime_lock:
            target = None
            for item in self._pending_tool_approvals:
                if str(item.get("approval_id") or "").strip() == normalized_approval_id:
                    target = item
                    break
            if target is None:
                return {"ok": False, "error": f"Unknown approval id: {normalized_approval_id}"}
            if str(target.get("status") or "").strip().lower() != "pending":
                return {"ok": False, "error": f"Approval already resolved: {normalized_approval_id}"}
            target["status"] = "approved" if normalized_decision == "approved" else "rejected"
            target["decision"] = normalized_decision
            target["updated_at"] = self._format_readable_time(time.time())
            target["resolved_at"] = target["updated_at"]

        if normalized_decision == "rejected":
            self._record_tool_security_event(
                event_type="approval_rejected",
                tool_name=target.get("tool_name", ""),
                detail=f"Approval rejected for {target.get('tool_name', '')}",
                status="rejected",
                payload={"approval_id": normalized_approval_id},
            )
            return {
                "ok": True,
                "decision": "rejected",
                "approval": dict(target),
                "tool_result": {},
            }

        with self._runtime_lock:
            security_config = dict((self.config or {}).get("Security", {}))
            approved_tools = {
                str(item or "").strip()
                for item in (security_config.get("approved_tools") or [])
                if str(item or "").strip()
            }
            approved_tools.add(str(target.get("tool_name") or "").strip())
            security_config["approved_tools"] = sorted(approved_tools)
            self.config["Security"] = security_config

        current_session_context = dict(self._active_tool_session_context or {}) if self._active_tool_session_context else None
        if current_session_context:
            current_session_context["approved_tools"] = set(approved_tools)
            self._set_active_tool_session_context(current_session_context)
        else:
            self._set_active_tool_session_context(self._build_tool_session_context())

        synthetic_tool_call = {
            "id": f"{normalized_approval_id}-tool-call",
            "type": "function",
            "function": {
                "name": target.get("tool_name", ""),
                "arguments": json.dumps(target.get("arguments") or {}, ensure_ascii=False),
            },
        }
        result = self.execute_tool_call(synthetic_tool_call)
        with self._runtime_lock:
            target["result"] = dict(result) if isinstance(result, dict) else {"value": result}
        self._record_tool_security_event(
            event_type="approval_approved",
            tool_name=target.get("tool_name", ""),
            detail=f"Approval approved for {target.get('tool_name', '')}",
            status="approved",
            payload={"approval_id": normalized_approval_id},
        )
        return {
            "ok": True,
            "decision": "approved",
            "approval": dict(target),
            "tool_result": dict(target.get("result") or {}) if isinstance(target.get("result"), dict) else target.get("result"),
        }

    def _append_visible_agent_step(
        self,
        *,
        step: int,
        phase: str,
        title: str,
        detail: str = "",
        status: str = "completed",
        tool_name: str = "",
    ):
        normalized_title = self._truncate_context_text(title, max_chars=80)
        normalized_detail = self._truncate_context_text(detail, max_chars=220)
        with self._runtime_lock:
            self._current_turn_visible_agent_step_seq += 1
            step_payload = {
                "id": f"agent-step-{self._current_turn_visible_agent_step_seq}",
                "step": max(1, int(step or 1)),
                "phase": str(phase or "tool").strip(),
                "tool_name": str(tool_name or "").strip(),
                "title": normalized_title,
                "detail": normalized_detail,
                "status": str(status or "completed").strip(),
                "timestamp": self._format_readable_time(time.time()),
                "input_count": self.input_num,
            }
            self._current_turn_visible_agent_steps.append(step_payload)
            if len(self._current_turn_visible_agent_steps) > VISIBLE_AGENT_STEP_MAX_COUNT:
                self._current_turn_visible_agent_steps = self._current_turn_visible_agent_steps[
                    -VISIBLE_AGENT_STEP_MAX_COUNT:
                ]

    def _finalize_visible_agent_step(
        self,
        *,
        step: int,
        phase: str,
        title: str,
        detail: str = "",
        status: str = "completed",
        tool_name: str = "",
    ):
        normalized_tool_name = str(tool_name or "").strip()
        normalized_phase = str(phase or "tool").strip()
        normalized_title = self._truncate_context_text(title, max_chars=80)
        normalized_detail = self._truncate_context_text(detail, max_chars=220)
        with self._runtime_lock:
            for index in range(len(self._current_turn_visible_agent_steps) - 1, -1, -1):
                current_item = self._current_turn_visible_agent_steps[index]
                if (
                    int(current_item.get("step", 0) or 0) == max(1, int(step or 1))
                    and str(current_item.get("phase", "") or "").strip() == normalized_phase
                    and str(current_item.get("tool_name", "") or "").strip() == normalized_tool_name
                    and str(current_item.get("status", "") or "").strip() == "running"
                ):
                    current_item["title"] = normalized_title
                    current_item["detail"] = normalized_detail
                    current_item["status"] = str(status or "completed").strip()
                    current_item["timestamp"] = self._format_readable_time(time.time())
                    return
        self._append_visible_agent_step(
            step=step,
            phase=phase,
            title=title,
            detail=detail,
            status=status,
            tool_name=tool_name,
        )

    def _build_visible_tool_step_title(self, tool_name: str, *, phase: str, tool_result=None) -> str:
        return tool_rendering_helpers._build_visible_tool_step_title(
            self,
            tool_name,
            phase=phase,
            tool_result=tool_result,
        )

    def _build_visible_tool_step_detail(self, tool_name: str, tool_result, *, function_args=None) -> str:
        return tool_rendering_helpers._build_visible_tool_step_detail(
            self,
            tool_name,
            tool_result,
            function_args=function_args,
        )

    def _build_recent_topic_digest(self, max_messages: int = 4, max_chars_per_message: int = 280) -> str:
        topic_records = [
            dict(item)
            for item in self._topic_records.get(self.topicGroup, [])
            if str(item.get("role", "")).strip().lower() in {"user", "assistant"}
        ]
        if not topic_records:
            return ""
        lines = []
        for record in topic_records[-max_messages:]:
            role = str(record.get("role", "assistant") or "assistant").strip().lower()
            content = self._truncate_context_text(record.get("content", ""), max_chars=max_chars_per_message)
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def build_subagent_seed_messages(self, agent_type=None, task_context: dict | None = None) -> list:
        normalized_agent_type = str(agent_type or "general").strip().lower() or "general"
        agent_registry = getattr(getattr(self, "subagent_runtime", None), "agent_registry", None)
        agent_definition = agent_registry.get(normalized_agent_type) if agent_registry else {}
        base_prompt_name = str((agent_definition or {}).get("system_prompt") or "Agent").strip() or "Agent"
        messages = [self._build_system_message(base_prompt_name)]
        relevant_files = list((task_context or {}).get("relevant_files") or [])
        current_skill_prompt = self._build_agent_skill_prompt_for_current_turn(additional_paths=relevant_files)
        if str(current_skill_prompt).strip():
            messages.append({"role": "system", "content": current_skill_prompt})

        context_parts = []

        if task_context:
            if task_context.get("background"):
                context_parts.append(f"Background:\n{task_context['background']}")

            if task_context.get("constraints"):
                context_parts.append(f"Constraints:\n{task_context['constraints']}")

            if task_context.get("expected_goal"):
                context_parts.append(f"Expected goal:\n{task_context['expected_goal']}")

            if task_context.get("workspace_root"):
                context_parts.append(f"Workspace root:\n{task_context['workspace_root']}")

            if relevant_files:
                files_list = "\n".join(relevant_files)
                context_parts.append(f"Relevant files:\n{files_list}")

            if task_context.get("expected_output"):
                context_parts.append(f"Expected output format:\n{task_context['expected_output']}")

        recent_digest = self._build_recent_topic_digest()
        if recent_digest:
            context_parts.append(f"Recent parent dialogue:\n{recent_digest}")

        if context_parts:
            context_heading = (
                "Context for autonomous work:\n\n"
                if normalized_agent_type == "autonomous"
                else "Context for delegated work:\n\n"
            )
            messages.append(
                {
                    "role": "system",
                    "content": context_heading + "\n\n".join(context_parts),
                }
            )
        return messages

    @staticmethod
    def _format_readable_time(value):
        """将时间值格式化为可读字符串。"""
        if value in (None, ""):
            return "Unknown"

        parsed_time = None
        if isinstance(value, datetime):
            parsed_time = value
        elif isinstance(value, (int, float)):
            timestamp_value = float(value)
            if timestamp_value > 1e12:
                timestamp_value /= 1000
            try:
                parsed_time = datetime.fromtimestamp(timestamp_value)
            except (OverflowError, OSError, ValueError):
                parsed_time = None
        elif isinstance(value, str):
            raw_value = value.strip()
            if not raw_value:
                return ""
            try:
                timestamp_value = float(raw_value)
                if timestamp_value > 1e12:
                    timestamp_value /= 1000
                parsed_time = datetime.fromtimestamp(timestamp_value)
            except (TypeError, ValueError, OverflowError, OSError):
                try:
                    parsed_time = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
                except ValueError:
                    return raw_value
        else:
            return str(value)

        if parsed_time is None:
            return str(value)
        if parsed_time.tzinfo is not None:
            parsed_time = parsed_time.astimezone().replace(tzinfo=None)
        return parsed_time.strftime("%Y-%m-%d %H:%M:%S")

    # ------------------------------------------------------------------
    # RAG ?????
    # ------------------------------------------------------------------
    def _build_rag_context(self, rag_results):
        """将 RAG 结果整理为提示词上下文。"""
        query_system_time = self._format_readable_time(datetime.now())
        memories = []
        seen_keys = set()

        for item in rag_results:
            payload = item.payload or {}
            text = str(payload.get("text") or payload.get("personalizedText") or "").strip()
            if not text:
                continue
            memory_timestamp = payload.get("timestamp")
            if memory_timestamp in (None, ""):
                memory_timestamp = payload.get("UpdateTime")

            dedupe_key = (
                getattr(item, "id", None),
                str(memory_timestamp or ""),
                text
            )
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

            memories.append({
                "text": text,
                "timestamp": self._format_readable_time(memory_timestamp)
            })

        return {
            "query_system_time": query_system_time,
            "memories": memories
        }

    def _split_query_parts(self, user_input: str):
        """按常见中英文分隔符拆分查询文本，供 RAG 分段检索使用。"""
        raw_text = str(user_input or "").strip()
        if not raw_text:
            return []
        # 同时兼容中英文问号、叹号、逗号和分号，便于将长输入拆成多个检索片段。
        parts = [part.strip() for part in re.split(r"[?？!！,，、；;]+", raw_text) if part.strip()]
        parts = [p for p in parts if len(p) >= 4 or not p.isascii()]
        return parts or [raw_text]

    def _get_or_create_search_qdrant(self, attr_name: str, collection_config: dict):
        """获取或延迟创建指定用途的 Qdrant 实例。"""
        qdrant_instance = getattr(self, attr_name, None)
        if qdrant_instance is not None:
            return qdrant_instance
        qdrant_cls = _ensure_qdrant_available()
        qdrant_instance = qdrant_cls(
            collection_config["name"],
            size=collection_config["vector_size"],
            session=self.session,
        )
        qdrant_instance.createDB()
        setattr(self, attr_name, qdrant_instance)
        return qdrant_instance

    def _get_cached_query_embedding(self, text: str, embedding_model: SentenceTransformer):
        """读取或写入查询向量缓存，并维护 LRU 顺序。"""
        cache_key = str(text or "").strip()
        if not cache_key:
            return None, False
        cached_vector = self._query_embedding_cache.get(cache_key)
        if cached_vector is not None:
            self._query_embedding_cache.move_to_end(cache_key)
            return cached_vector, True
        vector = embedding_model.encode(cache_key)
        self._query_embedding_cache[cache_key] = vector
        if len(self._query_embedding_cache) > self._query_embedding_cache_limit:
            self._query_embedding_cache.popitem(last=False)
        return vector, False

    def _prepare_query_embeddings(self, user_input: str, embedding_model: SentenceTransformer):
        """为查询片段生成 embedding，并统计缓存命中。"""
        prepared_parts = []
        cache_hits = 0
        cache_misses = 0
        for part in self._split_query_parts(user_input):
            vector, is_cache_hit = self._get_cached_query_embedding(part, embedding_model)
            if vector is None:
                continue
            prepared_parts.append({"text": part, "embedding": vector})
            if is_cache_hit:
                cache_hits += 1
            else:
                cache_misses += 1
        logger.info(
            "Query embedding prepared | part_count=%s | cache_hits=%s | cache_misses=%s",
            len(prepared_parts),
            cache_hits,
            cache_misses
        )
        return prepared_parts

    def _close_search_resources(self):
        """关闭检索相关的 Qdrant 实例并清空句柄。"""
        for attr_name in ("_intention_qdrant", "_rag_qdrant", "_memory_qdrant"):
            qdrant_instance = getattr(self, attr_name, None)
            if qdrant_instance is None:
                continue
            try:
                qdrant_instance.close()
            except Exception:
                logger.exception("Failed to close search qdrant | attr=%s", attr_name)
            finally:
                setattr(self, attr_name, None)

    @staticmethod
    def _parse_topic_file_identity(file_name: str):
        """从话题历史文件名中解析会话前缀与 topic_group。"""
        base_name = os.path.basename(str(file_name or ""))
        match = re.match(r"^(?P<prefix>.+)_(?P<group>\d+)\.jsonl$", base_name)
        if match:
            return match.group("prefix"), int(match.group("group"))
        return os.path.splitext(base_name)[0], 0

    @staticmethod
    def _build_topic_archive_excerpt(topic_records, max_messages: int = 4, max_chars: int = 360):
        excerpt_lines = []
        for record in list(topic_records or [])[-max(1, int(max_messages or 4)):]:
            role = str(record.get("role", "assistant") or "assistant").strip() or "assistant"
            content = str(record.get("content", "")).strip()
            if not content:
                continue
            excerpt_lines.append(f"{role}: {content}")
        excerpt_text = "\n".join(excerpt_lines).strip()
        if len(excerpt_text) > max_chars:
            excerpt_text = excerpt_text[: max_chars - 1].rstrip() + "…"
        return excerpt_text

    def _summarize_topic_records_for_archive(self, topic_records):
        """把已结束的话题压缩成可检索的段落摘要。"""
        normalized_records = [
            {
                "role": str(record.get("role", "assistant") or "assistant"),
                "content": str(record.get("content", "")),
            }
            for record in topic_records or []
            if str(record.get("content", "")).strip()
        ]
        if not normalized_records:
            return ""
        fallback_lines = [
            f'{record["role"]}: {record["content"]}'
            for record in normalized_records[-4:]
        ]
        fallback_summary = " | ".join(fallback_lines)[:500]
        task_config = self._get_topic_postprocess_llm_task_config("topic_archive_summary")
        if not task_config.get("enabled", True):
            return fallback_summary

        summary_prompt = render_prompt_text(
            "TopicArchiveSummary",
            {
                "TOPIC_RECORDS_JSON": json.dumps(
                    normalized_records,
                    ensure_ascii=False,
                    indent=2,
                )
            },
        )
        model_key = task_config.get("model")
        if not model_key:
            return fallback_summary
        try:
            summary_text = call_LLM(
                [{"role": "system", "content": summary_prompt}],
                model_key,
                self.session,
                task_config.get("thinking", False),
                task_config.get("json_mode", False),
                caller="topic_archive_summary",
                reasoning_effort=task_config.get("reasoning_effort", "high"),
            )
            return str(summary_text or "").strip()
        except Exception:
            logger.exception("Topic archive summary failed")
            return fallback_summary

    def _archive_completed_topic(self, archive_task: dict):
        """把已结束的话题摘要写入 SQLite 归档库（不再写入 Qdrant 向量库）。

        话题归档通过 searchFullText / listTopicsByDate 工具按需检索，
        不参与自动向量召回，避免长摘要文本产生语义漂移噪声。
        """
        if not archive_task:
            return
        topic_records = archive_task.get("records") or []
        if not topic_records:
            return

        summary_text = self._summarize_topic_records_for_archive(topic_records)
        if not summary_text:
            return

        topic_group = archive_task.get("topic_group")
        source_file = str(archive_task.get("source_file") or "").strip() or os.path.basename(
            self._topic_file_path(topic_group)
        )
        source_session_prefix, source_topic_group = self._parse_topic_file_identity(source_file)
        archive_time = time.time()

        try:
            self.topic_archive_repository.upsert_archive(
                source_file=source_file,
                source_session_prefix=source_session_prefix,
                source_topic_group=source_topic_group,
                topic_message_count=len(topic_records),
                summary_text=summary_text,
                topic_records=topic_records,
                archived_at=archive_time,
            )
        except Exception:
            logger.exception("Failed to archive completed topic to sqlite | topic_group=%s", topic_group)
            return

        self._invalidate_memory_debug_snapshot()
        logger.info(
            "Archived completed topic to sqlite | topic_group=%s | source_file=%s | message_count=%s",
            topic_group,
            source_file,
            len(topic_records),
        )

    @staticmethod
    def _build_memory_result_key(item):
        payload = item.payload or {}
        text = str(payload.get("text") or payload.get("personalizedText") or "").strip()
        memory_timestamp = payload.get("timestamp")
        if memory_timestamp in (None, ""):
            memory_timestamp = payload.get("UpdateTime")
        return (
            getattr(item, "id", None),
            str(memory_timestamp or ""),
            text,
        )

    def _select_top_memory_results(self, memory_results, top_n: int = 8):
        """合并重复命中，仅保留分数更高的历史记忆。"""
        best_results = OrderedDict()
        for item in memory_results:
            result_key = self._build_memory_result_key(item)
            existing_item = best_results.get(result_key)
            if existing_item is None or float(getattr(item, "score", 0.0)) > float(getattr(existing_item, "score", 0.0)):
                best_results[result_key] = item
        ranked_results = sorted(
            best_results.values(),
            key=lambda candidate: float(getattr(candidate, "score", 0.0)),
            reverse=True,
        )
        return ranked_results[:max(0, int(top_n))]

    @staticmethod
    def _is_historical_memory_query(user_input: str) -> bool:
        """判断用户是否在追问过去状态、变更轨迹或记忆冲突。"""
        normalized_input = str(user_input or "").strip()
        if not normalized_input:
            return False
        if MEMORY_HISTORY_QUERY_PATTERN.search(normalized_input):
            return True
        if ("之前" in normalized_input or "以前" in normalized_input) and ("现在" in normalized_input or "后来" in normalized_input):
            return True
        return False

    def _collect_memory_results(self, query_parts, memory_qdrant: Qdrant):
        """按查询片段汇总 memory 库的原始召回结果。"""
        memory_results = []
        for query_part in query_parts:
            memory_results.extend(memory_qdrant.SearchRawScore(query_part["embedding"]))
        logger.info(
            "Memory raw search succeeded | part_count=%s | hit_count=%s",
            len(query_parts),
            len(memory_results),
        )
        return memory_results

    def _rerank_memory_results(self, user_input: str, memory_results, memory_qdrant: Qdrant):
        """对候选记忆做一次全文 rerank。"""
        normalized_input = str(user_input or "").strip()
        if not normalized_input or not memory_results:
            return memory_results

        reranked_results = memory_qdrant._rerank_and_boost(memory_results, query_text=normalized_input)
        min_score = float(self.config.get("MemoryRecall", {}).get("rerank_min_score", 0.65))
        return [
            item
            for item in reranked_results
            if float(getattr(item, "score", 0.0)) >= min_score
        ]

    def _get_topic_archive_payload(self, source_file: str):
        archive_record = self.topic_archive_repository.get_archive_by_source_file(source_file)
        if not archive_record:
            return None
        topic_records = list(archive_record.get("topic_records") or [])
        return {
            "summary_text": str(archive_record.get("summary_text", "")).strip(),
            "topic_message_count": int(archive_record.get("topic_message_count") or 0),
            "archived_at": self._format_readable_time(archive_record.get("archived_at")),
            "topic_excerpt": self._build_topic_archive_excerpt(topic_records),
        }

    @staticmethod
    def _normalize_intention_examples(examples):
        """清洗并去重意图示例文本。"""
        normalized_examples = []
        seen_examples = set()
        pending_values = list(examples or [])
        while pending_values:
            value = pending_values.pop(0)
            if isinstance(value, (list, tuple, set)):
                pending_values[:0] = list(value)
                continue
            normalized = str(value or "").strip()
            normalized = re.sub(r"^[\-\*\d\.\)閵嗕箺s]+", "", normalized)
            normalized = re.sub(r"\s+", " ", normalized).strip()
            if len(normalized) < 2 or normalized in seen_examples:
                continue
            seen_examples.add(normalized)
            normalized_examples.append(normalized)
        return normalized_examples

    @staticmethod
    def _get_intention_storage_function_name(ability: dict) -> str:
        """提取意图样本写入时使用的函数名。"""
        return str((ability or {}).get("name", "")).strip()

    @staticmethod
    def _normalize_intention_payload(payload):
        """将旧版意图 payload 规范为统一结构。"""
        payload = payload or {}
        text = str(payload.get("text") or payload.get("personalizedText") or "").strip()
        if not text:
            return None

        function_name = str(payload.get("FunctionName") or payload.get("ability_name") or "").strip()
        if not function_name:
            ability_key = str(payload.get("ability_key", "")).strip()
            if ":" in ability_key:
                function_name = ability_key.split(":", 1)[1].strip()
            else:
                function_name = ability_key

        function_name = str(function_name or "").strip()
        if not function_name:
            return None

        return {
            "text": text,
            "FunctionName": function_name
        }

    def _collect_existing_intention_examples(self, intention_qdrant: Qdrant):
        """遍历向量库并收集已有的意图示例与记录 ID。"""
        inventory_by_function = {}
        offset = None
        while True:
            records, offset = intention_qdrant.client.scroll(
                collection_name=intention_qdrant.CollectionName,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
            if not records:
                break
            for record in records:
                normalized_payload = self._normalize_intention_payload(record.payload or {})
                if normalized_payload is None:
                    continue
                function_name = normalized_payload["FunctionName"]
                function_inventory = inventory_by_function.setdefault(
                    function_name,
                    {"texts": set(), "record_ids": []}
                )
                function_inventory["texts"].add(normalized_payload["text"])
                if record.id is not None:
                    function_inventory["record_ids"].append(record.id)
            if offset is None:
                break
        return inventory_by_function

    def _remove_stale_intention_function_records(
        self,
        intention_qdrant: Qdrant,
        existing_inventory: dict,
        valid_function_names: set,
    ):
        """删除意图库中已不再对应任何能力定义的 FunctionName 记录。"""
        stale_function_names = sorted(
            function_name
            for function_name in existing_inventory.keys()
            if function_name not in valid_function_names
        )
        if not stale_function_names:
            return 0, []

        stale_record_ids = []
        seen_record_ids = set()
        for function_name in stale_function_names:
            for record_id in existing_inventory.get(function_name, {}).get("record_ids", []):
                record_id_key = str(record_id)
                if not record_id_key or record_id_key in seen_record_ids:
                    continue
                seen_record_ids.add(record_id_key)
                stale_record_ids.append(record_id)

        if stale_record_ids:
            intention_qdrant.client.delete(
                collection_name=intention_qdrant.CollectionName,
                points_selector=PointIdsList(points=stale_record_ids),
            )

        for function_name in stale_function_names:
            existing_inventory.pop(function_name, None)

        logger.info(
            "Removed stale intention function records | stale_function_count=%s | stale_record_count=%s | stale_functions=%s",
            len(stale_function_names),
            len(stale_record_ids),
            stale_function_names,
        )
        return len(stale_record_ids), stale_function_names

    def _migrate_existing_intention_payloads(self, intention_qdrant: Qdrant):
        """将旧版意图 payload 迁移到统一结构。"""
        migrated_count = 0
        offset = None
        while True:
            records, offset = intention_qdrant.client.scroll(
                collection_name=intention_qdrant.CollectionName,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=True
            )
            if not records:
                break
            for record in records:
                normalized_payload = self._normalize_intention_payload(record.payload or {})
                if normalized_payload is None or (record.payload or {}) == normalized_payload:
                    continue
                if record.vector is None:
                    logger.warning("Skip migrating intention record without vector | id=%s", record.id)
                    continue
                intention_qdrant.addRaw(
                    vector=record.vector,
                    text=normalized_payload["text"],
                    id=record.id,
                    deduplication=False,
                    FunctionName=normalized_payload["FunctionName"]
                )
                migrated_count += 1
            if offset is None:
                break
        if migrated_count:
            logger.info("Migrated legacy intention payloads | migrated_count=%s", migrated_count)
        return migrated_count

    def _build_intention_generation_messages(self, ability: dict, target_count: int, existing_examples):
        """构造生成意图示例所需的 LLM 消息。"""
        ability_context = {
            "ability_type": ability.get("ability_type"),
            "ability_name": ability.get("name"),
            "description": ability.get("description"),
            "when_to_use": ability.get("when_to_use", []),
            "parameter_summaries": ability.get("parameter_summaries", []),
            "registered_tools": ability.get("registered_tools", []),
            "existing_examples": list(existing_examples)
        }
        ability_context_json = json.dumps(ability_context, ensure_ascii=False, indent=2)
        return [
            {
                "role": "system",
                "content": setSystemPrompt("IntentionExampleSystem")
            },
            {
                "role": "user",
                "content": setSystemPrompt("IntentionExampleUser").format(
                    target_count=target_count,
                    ability_context_json=ability_context_json
                )
            }
        ]

    def _fallback_intention_examples(self, ability: dict):
        """在 LLM 不可用时生成保底意图示例。"""
        fallback_examples = []
        if ability.get("manual_examples"):
            fallback_examples.extend(ability["manual_examples"])

        description = str(ability.get("description", "")).strip()
        if description:
            fallback_examples.append(f"Help me with: {description}")

        return self._normalize_intention_examples(fallback_examples)

    def _generate_intention_examples_with_llm(self, ability: dict, target_count: int, existing_examples):
        """调用 LLM 生成意图示例，并做好解析兜底。"""
        if target_count <= 0:
            return []

        prompt_messages = self._build_intention_generation_messages(
            ability,
            target_count=max(target_count + 2, target_count),
            existing_examples=existing_examples
        )
        try:
            agent_task_config = self._get_model_select_task_config("Agent")
            raw_result = call_LLM(
                prompt_messages,
                agent_task_config.get("model") or self.model,
                self.session,
                agent_task_config.get("thinking", True),
                True,
                caller="intention_example_gen",
                reasoning_effort=agent_task_config.get("reasoning_effort", "high"),
            )
        except Exception:
            logger.exception(
                "Failed to generate intention examples with LLM | ability=%s",
                ability.get("ability_key")
            )
            return self._fallback_intention_examples(ability)[:target_count]
        try:
            parsed_result = json.loads(raw_result)
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse generated intention examples as JSON | ability=%s | raw=%s",
                ability.get("ability_key"),
                raw_result
            )
            return self._fallback_intention_examples(ability)[:target_count]

        generated_examples = []
        for key in ("examples", "utterances", "queries", "inputs"):
            if key in parsed_result:
                generated_examples = parsed_result.get(key) or []
                break
        generated_examples = self._normalize_intention_examples(generated_examples)
        generated_examples = [
            example for example in generated_examples
            if example not in existing_examples
        ]
        if not generated_examples:
            generated_examples = self._fallback_intention_examples(ability)
        return generated_examples[:target_count]

    def _store_intention_example(
        self,
        intention_qdrant: Qdrant,
        embedding_model: SentenceTransformer,
        ability: dict,
        example_text: str
    ):
        """将单条意图示例向量化后写入向量库。"""
        normalized_example = str(example_text or "").strip()
        if not normalized_example:
            return
        function_name = self._get_intention_storage_function_name(ability)
        if not function_name:
            return

        vector = embedding_model.encode(normalized_example)
        intention_qdrant.addRaw(
            vector=vector,
            text=normalized_example,
            deduplication=False,
            FunctionName=function_name
        )

    def ensure_intention_examples_synced(self, embedding_model: SentenceTransformer):
        """确保各能力的意图示例已经写入向量库。"""
        if self._uses_llm_intent_router():
            logger.info("Intention sync skipped | reason=llm_intent_router")
            return

        abilities = load_intention_ability_definitions()
        if not abilities:
            logger.info("Intention sync skipped | reason=no_abilities")
            return

        intention_qdrant = self._get_or_create_search_qdrant("_intention_qdrant", self._intention_collection)
        self._migrate_existing_intention_payloads(intention_qdrant)
        existing_inventory = self._collect_existing_intention_examples(intention_qdrant)
        valid_function_names = {
            self._get_intention_storage_function_name(ability)
            for ability in abilities
            if self._get_intention_storage_function_name(ability)
        }
        removed_stale_count, _ = self._remove_stale_intention_function_records(
            intention_qdrant,
            existing_inventory,
            valid_function_names,
        )
        existing_examples = {
            function_name: set(function_inventory.get("texts", set()))
            for function_name, function_inventory in existing_inventory.items()
        }
        inserted_count = 0

        for ability in abilities:
            ability_key = ability["ability_key"]
            function_name = self._get_intention_storage_function_name(ability)
            if not function_name:
                logger.warning("Intention sync skipped | reason=missing_function_name | ability=%s", ability_key)
                continue
            current_examples = set(existing_examples.get(function_name, set()))

            if current_examples:
                existing_examples[function_name] = current_examples
                logger.info(
                    "Intention ability sync skipped | ability=%s | function_name=%s | existing_example_count=%s | reason=already_seeded",
                    ability_key,
                    function_name,
                    len(current_examples),
                )
                continue

            manual_examples = self._normalize_intention_examples(ability.get("manual_examples", []))
            for example in manual_examples:
                if example in current_examples:
                    continue
                self._store_intention_example(
                    intention_qdrant,
                    embedding_model,
                    ability,
                    example
                )
                current_examples.add(example)
                inserted_count += 1

            minimum_examples = max(1, int(ability.get("minimum_examples", 1)))
            missing_count = minimum_examples - len(current_examples)
            if missing_count > 0:
                generated_examples = self._generate_intention_examples_with_llm(
                    ability,
                    missing_count,
                    current_examples
                )
                for example in generated_examples:
                    if example in current_examples:
                        continue
                    self._store_intention_example(
                        intention_qdrant,
                        embedding_model,
                        ability,
                        example
                    )
                    current_examples.add(example)
                    inserted_count += 1

            existing_examples[function_name] = current_examples
            logger.info(
                "Intention ability synced | ability=%s | function_name=%s | example_count=%s | minimum_examples=%s",
                ability_key,
                function_name,
                len(current_examples),
                minimum_examples
            )

        logger.info(
            "Intention sync completed | ability_count=%s | inserted_examples=%s | removed_stale_records=%s",
            len(abilities),
            inserted_count,
            removed_stale_count,
        )
    # ------------------------------------------------------------------
    # Agent ????
    def getWeather(self, Location: str):
        """返回天气工具的占位结果。"""
        return f"Weather for {Location} is unavailable."

    def getLocation(self):
        """返回定位工具的占位结果。"""
        return "Location unavailable."

    # Agent 工具函数
    def _iter_unique_llm_configs(self, model_keys: list):
        """遍历去重后的 LLM 配置，用于模型预热。"""
        warmed = set()
        for model_key in model_keys:
            try:
                llm = get_LLM(model_key)
            except KeyError:
                logger.warning("Skip warmup for unknown model key: %s", model_key)
                continue
            warmup_key = (llm["url"], llm["modelName"], llm["API"])
            if warmup_key in warmed:
                continue
            warmed.add(warmup_key)
            yield llm

    def warmup_models(self, model_keys: list):
        """预热本地 embedding 模型与远端 LLM。"""
        local_embedding_model = self.ensure_local_embedding_model()
        warmup_messages = [
            {"role": "system", "content": "You are a concise assistant."},
            {"role": "user", "content": "ping"}
        ]
        for llm in self._iter_unique_llm_configs(model_keys):
            try:
                response = self.session.post(
                    llm["url"],
                    headers={
                        "Authorization": f'Bearer {llm["API"]}',
                        "Content-Type": "application/json"
                    },
                    data=json.dumps({
                        "model": llm["modelName"],
                        "messages": warmup_messages,
                        "stream": False,
                        "enable_thinking": False,
                        "thinking": {"type": "disabled"},
                    }),
                    timeout=(5, 20)
                )
                response.raise_for_status()
                logger.info("热启动完成| model=%s | status=%s", llm["modelName"], response.status_code)
            except Exception as e:
                logger.warning("热启动失败| model=%s | error=%s", llm["modelName"], str(e))
        return local_embedding_model

    def _build_agent_runtime_prompt(self, runtime_config: dict, *, supports_vision: bool = False):
        vision_line = (
            "- 当前 Agent 模型支持图像输入；如果需要额外视觉确认，可以把 browserScreenshot 当作辅助信息来源。\n"
            if supports_vision
            else "- 当前 Agent 模型不支持图像输入；不要把 browserScreenshot 当成必需步骤，优先依赖 browserSnapshot / browserExtractPage 的文本结果。\n"
        )
        return (
            "Agent 运行约束:\n"
            f"- 普通 tool 调用最多 {runtime_config['max_tool_calls']} 次，不包含 `{SUMMARY_TOOL_NAME}`。\n"
            f"- 当同名 tool 连续调用达到 {runtime_config['max_consecutive_same_tool_calls']} 次时，必须立刻调用 `{SUMMARY_TOOL_NAME}`。\n"
            f"{vision_line}"
            f"- 不要直接输出 assistant 文本，结束时只能调用 `{SUMMARY_TOOL_NAME}`。\n"
            f"- 最终回复固定使用 `{ROLE_PLAY_TASK_NAME}` 模式生成。"
        )

    @staticmethod
    def _stringify_tool_result(tool_result, max_chars: int = 320):
        try:
            if isinstance(tool_result, str):
                serialized = tool_result
            else:
                serialized = json.dumps(tool_result, ensure_ascii=False)
        except Exception:
            serialized = str(tool_result)
        normalized = re.sub(r"\s+", " ", str(serialized or "")).strip()
        if len(normalized) > max_chars:
            return normalized[: max_chars - 3] + "..."
        return normalized or "No tool output."

    def _append_agent_log_tool_message(
        self,
        agent_log_id: int,
        function_name: str,
        *,
        tool_result=None,
        function_args: dict | None = None,
        tag: str = "",
    ):
        """Append a compact tool-result message into the current LLM inspector entry."""
        if int(agent_log_id or 0) <= 0:
            return
        normalized_name = str(function_name or "").strip() or "tool"
        label = f"{normalized_name}:{tag}" if tag else normalized_name
        if normalized_name == SUMMARY_TOOL_NAME:
            try:
                payload_text = json.dumps(function_args or {}, ensure_ascii=False)
            except Exception:
                payload_text = str(function_args or {})
            content = f"[{label}] {payload_text}"
        else:
            content = f"[{label}] {self._stringify_tool_result(tool_result, max_chars=500)}"
        append_llm_call_message(
            agent_log_id,
            {
                "role": "tool",
                "content": content,
            },
        )

    @staticmethod
    def _build_tool_approval_reason_text(tool_name: str, policy_result: dict = None) -> str:
        normalized_tool_name = str(tool_name or "").strip() or "当前工具"
        policy_result = policy_result if isinstance(policy_result, dict) else {}
        metadata = policy_result.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}

        if bool(metadata.get("admin_only", False)):
            return "这个工具需要管理员权限，所以要先征求你的授权。"

        risk_level = str(metadata.get("risk_level", "") or "").strip().lower()
        if risk_level == "privileged":
            return "这个工具涉及高权限操作，需要你先确认。"
        if risk_level == "guarded":
            return "这个工具属于受保护操作，需要你先确认。"

        reason = str(policy_result.get("reason", "") or "").strip()
        normalized_reason = reason.lower()
        if "admin privileges" in normalized_reason:
            return "这个工具需要管理员权限，所以要先征求你的授权。"
        if "requires approval" in normalized_reason:
            return "这个工具需要先得到你的确认。"
        if reason:
            return reason
        return f"工具 {normalized_tool_name} 需要先得到你的确认。"

    def _build_fallback_tool_approval_question(self, tool_name: str, function_args: dict = None, policy_result: dict = None) -> str:
        normalized_tool_name = str(tool_name or "").strip() or "当前工具"
        function_args = function_args if isinstance(function_args, dict) else {}
        reason_text = self._build_tool_approval_reason_text(normalized_tool_name, policy_result)
        args_desc = self._stringify_tool_result(function_args, max_chars=200)

        question_parts = [
            f"为了继续处理你的请求，我准备调用 {normalized_tool_name}。",
            reason_text,
        ]
        if args_desc and args_desc != "{}":
            question_parts.append(f"本次调用参数：{args_desc}")
        question_parts.append("要允许我现在执行它吗？")
        return "\n".join(part for part in question_parts if str(part or "").strip())

    def _build_tool_approval_context_excerpt(self, tmp_agent_message: list, max_items: int = 4) -> str:
        excerpts = []
        for message in reversed(list(tmp_agent_message or [])):
            role = str(message.get("role", "") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = message.get("content", "")
            if isinstance(content, list):
                content = "".join(
                    str(item.get("text", ""))
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                )
            content = self._truncate_context_text(content, max_chars=120)
            if not content:
                continue
            role_label = "用户" if role == "user" else "助手"
            excerpts.append(f"{role_label}：{content}")
            if len(excerpts) >= max(1, int(max_items or 4)):
                break
        excerpts.reverse()
        return "\n".join(excerpts)

    def _sanitize_generated_tool_approval_question(self, text: str, *, tool_name: str) -> str:
        normalized_tool_name = str(tool_name or "").strip() or "当前工具"
        raw_text = str(text or "").replace("```", " ").strip().strip("\"'")
        lines = []
        for raw_line in raw_text.splitlines():
            line = raw_line.strip().lstrip("-").strip()
            if not line:
                continue
            if line.lower().startswith("assistant:"):
                line = line.split(":", 1)[1].strip()
            if line.startswith("助手："):
                line = line[3:].strip()
            if line:
                lines.append(line)

        if not lines:
            return ""

        content_lines = []
        has_question_line = False
        for line in lines:
            content_lines.append(line)
            if "？" in line or "?" in line:
                has_question_line = True

        joined_content = "\n".join(content_lines)
        if normalized_tool_name not in joined_content:
            content_lines.insert(0, f"为了继续处理你的请求，我准备调用 {normalized_tool_name}。")

        if not has_question_line:
            content_lines.append("要允许我现在执行它吗？")

        normalized = "\n".join(content_lines)
        if len(normalized) > 240:
            return ""
        return normalized

    def _build_tool_approval_question(
        self,
        tool_name: str,
        function_args: dict = None,
        policy_result: dict = None,
        tmp_agent_message: list = None,
        runtime_settings: dict = None,
    ) -> str:
        fallback_question = self._build_fallback_tool_approval_question(
            tool_name,
            function_args=function_args,
            policy_result=policy_result,
        )
        runtime_settings = runtime_settings if isinstance(runtime_settings, dict) else {}
        model_key = str(runtime_settings.get("model_key", "") or "").strip()
        if not model_key:
            return fallback_question

        turn_request = dict(getattr(self, "_current_turn_agent_request", {}) or {})
        current_user_request = self._truncate_context_text(turn_request.get("user_input", ""), max_chars=160)
        context_excerpt = self._build_tool_approval_context_excerpt(tmp_agent_message, max_items=4)
        reason_text = self._build_tool_approval_reason_text(tool_name, policy_result)
        args_desc = self._stringify_tool_result(function_args if isinstance(function_args, dict) else {}, max_chars=200)
        prompt_payload = {
            "current_user_request": current_user_request,
            "recent_dialogue": context_excerpt,
            "tool_name": str(tool_name or "").strip(),
            "tool_arguments": args_desc,
            "approval_reason": reason_text,
        }
        prompt_messages = [
            {
                "role": "system",
                "content": (
                    "你是当前对话中的 Agent。"
                    "现在某个工具调用需要用户授权，请直接生成一段发给用户看的中文确认提问。"
                    "要求："
                    "语气自然，像正常对话，不要像系统告警；"
                    "明确说明准备调用哪个工具；"
                    "用自然语言解释为什么需要授权；"
                    "只有在确有帮助时才简短提及调用参数；"
                    "结尾必须询问用户是否允许执行；"
                    "不要列出固定选项，也不要追加选择行；"
                    "不要使用 Markdown 标题、警告符号、代码块、编号，也不要提内部字段名。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(prompt_payload, ensure_ascii=False),
            },
        ]

        try:
            generated_question = call_LLM(
                prompt_messages,
                model_key,
                self.session,
                False,
                False,
                stream=False,
                caller="AgentApprovalQuestion",
                reasoning_effort=runtime_settings.get("reasoning_effort", "high"),
            )
            sanitized_question = self._sanitize_generated_tool_approval_question(
                generated_question,
                tool_name=tool_name,
            )
            if sanitized_question:
                logger.info(
                    "Tool approval question generated by Agent | tool=%s | question=%s",
                    tool_name,
                    sanitized_question[:120],
                )
                return sanitized_question
        except Exception:
            logger.exception("Failed to generate tool approval question with Agent model | tool=%s", tool_name)

        return fallback_question

    def _build_pending_tool_approval_result(
        self,
        *,
        approval_id: str,
        tool_name: str,
        function_args: dict = None,
        policy_result: dict = None,
        approval_question: str = "",
    ) -> dict:
        return {
            "ok": False,
            "approval_pending": True,
            "approval_id": str(approval_id or "").strip(),
            "tool_name": str(tool_name or "").strip(),
            "tool_arguments": dict(function_args or {}),
            "reason": self._build_tool_approval_reason_text(tool_name, policy_result),
            "question": str(approval_question or "").strip(),
            "message": "该工具调用已暂停，等待用户授权确认。",
        }

    def _build_tool_approval_resume_system_message(self, state: dict, user_reply: str) -> str:
        function_name = str(state.get("pending_function_name", "") or "").strip()
        function_args = dict(state.get("pending_function_args") or {})
        approval_id = str(state.get("pending_approval_id", "") or "").strip()
        reason_text = self._build_tool_approval_reason_text(
            function_name,
            state.get("pending_policy_result") or {},
        )
        args_desc = self._stringify_tool_result(function_args, max_chars=200)
        reply_text = self._truncate_context_text(str(user_reply or "").strip(), max_chars=120)

        guidance_lines = [
            "系统提示：当前存在一个待处理的工具授权请求。",
            f"ApprovalId：{approval_id or 'unknown'}",
            f"待授权工具：{function_name or 'unknown'}",
            f"授权原因：{reason_text}",
        ]
        if args_desc and args_desc != "{}":
            guidance_lines.append(f"调用参数：{args_desc}")
        if reply_text:
            guidance_lines.append(f"用户刚才的回复：{reply_text}")
        guidance_lines.extend([
            "请结合前面的授权提问和最新用户回复自行判断。",
            "如果用户明确同意，调用 resolveToolApproval，并传入 Decision='approved'。",
            "如果用户明确拒绝，调用 resolveToolApproval，并传入 Decision='rejected'。",
            "如果用户表达不清楚，不要猜测，调用 askUser 继续确认。",
            f"在授权真正解决前，不要直接再次调用 {function_name or '该工具'}。",
        ])
        return "\n".join(guidance_lines)

    @staticmethod
    def _score_browser_element_for_prompt(element: dict) -> int:
        return tool_rendering_helpers._score_browser_element_for_prompt(element)

    @classmethod
    def _compress_browser_elements_for_prompt(cls, elements, max_items: int = 24):
        return tool_rendering_helpers._compress_browser_elements_for_prompt(
            cls,
            elements,
            max_items=max_items,
        )

    @classmethod
    def _compress_tool_result_payload(cls, tool_name: str, tool_result):
        return tool_rendering_helpers._compress_tool_result_payload(cls, tool_name, tool_result)

    # ── 操作经验存储 ─────────────────────────────────────────────

    def _save_learned_procedure(self, procedure_text: str, *, previous_id: str = ""):
        """将 Agent 总结的操作经验存入 learned_procedures。

        - 如果提供了 previous_id（来自 searchLearnedProcedures），直接更新该条目。
        - 否则用 embedding 查找是否已有相似经验，有则更新，无则新增。
        """
        normalized_text = str(procedure_text or "").strip()
        if not normalized_text:
            return
        procedures = list(self._context_memory_state.get("learned_procedures") or [])

        # 优先按 previous_id 精确匹配
        target_index = -1
        if previous_id:
            target_index = next(
                (i for i, item in enumerate(procedures)
                 if isinstance(item, dict) and item.get("id") == previous_id),
                -1,
            )
        # 无精确匹配时，用 embedding 模糊查重
        if target_index < 0 and self.local_embedding_model is not None and procedures:
            target_index = self._find_similar_procedure_index(procedures, normalized_text)

        if target_index >= 0:
            old_item = procedures[target_index]
            old_id = old_item.get("id", "") if isinstance(old_item, dict) else ""
            new_item = _build_core_memory_item(
                "learned_procedures",
                normalized_text,
                item_id=old_id or None,
                created_at=(old_item.get("created_at") if isinstance(old_item, dict) else None),
            )
            procedures[target_index] = new_item
            logger.info(
                "Updated learned procedure | id=%s | new=%s",
                old_id, normalized_text[:80],
            )
        else:
            new_item = _build_core_memory_item("learned_procedures", normalized_text)
            procedures.append(new_item)
            logger.info("Added learned procedure | text=%s", normalized_text[:80])

        self._context_memory_state["learned_procedures"] = procedures
        self._context_memory_state = normalize_core_memory_state(self._context_memory_state)
        self._persist_context_memory_state()

    def _find_similar_procedure_index(self, procedures: list, text: str, threshold: float = 0.6) -> int:
        """用 embedding 相似度查找与 text 最接近的已有经验，返回索引或 -1。"""
        try:
            import numpy as np
            # 取 → 前面的触发场景做匹配
            query_trigger = text.split("→")[0].strip() if "→" in text else text
            query_vector = self.local_embedding_model.encode(query_trigger)
            existing_triggers = []
            for item in procedures:
                t = item.get("text", "") if isinstance(item, dict) else str(item or "")
                existing_triggers.append(t.split("→")[0].strip() if "→" in t else t)
            if not existing_triggers:
                return -1
            existing_vectors = self.local_embedding_model.encode(existing_triggers)
            query_norm = query_vector / (np.linalg.norm(query_vector) + 1e-9)
            existing_norms = existing_vectors / (
                np.linalg.norm(existing_vectors, axis=1, keepdims=True) + 1e-9
            )
            similarities = existing_norms @ query_norm
            best_idx = int(np.argmax(similarities))
            if similarities[best_idx] >= threshold:
                return best_idx
        except Exception:
            logger.exception("Failed to find similar procedure")
        return -1

    # ── Skill 自进化 ────────────────────────────────────────────

    def _get_skill_evolution_config(self) -> dict:
        configured = dict(self.config.get("SkillEvolution", {}))
        merged = {**DEFAULT_SKILL_EVOLUTION_CONFIG, **configured}
        merged["enabled"] = bool(merged.get("enabled", True))
        try:
            merged["min_tool_calls"] = max(2, int(merged.get("min_tool_calls", 3)))
        except (TypeError, ValueError):
            merged["min_tool_calls"] = 3
        try:
            merged["similarity_threshold"] = float(merged.get("similarity_threshold", 0.70))
        except (TypeError, ValueError):
            merged["similarity_threshold"] = 0.70
        return merged

    def _try_auto_evolve_skill(self, procedure_summary: str):
        evolution_config = self._get_skill_evolution_config()
        if not evolution_config["enabled"]:
            return
        tool_trace = getattr(self, "_current_turn_tool_trace", None) or []
        successful_calls = [
            t for t in tool_trace
            if t.get("tool_name") and t.get("tool_name") != SUMMARY_TOOL_NAME
        ]
        if len(successful_calls) < evolution_config["min_tool_calls"]:
            return
        existing_skill = self._find_matching_skill_for_procedure(
            procedure_summary,
            threshold=evolution_config["similarity_threshold"],
        )
        if existing_skill:
            self._auto_update_existing_skill(existing_skill, procedure_summary, tool_trace)
        else:
            self._auto_create_skill_from_procedure(procedure_summary, tool_trace, evolution_config)

    def _find_matching_skill_for_procedure(self, procedure_text: str, *, threshold: float = 0.70):
        if self.local_embedding_model is None:
            return None
        try:
            import numpy as np
            skills = load_skill_definitions()
            if not skills:
                return None
            trigger = procedure_text.split("→")[0].strip() if "→" in procedure_text else procedure_text
            query_vec = self.local_embedding_model.encode(trigger)
            descriptions = [s.get("description", "") or s.get("name", "") for s in skills]
            desc_vecs = self.local_embedding_model.encode(descriptions)
            query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-9)
            desc_norms = desc_vecs / (np.linalg.norm(desc_vecs, axis=1, keepdims=True) + 1e-9)
            similarities = desc_norms @ query_norm
            best_idx = int(np.argmax(similarities))
            if similarities[best_idx] >= threshold:
                return skills[best_idx]
        except Exception:
            logger.exception("Failed to match skill for procedure")
        return None

    def _auto_update_existing_skill(self, skill: dict, procedure_summary: str, tool_trace: list):
        skill_name = skill.get("name", "")
        old_instructions = skill.get("instructions", "")
        shortcut_section = f"\n\n## Learned Shortcut\n{procedure_summary}"
        if "## Learned Shortcut" in old_instructions:
            updated_instructions = re.sub(
                r"## Learned Shortcut\n.*?(?=\n## |\Z)",
                f"## Learned Shortcut\n{procedure_summary}\n",
                old_instructions,
                flags=re.DOTALL,
            )
        else:
            updated_instructions = old_instructions + shortcut_section
        try:
            create_or_update_skill(
                skill_name=skill_name,
                description=skill.get("description", ""),
                when_to_use=skill.get("when_to_use"),
                intent_examples=skill.get("intent_examples"),
                skill_instructions=updated_instructions,
            )
            self._refresh_runtime_tools()
            logger.info(
                "Auto-updated skill with new shortcut | skill=%s | procedure=%s",
                skill_name, procedure_summary[:80],
            )
        except Exception:
            logger.exception("Failed to auto-update skill | skill=%s", skill_name)

    def _auto_create_skill_from_procedure(self, procedure_summary: str, tool_trace: list, evolution_config: dict):
        model_key = str(evolution_config.get("model") or "").strip()
        if not model_key:
            model_key = self._get_model_select_task_config("SkillEvolutionEval", fallback_task_names=["Agent"]).get("model") or self.model
        trigger = procedure_summary.split("→")[0].strip() if "→" in procedure_summary else procedure_summary
        tool_names_used = list(OrderedDict.fromkeys(
            t["tool_name"] for t in tool_trace if t.get("tool_name") and t["tool_name"] != SUMMARY_TOOL_NAME
        ))
        evaluation_prompt = render_prompt_text(
            "SkillEvolutionEval",
            {
                "PROCEDURE_SUMMARY": procedure_summary,
                "TOOL_NAMES": ", ".join(tool_names_used),
                "TOOL_CALL_COUNT": str(len(tool_trace)),
            },
        )
        eval_task_config = self._get_model_select_task_config("SkillEvolutionEval", fallback_task_names=["Agent"])
        try:
            raw_result = call_LLM(
                [{"role": "system", "content": evaluation_prompt}],
                model_key,
                self.session,
                eval_task_config.get("thinking", False),
                True,
                caller="skill_evolution_eval",
                reasoning_effort=eval_task_config.get("reasoning_effort", "low"),
            )
            parsed = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
            if not isinstance(parsed, dict) or not parsed.get("should_create"):
                logger.info("Skill evolution skipped | reason=llm_rejected | procedure=%s", trigger[:60])
                return
            skill_name = str(parsed.get("skill_name") or "").strip()
            if not skill_name:
                skill_name = self._derive_skill_name_from_procedure(procedure_summary)
            description = str(parsed.get("description") or "").strip()
            if not description:
                description = f"Reusable shortcut for: {trigger}"
        except Exception:
            logger.exception("Skill evolution LLM eval failed, falling back to heuristic")
            skill_name = self._derive_skill_name_from_procedure(procedure_summary)
            description = f"Reusable shortcut for: {trigger}"

        instructions = "\n".join([
            f"# {skill_name.replace('-', ' ').title()}",
            "",
            "## Purpose",
            description,
            "",
            "## Learned Shortcut",
            procedure_summary,
            "",
            "## Operating Rules",
            "- Treat this as a reusable shortcut, not as proof that the task is already complete.",
            "- Reuse concrete URLs, command arguments, and tool parameters exactly when they still match the request.",
            "- If the page, app, or API behavior has changed, execute the tools again and update the shortcut after the task.",
        ])
        try:
            create_or_update_skill(
                skill_name=skill_name,
                description=description,
                when_to_use=[trigger],
                intent_examples=[trigger],
                tool_definitions=[],
                enabled=True,
                skill_instructions=instructions,
                metadata={"source": "auto-evolved", "version": "1"},
            )
            self._refresh_runtime_tools()
            logger.info(
                "Auto-created skill from procedure | skill=%s | procedure=%s",
                skill_name, procedure_summary[:80],
            )
        except Exception:
            logger.exception("Failed to auto-create skill | name=%s", skill_name)

    # ── /Skill 自进化 ───────────────────────────────────────────

    def _build_tool_summary_from_trace(self, tool_trace: list, *, extra_notice: str = ""):
        summary_lines = []
        if tool_trace:
            summary_lines.append("工具调用结果摘要:")
            for index, trace_item in enumerate(tool_trace, start=1):
                title = trace_item["tool_name"]
                if trace_item["skill_name"]:
                    title = f"{title} | skill={trace_item['skill_name']}"
                args_excerpt = str(trace_item.get("args_excerpt", "")).strip()
                if args_excerpt:
                    summary_lines.append(f"{index}. {title}({args_excerpt}): {trace_item['result_excerpt']}")
                else:
                    summary_lines.append(f"{index}. {title}: {trace_item['result_excerpt']}")
        else:
            summary_lines.append("本轮没有可复用的工具结果，需要直接基于当前上下文完成回复。")
        normalized_notice = str(extra_notice or "").strip()
        if normalized_notice:
            summary_lines.append(normalized_notice)
        return "\n".join(summary_lines)

    def _force_tool_summary_from_trace(
        self,
        tool_trace: list,
        *,
        input_num: int,
        reached_max_tool_calls: bool = False,
        extra_notice: str = "",
    ):
        return self.summarizeToolResults(
            SummaryTools=self._build_tool_summary_from_trace(tool_trace, extra_notice=extra_notice),
            ReachedMaxToolCalls=reached_max_tool_calls,
            InputNum=input_num,
        )

    def _should_persist_summary_tools(self, summary_tools: str, *, reached_max_tool_calls: bool = False) -> bool:
        if reached_max_tool_calls:
            return False
        normalized_summary = str(summary_tools or "").strip()
        if not normalized_summary:
            return False
        tool_trace = list(self._current_turn_tool_trace or [])
        if any(
            str((item or {}).get("tool_name", "") or "").strip() in BROWSER_TOOL_NAMES
            for item in tool_trace
            if isinstance(item, dict)
        ):
            return False
        return True

    def summarizeToolResults(
        self,
        SummaryTools: str = "",
        ReachedMaxToolCalls: bool = False,
        InputNum: int = 0,
        ProcedureSummary: str = "",
        PreviousProcedureId: str = "",
    ):
        """生成最终回复，并按需要注入持久系统上下文。"""
        self.agent_cycle = False
        normalized_procedure = str(ProcedureSummary or "").strip()
        normalized_prev_id = str(PreviousProcedureId or "").strip()
        if normalized_procedure and not ReachedMaxToolCalls:
            self._save_learned_procedure(normalized_procedure, previous_id=normalized_prev_id)
            self._try_auto_evolve_skill(normalized_procedure)
        reply_task_name = ROLE_PLAY_TASK_NAME
        reply_task_config = self._get_model_select_task_config(reply_task_name)
        thinking = reply_task_config.get("thinking", True)
        normalized_summary_tools = str(SummaryTools or "").strip()
        self._backfill_retrieval_cache_summary(normalized_summary_tools)
        request_system_contents = []
        if normalized_summary_tools:
            request_system_contents.append(normalized_summary_tools)
        if ReachedMaxToolCalls:
            request_system_contents.append(
                "补充说明：Agent 已达到最大工具调用次数限制，必须基于现有工具结果直接完成回复，并明确说明这一点。"
            )
        request_message = self._build_messages_with_persistent_contexts(
            self.role_play_message,
            request_system_contents
        )
        if normalized_summary_tools:
            logger.info("SummaryTools: %s", normalized_summary_tools)
        model = reply_task_config.get("model") or "kimi"
        logger.info(
            "Final reply | task=%s | reached_max_tool_calls=%s | model=%s | thinking=%s",
            reply_task_name,
            ReachedMaxToolCalls,
            get_LLM(model)["modelName"],
            thinking
        )
        with self._temporary_due_reminder_contexts(request_message) as due_tasks:
            result = call_LLM(
                request_message,
                model,
                self.session,
                thinking,
                reply_task_config.get("json_mode", False),
                caller=reply_task_name,
                reasoning_effort=reply_task_config.get("reasoning_effort", "high"),
            )
        logger.info("Final reply result: %s", result)
        if self._should_persist_summary_tools(
            normalized_summary_tools,
            reached_max_tool_calls=ReachedMaxToolCalls,
        ):
            self._add_persistent_system_context(
                context_type="summary_tools",
                content=normalized_summary_tools
            )
        self._acknowledge_due_reminders(due_tasks)
        self.appendUserMessageSync(result, "assistant")

        return {"Finish": not self.agent_cycle}

    def literaryCreation(
        self,
        CreativePrompt: str = "",
        Genre: str = "other",
        **kwargs,
    ):
        """文学创作专用工具：注入完整 RolePlay 人设并直接生成最终创作内容。"""
        alias_prompt = (
            kwargs.get("prompt")
            or kwargs.get("Prompt")
            or kwargs.get("creative_prompt")
            or kwargs.get("creativePrompt")
            or ""
        )
        creative_prompt = str(CreativePrompt or alias_prompt or "").strip()
        if not creative_prompt:
            return {"ok": False, "error": "CreativePrompt is required."}

        alias_genre = kwargs.get("genre") or kwargs.get("Genre") or "other"
        genre = str(Genre or alias_genre or "other").strip().lower() or "other"
        if alias_prompt and not CreativePrompt:
            logger.info(
                "Literary creation prompt alias accepted | alias_keys=%s",
                sorted(str(key) for key in kwargs.keys()),
            )
        creation_task_config = self._get_model_select_task_config(
            "LiteraryCreation",
            fallback_task_names=(ROLE_PLAY_TASK_NAME,),
        )
        if not creation_task_config.get("enabled", True):
            return {"ok": False, "error": "LiteraryCreation task is disabled."}

        model = creation_task_config.get("model") or self._get_model_select_model_key(ROLE_PLAY_TASK_NAME) or "kimi"
        try:
            model_name = get_LLM(model)["modelName"]
        except Exception:
            model_name = str(model or "").strip() or "unknown"

        char_replacements = get_character_replacements(self.config)
        char_name = char_replacements.get("CHAR_NAME") or "Assistant"

        creation_system = "\n".join(
            [
                "[文学创作任务]",
                f"体裁：{genre}",
                f"创作要求：{creative_prompt}",
                "你现在要直接完成这次创作，不要解释，不要分析，不要输出元评论。",
                f"请保留{char_name}的人设、情感气质、文字审美与表达张力。",
                "如果用户给了明确格式、篇幅或结构要求，严格遵守；如果没有，就按最自然、完整的文学表达完成。",
            ]
        ).strip()
        request_message = self._build_messages_with_persistent_contexts(self.role_play_message)
        request_message = self._insert_system_messages_after_leading_systems(
            request_message,
            [{"role": "system", "content": creation_system}],
        )
        request_message.append(
            {
                "role": "user",
                "content": f"请直接完成这次{genre}创作。\n创作要求：{creative_prompt}",
            }
        )
        logger.info(
            "Literary creation started | genre=%s | model=%s | prompt_length=%s",
            genre,
            model_name,
            len(creative_prompt),
        )
        try:
            result = call_LLM(
                request_message,
                model,
                self.session,
                creation_task_config.get("thinking", True),
                creation_task_config.get("json_mode", False),
                caller="literary_creation",
                reasoning_effort=creation_task_config.get("reasoning_effort", "high"),
            )
        except Exception as exc:
            logger.exception("Literary creation failed")
            return {"ok": False, "error": f"Literary creation failed: {exc}"}

        normalized_result = str(result or "").strip()
        if not normalized_result:
            return {"ok": False, "error": "Literary creation returned empty content."}

        self.agent_cycle = False
        return {
            "ok": True,
            "Finish": True,
            "direct_reply": True,
            "genre": genre,
            "content": normalized_result,
            "content_preview": self._truncate_context_text(normalized_result, max_chars=240),
            "content_length": len(normalized_result),
        }

    def controlSelf(self, Result: str):
        """处理 Agent 对自身运行状态的控制指令。"""
        if Result == "Shutdown":
            self.chat_cycle = False
        return {"Result": Result}

    # ── askUser + Agent 暂停/恢复/中断 ──────────────────────────

    ASK_USER_TOOL_NAME = "askUser"

    def askUser(self, Question: str, Options: list = None, Context: str = ""):
        """Agent 主动向用户提问，暂停循环等待回复。

        实际的暂停逻辑不在这里——这个方法只返回一个标记，
        由 run_agent 循环检测到后执行暂停。
        """
        return {
            "ok": True,
            "action": "ask_user",
            "question": str(Question or "").strip(),
            "options": [str(o).strip() for o in (Options or []) if str(o).strip()],
            "context": str(Context or "").strip(),
        }

    def _suspend_agent_state(
        self,
        *,
        tmp_agent_message: list,
        tool_trace: list,
        executed_tool_calls: int,
        model: str,
        runtime_settings: dict,
        ask_user_payload: dict = None,
        ask_user_tool_call: dict = None,
        suspension_type: str = "ask_user",
        pending_tool_call: dict = None,
        pending_function_name: str = "",
        pending_function_args: dict = None,
        pending_policy_result: dict = None,
        pending_approval_id: str = "",
        approval_question: str = "",
    ):
        """保存 Agent 循环的完整状态，以便用户回复后恢复。"""
        self._suspended_agent_state = {
            "suspension_type": suspension_type,
            "tmp_agent_message": [dict(m) for m in tmp_agent_message],
            "tool_trace": list(tool_trace),
            "executed_tool_calls": executed_tool_calls,
            "model": model,
            "runtime_settings_model_key": runtime_settings.get("model_key", model),
            "turn_loaded_deferred_tool_names": sorted(self._turn_loaded_deferred_tool_names or set()),
            "turn_active_tools_snapshot": [dict(tool) for tool in (self._turn_active_tools_snapshot or [])],
            "current_turn_agent_request": dict(getattr(self, "_current_turn_agent_request", {}) or {}),
            "current_turn_browser_observations": [
                dict(item)
                for item in (self._current_turn_browser_observations or [])
                if isinstance(item, dict)
            ],
            "current_turn_browser_visual_artifacts": [
                dict(item)
                for item in (self._current_turn_browser_visual_artifacts or [])
                if isinstance(item, dict)
            ],
            "suspended_at": time.time(),
        }
        if suspension_type == "tool_approval":
            self._suspended_agent_state.update({
                "pending_tool_call": dict(pending_tool_call or {}),
                "pending_function_name": str(pending_function_name or "").strip(),
                "pending_function_args": dict(pending_function_args or {}),
                "pending_policy_result": dict(pending_policy_result or {}),
                "pending_approval_id": str(pending_approval_id or "").strip(),
                "approval_question": str(approval_question or "").strip(),
            })
            logger.info(
                "Agent suspended for tool approval | tool=%s | tool_call_id=%s",
                pending_function_name,
                (pending_tool_call or {}).get("id", ""),
            )
        else:
            self._suspended_agent_state.update({
                "ask_user_payload": dict(ask_user_payload or {}),
                "ask_user_tool_call_id": (ask_user_tool_call or {}).get("id", ""),
                "ask_user_tool_name": self.ASK_USER_TOOL_NAME,
            })
            logger.info(
                "Agent suspended for askUser | question=%s | tool_call_id=%s",
                str((ask_user_payload or {}).get("question", ""))[:80],
                (ask_user_tool_call or {}).get("id", ""),
            )

    def _get_suspended_question(self) -> str:
        state = self._suspended_agent_state
        if not state:
            return ""
        if state.get("suspension_type") == "tool_approval":
            return str(state.get("clarification_question") or state.get("approval_question", "")).strip()
        return str((state.get("ask_user_payload") or {}).get("question", "")).strip()

    def _restore_runtime_from_suspended_state(self, state: dict):
        if not isinstance(state, dict):
            return
        self._turn_loaded_deferred_tool_names = {
            str(name or "").strip()
            for name in (state.get("turn_loaded_deferred_tool_names") or [])
            if str(name or "").strip()
        }
        self._turn_active_tools_snapshot = [
            dict(tool_definition)
            for tool_definition in (state.get("turn_active_tools_snapshot") or [])
            if isinstance(tool_definition, dict)
        ]
        self._current_turn_browser_observations = [
            dict(item)
            for item in (state.get("current_turn_browser_observations") or [])
            if isinstance(item, dict)
        ]
        self._current_turn_browser_visual_artifacts = [
            dict(item)
            for item in (state.get("current_turn_browser_visual_artifacts") or [])
            if isinstance(item, dict)
        ]
        if isinstance(state.get("current_turn_agent_request"), dict):
            self._current_turn_agent_request = dict(state.get("current_turn_agent_request") or {})

    def _resume_agent_from_suspension(self, user_reply: str):
        """用用户的回复恢复被暂停的 Agent 循环。"""
        state = self._suspended_agent_state
        if state is None:
            return
        self._suspended_agent_state = None
        self._restore_runtime_from_suspended_state(state)

        suspension_type = str(state.get("suspension_type", "ask_user")).strip()

        if suspension_type == "tool_approval":
            return self._resume_from_tool_approval(state, user_reply)

        # askUser 类型的恢复
        tmp_agent_message = state["tmp_agent_message"]
        tmp_agent_message.append({
            "role": "tool",
            "tool_call_id": state["ask_user_tool_call_id"],
            "name": state["ask_user_tool_name"],
            "content": json.dumps({
                "ok": True,
                "user_reply": str(user_reply or "").strip(),
            }, ensure_ascii=False),
        })

        logger.info(
            "Agent resuming from suspension | user_reply=%s | tool_call_id=%s",
            str(user_reply or "")[:80],
            state["ask_user_tool_call_id"],
        )

        self._resume_agent_loop(
            tmp_agent_message=tmp_agent_message,
            tool_trace=state["tool_trace"],
            executed_tool_calls=state["executed_tool_calls"],
            model=state["model"],
        )

    @staticmethod
    def _infer_tool_approval_decision_from_reply(user_reply: str) -> str:
        raw_reply = str(user_reply or "").strip()
        if not raw_reply:
            return ""
        normalized = re.sub(r"[\s，。！？、,.!?]+", "", raw_reply.lower())
        reject_exact_markers = {
            "不",
            "否",
            "no",
            "n",
            "deny",
            "reject",
            "cancel",
            "stop",
        }
        reject_markers = (
            "不允许",
            "不批准",
            "不同意",
            "不可以",
            "不能",
            "不用",
            "不要",
            "不需要",
            "不必",
            "不执行",
            "不调用",
            "不要执行",
            "不要调用",
            "别执行",
            "别调用",
            "别动",
            "先别",
            "先不要",
            "暂时不要",
            "取消",
            "拒绝",
            "不行",
            "算了",
            "停",
            "donot",
            "don't",
        )
        if normalized in reject_exact_markers or any(marker in normalized for marker in reject_markers):
            return "rejected"

        approve_exact_markers = {
            "执行",
            "执行吧",
            "执行把",
            "允许",
            "批准",
            "同意",
            "确认",
            "继续",
            "可以",
            "好的",
            "好",
            "行",
            "嗯",
            "yes",
            "y",
            "ok",
            "okay",
            "approve",
            "allow",
            "proceed",
            "run",
        }
        approve_markers = (
            "执行",
            "允许",
            "批准",
            "同意",
            "确认",
            "继续",
            "可以",
            "去做",
            "开干",
            "approve",
            "allow",
            "proceed",
            "goahead",
            "runit",
        )
        if normalized in approve_exact_markers or any(marker in normalized for marker in approve_markers):
            return "approved"
        return ""

    def _build_tool_approval_clarification_question(self, state: dict) -> str:
        function_name = str(state.get("pending_function_name", "") or "").strip() or "这个工具"
        return f"我还不能确定你是否允许调用 {function_name}。请直接告诉我：允许执行，还是拒绝执行？"

    @staticmethod
    def _is_pending_tool_approval_message(message: dict, tool_call_id: str) -> bool:
        if str((message or {}).get("role", "") or "").strip().lower() != "tool":
            return False
        if str((message or {}).get("tool_call_id", "") or "").strip() != str(tool_call_id or "").strip():
            return False
        try:
            payload = json.loads(str((message or {}).get("content", "") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return bool(isinstance(payload, dict) and payload.get("approval_pending"))

    def _replace_pending_tool_approval_result(
        self,
        tmp_agent_message: list,
        *,
        pending_tool_call: dict,
        function_name: str,
        tool_result,
        approval_question: str = "",
    ) -> list:
        tool_call_id = str((pending_tool_call or {}).get("id", "") or "").strip()
        compressed_tool_result = self._compress_tool_result_payload(function_name, tool_result)
        resolved_tool_message = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": function_name or ((pending_tool_call or {}).get("function") or {}).get("name", ""),
            "content": json.dumps(compressed_tool_result, ensure_ascii=False),
        }
        normalized_question = str(approval_question or "").strip()
        replaced = False
        rebuilt_messages = []
        for message in list(tmp_agent_message or []):
            if self._is_pending_tool_approval_message(message, tool_call_id):
                if not replaced:
                    rebuilt_messages.append(resolved_tool_message)
                    replaced = True
                continue
            if (
                normalized_question
                and str((message or {}).get("role", "") or "").strip().lower() == "assistant"
                and not (message or {}).get("tool_calls")
                and str((message or {}).get("content", "") or "").strip() == normalized_question
            ):
                continue
            rebuilt_messages.append(dict(message))

        if replaced:
            return rebuilt_messages

        insert_index = len(rebuilt_messages)
        for index, message in enumerate(rebuilt_messages):
            if str(message.get("role", "") or "").strip().lower() != "assistant":
                continue
            for tool_call in message.get("tool_calls") or []:
                if str((tool_call or {}).get("id", "") or "").strip() == tool_call_id:
                    insert_index = index + 1
                    break
            if insert_index == index + 1:
                break
        rebuilt_messages.insert(insert_index, resolved_tool_message)
        return rebuilt_messages

    def _resume_from_tool_approval(self, state: dict, user_reply: str):
        """根据用户回复解决审批，并把结果接回原工具调用后继续 Agent。"""
        decision = self._infer_tool_approval_decision_from_reply(user_reply)
        if not decision:
            clarification_question = self._build_tool_approval_clarification_question(state)
            state["clarification_question"] = clarification_question
            self._suspended_agent_state = state
            self.appendUserMessageSync(clarification_question, "assistant")
            logger.info(
                "Tool approval reply ambiguous; asking clarification | tool=%s | reply=%s",
                str(state.get("pending_function_name", "") or "").strip(),
                str(user_reply or "")[:80],
            )
            return

        approval_id = str(state.get("pending_approval_id", "") or "").strip()
        function_name = str(state.get("pending_function_name", "") or "").strip()
        resolution_result = self.resolveToolApproval(
            ApprovalId=approval_id,
            Decision=decision,
        )
        if decision == "approved":
            resolved_tool_result = resolution_result.get("tool_result", {}) if isinstance(resolution_result, dict) else {}
        else:
            resolved_tool_result = {
                "ok": False,
                "approval_rejected": True,
                "error": f"用户拒绝授权调用 {function_name or '该工具'}。",
            }
        if isinstance(resolution_result, dict) and not resolution_result.get("ok", False):
            resolved_tool_result = {
                "ok": False,
                "error": str(resolution_result.get("error") or "工具授权处理失败。"),
                "approval_resolution": resolution_result,
            }

        tmp_agent_message = self._replace_pending_tool_approval_result(
            state["tmp_agent_message"],
            pending_tool_call=state.get("pending_tool_call") or {},
            function_name=function_name,
            tool_result=resolved_tool_result,
            approval_question=state.get("approval_question", ""),
        )
        tool_trace = list(state["tool_trace"])
        executed_tool_calls = int(state.get("executed_tool_calls") or 0)
        if decision == "approved" and function_name != SUMMARY_TOOL_NAME:
            self._store_retrieval_cache_entry(
                function_name,
                state.get("pending_function_args") or {},
                resolved_tool_result,
            )
            executed_tool_calls += 1
            self._record_browser_observation(
                function_name,
                resolved_tool_result,
                state.get("pending_function_args") or {},
            )
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

        self._append_visible_agent_step(
            step=len(self._current_turn_visible_agent_steps) + 1,
            phase="agent",
            title="工具审批已处理",
            detail=(
                f"{function_name} 已获批并完成执行。"
                if decision == "approved"
                else f"{function_name} 的工具调用已被拒绝。"
            ),
            status="completed",
        )

        logger.info(
            "Tool approval resolved and original tool result attached | tool=%s | decision=%s | reply=%s",
            function_name,
            decision,
            str(user_reply or "")[:80],
        )

        self._resume_agent_loop(
            tmp_agent_message=tmp_agent_message,
            tool_trace=tool_trace,
            executed_tool_calls=executed_tool_calls,
            model=state["model"],
        )

    def _resume_agent_loop(
        self,
        *,
        tmp_agent_message: list,
        tool_trace: list,
        executed_tool_calls: int,
        model: str,
    ):
        """从保存的状态恢复 Agent 循环，继续执行直到完成。"""
        self.agent_cycle = True
        self._set_active_tool_session_context(self._build_tool_session_context())
        steps = 0
        previous_tool_name = ""
        consecutive_tool_calls = 0
        agent_runtime_config = self._get_agent_runtime_config(browser_mode=self._turn_looks_like_browser_task())
        max_tool_calls = agent_runtime_config["max_tool_calls"]
        max_consecutive_same_calls = agent_runtime_config["max_consecutive_same_tool_calls"]
        runtime_settings = self._resolve_agent_runtime(model)
        current_llm = runtime_settings["llm"]
        self._turn_active_tools_snapshot = list(runtime_settings["tools"])
        thinking_enabled = runtime_settings["thinking_enabled"]

        # 复用 run_agent 的主循环逻辑
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

    def submit_agent_interrupt(self, message: str):
        """外部（前端 API）提交一条中断消息给正在运行的 Agent。

        Agent 循环在每次 LLM 调用前会检查中断队列。
        """
        normalized = str(message or "").strip()
        if normalized:
            self._agent_interrupt_queue.put(normalized)
            logger.info("Agent interrupt submitted | message=%s", normalized[:80])

    def _check_agent_interrupt(self, tmp_agent_message: list) -> bool:
        """检查中断队列，如果有用户消息则注入到上下文中。返回是否发生了中断。"""
        interrupted = False
        while not self._agent_interrupt_queue.empty():
            try:
                interrupt_message = self._agent_interrupt_queue.get_nowait()
            except queue.Empty:
                break
            if str(interrupt_message or "").strip():
                tmp_agent_message.append({
                    "role": "user",
                    "content": str(interrupt_message).strip(),
                })
                logger.info("Agent interrupt injected | message=%s", str(interrupt_message)[:80])
                interrupted = True
        return interrupted

    # ── /askUser + Agent 暂停/恢复/中断 ─────────────────────────

    # ── 子 Agent 完成回调 ─────────────────────────────────────

    def _on_subagent_task_complete(self, task_id: str, task_record: dict):
        """子 Agent 完成后的回调，由 SubAgentRuntime 工作线程调用。"""
        status = str(task_record.get("status", "") or "").strip().lower()
        if status not in SubAgentRuntime.TERMINAL_STATUSES:
            return
        self._pending_subagent_results.put({
            "task_id": task_id,
            "task": str(task_record.get("task", "") or "").strip(),
            "agent_type": str(task_record.get("agent_type", "") or "").strip(),
            "status": status,
            "result": str(task_record.get("result", "") or "").strip(),
            "error": str(task_record.get("error", "") or "").strip(),
        })
        self._trigger_agent_for_subagent_results()

    def _trigger_agent_for_subagent_results(self):
        """从 pending 队列消费所有子 Agent 结果，注入到 agent_message 后触发主 Agent 执行。"""
        if not self._run_agent_lock.acquire(blocking=False):
            return
        try:
            results = []
            while not self._pending_subagent_results.empty():
                try:
                    results.append(self._pending_subagent_results.get_nowait())
                except queue.Empty:
                    break
            if not results:
                return
            for item in results:
                content = self._format_subagent_result_message(item)
                self.agent_message.append({
                    "role": "system",
                    "content": content,
                })
            self._set_current_turn_agent_request()
            self._reset_visible_agent_steps()
            self._append_visible_agent_step(
                step=1,
                phase="agent",
                title="收到后台子 Agent 结果",
                detail=f"共 {len(results)} 个子任务完成，主 Agent 继续处理。",
                status="running",
            )
            self.run_agent(model=self.model)
        except Exception:
            logger.exception("Failed to trigger agent for sub-agent results")
        finally:
            self._run_agent_lock.release()

    @staticmethod
    def _format_subagent_result_message(item: dict) -> str:
        task_id = item.get("task_id", "")
        task_desc = item.get("task", "")
        agent_type = item.get("agent_type", "")
        status = item.get("status", "")
        result_text = item.get("result", "")
        error_text = item.get("error", "")
        parts = [f"[子Agent任务完成通知] task_id={task_id}, agent_type={agent_type}, status={status}"]
        if task_desc:
            parts.append(f"任务: {task_desc}")
        if status == "completed" and result_text:
            parts.append(f"结果: {result_text}")
        elif error_text:
            parts.append(f"错误: {error_text}")
        return "\n".join(parts)

    def delegateTask(
        self,
        Task: str,
        AgentType: str = "general",
        Context: dict = None,
        Model: str = "",
        MaxToolCalls: int = None,
        TimeoutSeconds: float = 60.0,
        RunInBackground: bool = True,
        Priority=None,
        UseCache: bool = True,
    ):
        result = self.subagent_runtime.spawn(
            task=Task,
            agent_type=AgentType,
            task_context=Context,
            model=Model,
            max_tool_calls=MaxToolCalls,
            timeout_seconds=TimeoutSeconds,
            run_in_background=RunInBackground,
            priority=Priority,
            use_cache=UseCache,
        )
        if RunInBackground and result.get("ok"):
            task_record = result.get("task") or {}
            task_id = str(task_record.get("task_id", "") or "").strip()
            if task_id and task_record.get("status") not in SubAgentRuntime.TERMINAL_STATUSES:
                self.subagent_runtime.on_task_complete(
                    task_id, self._on_subagent_task_complete
                )
        return result

    def continueDelegatedTask(
        self,
        TaskId: str,
        UserReply: str = "",
        ApprovalDecision: str = "",
    ):
        return self.subagent_runtime.resume_task(
            TaskId,
            user_reply=UserReply,
            approval_decision=ApprovalDecision,
        )

    def cancelDelegatedTask(
        self,
        TaskId: str,
        Reason: str = "",
    ):
        return self.subagent_runtime.cancel_task(
            TaskId,
            reason=Reason,
        )

    def getDelegatedTaskStatus(
        self,
        TaskId: str,
        WaitForCompletion: bool = False,
        TimeoutSeconds: float = 20.0,
        PollIntervalSeconds: float = 0.5,
    ):
        if WaitForCompletion:
            return self.subagent_runtime.wait_for_task(
                TaskId,
                timeout_seconds=TimeoutSeconds,
                poll_interval_seconds=PollIntervalSeconds,
            )
        return self.subagent_runtime.get_status(TaskId)

    def listDelegatedTasks(self, IncludeCompleted: bool = True):
        return self.subagent_runtime.list_tasks(include_completed=IncludeCompleted)

    def delegateTasksParallel(
        self,
        Tasks: list,
        GroupLabel: str = "",
        WaitForCompletion: bool = False,
        TimeoutSeconds: float = 20.0,
        PollIntervalSeconds: float = 0.5,
    ):
        result = self.subagent_runtime.spawn_parallel(
            task_specs=list(Tasks or []),
            group_label=GroupLabel,
            wait_for_completion=bool(WaitForCompletion),
            timeout_seconds=TimeoutSeconds,
            poll_interval_seconds=PollIntervalSeconds,
        )
        if not WaitForCompletion and result.get("ok"):
            for task_record in (result.get("tasks") or []):
                task_id = str(task_record.get("task_id", "") or "").strip()
                if task_id and task_record.get("status") not in SubAgentRuntime.TERMINAL_STATUSES:
                    self.subagent_runtime.on_task_complete(
                        task_id, self._on_subagent_task_complete
                    )
        return result

    def waitForDelegatedTasks(
        self,
        TaskIds: list,
        TimeoutSeconds: float = 20.0,
        PollIntervalSeconds: float = 0.5,
    ):
        return self.subagent_runtime.wait_for_tasks(
            list(TaskIds or []),
            timeout_seconds=TimeoutSeconds,
            poll_interval_seconds=PollIntervalSeconds,
        )

    def getSelfLog(self):
        """读取最近日志，供 Agent 自检使用。"""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            for handler in logging.getLogger().handlers:
                if hasattr(handler, "flush"):
                    handler.flush()
            if not os.path.exists(log_path):
                return {"log": "", "current_time": current_time, "error": "log file does not exist."}
            recent_log = read_recent_log_tail(log_path, hours=1, max_lines=40)
            return {"log": recent_log, "current_time": current_time, "error": ""}
        except Exception as e:
            return {"log": "", "current_time": current_time, "error": str(e)}

    def _get_or_create_autonomous_task_log_reader(self):
        if self._autonomous_task_log is not None:
            return self._autonomous_task_log
        if not os.path.exists(AUTONOMOUS_TASK_DB_PATH):
            return None
        try:
            self._autonomous_task_log = AutonomousTaskLog(AUTONOMOUS_TASK_DB_PATH)
        except Exception:
            autonomous_logger.exception(
                "Failed to initialize autonomous task log reader | db_path=%s",
                AUTONOMOUS_TASK_DB_PATH,
            )
            return None
        return self._autonomous_task_log

    @staticmethod
    def _extract_autonomous_task_query_tokens(query: str) -> list[str]:
        normalized_query = str(query or "").strip().lower()
        if not normalized_query:
            return []
        tokens = [
            token
            for token in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9][a-z0-9_-]{1,}", normalized_query)
            if token
        ]
        if not tokens:
            tokens = [normalized_query]
        deduped_tokens = []
        seen_tokens = set()
        for token in tokens:
            if token in seen_tokens:
                continue
            seen_tokens.add(token)
            deduped_tokens.append(token)
        return deduped_tokens

    def _score_autonomous_task_search_result(self, task: dict, query: str) -> int:
        normalized_query = str(query or "").strip().lower()
        if not normalized_query:
            return 0
        task_content = str((task or {}).get("task_content") or "").strip().lower()
        expected_goal = str((task or {}).get("expected_goal") or "").strip().lower()
        execution_log = str((task or {}).get("execution_log") or "").strip().lower()
        haystack = "\n".join(part for part in (task_content, expected_goal, execution_log) if part)
        if not haystack:
            return -1

        score = 0
        if normalized_query in haystack:
            score += 8
        for token in self._extract_autonomous_task_query_tokens(normalized_query):
            if token in task_content:
                score += 4
            if token in expected_goal:
                score += 3
            if token in execution_log:
                score += 2
        return score

    def _serialize_autonomous_task_search_result(self, task: dict, *, preview_chars: int = 280) -> dict:
        execution_log = str((task or {}).get("execution_log") or "").strip()
        return {
            "task_id": int((task or {}).get("id") or 0),
            "task_date": str((task or {}).get("task_date") or "").strip(),
            "status": str((task or {}).get("status") or "").strip(),
            "task_content": str((task or {}).get("task_content") or "").strip(),
            "expected_goal": str((task or {}).get("expected_goal") or "").strip(),
            "attempt_count": int((task or {}).get("attempt_count") or 0),
            "completed_at": str((task or {}).get("completed_at") or "").strip(),
            "execution_excerpt": self._truncate_context_text(execution_log, max_chars=preview_chars),
            "has_execution_log": bool(execution_log),
        }

    def searchAutonomousTaskArtifacts(
        self,
        Query: str = "",
        Limit: int = 5,
        SinceDate: str = "",
        UntilDate: str = "",
        IncludeIncomplete: bool = False,
    ):
        normalized_query = str(Query or "").strip()
        try:
            limit = max(1, min(int(Limit or 5), 20))
        except (TypeError, ValueError):
            limit = 5
        task_log = self._get_or_create_autonomous_task_log_reader()
        if task_log is None:
            return {
                "ok": False,
                "error": "Autonomous task log is unavailable.",
            }

        statuses = None
        if not IncludeIncomplete:
            statuses = ["completed"]
        try:
            tasks = task_log.search_tasks(
                query=normalized_query,
                statuses=statuses,
                limit=max(limit * 4, limit + 4),
                since_date=SinceDate or None,
                until_date=UntilDate or None,
                require_execution_log=True,
            )
        except Exception as error:
            autonomous_logger.exception("searchAutonomousTaskArtifacts failed")
            return {"ok": False, "error": str(error)}

        if normalized_query:
            tasks = sorted(
                tasks,
                key=lambda task: (
                    self._score_autonomous_task_search_result(task, normalized_query),
                    self._coerce_sort_timestamp(task.get("completed_at")),
                    self._coerce_sort_timestamp(task.get("updated_at")),
                    int(task.get("id") or 0),
                ),
                reverse=True,
            )
        selected_tasks = tasks[:limit]
        autonomous_logger.info(
            "searchAutonomousTaskArtifacts | query=%s | count=%s | include_incomplete=%s",
            normalized_query[:80],
            len(selected_tasks),
            bool(IncludeIncomplete),
        )
        return {
            "ok": True,
            "count": len(selected_tasks),
            "results": [
                self._serialize_autonomous_task_search_result(task)
                for task in selected_tasks
            ],
            "note": "Use readAutonomousTaskArtifact(TaskId=...) to read the full original text of a selected task.",
            "filters": {
                "query": normalized_query,
                "since_date": str(SinceDate or "").strip(),
                "until_date": str(UntilDate or "").strip(),
                "include_incomplete": bool(IncludeIncomplete),
            },
        }

    def readAutonomousTaskArtifact(self, TaskId: int, MaxChars: int = 6000):
        try:
            normalized_task_id = int(TaskId)
        except (TypeError, ValueError):
            return {"ok": False, "error": "TaskId must be an integer."}
        if normalized_task_id <= 0:
            return {"ok": False, "error": "TaskId must be > 0."}
        try:
            max_chars = max(200, min(int(MaxChars or 6000), 20000))
        except (TypeError, ValueError):
            max_chars = 6000

        task_log = self._get_or_create_autonomous_task_log_reader()
        if task_log is None:
            return {
                "ok": False,
                "error": "Autonomous task log is unavailable.",
            }

        try:
            task = task_log.get_task(normalized_task_id)
        except Exception as error:
            autonomous_logger.exception("readAutonomousTaskArtifact failed | task_id=%s", normalized_task_id)
            return {"ok": False, "error": str(error)}
        if not task:
            return {"ok": False, "error": f"Task {normalized_task_id} was not found."}

        execution_log = str(task.get("execution_log") or "").strip()
        if not execution_log:
            return {
                "ok": False,
                "error": f"Task {normalized_task_id} does not have a stored execution artifact.",
            }

        truncated = len(execution_log) > max_chars
        autonomous_logger.info(
            "readAutonomousTaskArtifact | task_id=%s | truncated=%s | max_chars=%s",
            normalized_task_id,
            truncated,
            max_chars,
        )
        return {
            "ok": True,
            "task": {
                "task_id": int(task.get("id") or 0),
                "task_date": str(task.get("task_date") or "").strip(),
                "status": str(task.get("status") or "").strip(),
                "task_content": str(task.get("task_content") or "").strip(),
                "expected_goal": str(task.get("expected_goal") or "").strip(),
                "attempt_count": int(task.get("attempt_count") or 0),
                "completed_at": str(task.get("completed_at") or "").strip(),
                "execution_log": execution_log[:max_chars] if truncated else execution_log,
                "truncated": truncated,
                "execution_log_length": len(execution_log),
            },
        }

    @staticmethod
    def _parse_date_param(raw_value, *, end_of_day: bool = False):
        """把外部传入的日期参数统一转成 epoch float。

        接受形式：
        - None / 空字符串 -> 返回 None（该侧无限制）
        - float / int -> 视为已是 epoch（兼容毫秒，>1e12 时自动 /1000）
        - "YYYY-MM-DD" -> 本地 0 点；end_of_day=True 时落到次日 0 点（生成左闭右开区间的右端）
        - ISO 字符串（含 T/空格时间）-> datetime.fromisoformat
        """
        if raw_value is None:
            return None
        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            numeric_value = float(raw_value)
            if numeric_value > 1e12:
                numeric_value /= 1000.0
            return numeric_value
        text = str(raw_value or "").strip()
        if not text:
            return None
        try:
            numeric_value = float(text)
            if numeric_value > 1e12:
                numeric_value /= 1000.0
            return numeric_value
        except (TypeError, ValueError):
            pass
        normalized_text = text.replace("Z", "+00:00").replace("/", "-")
        if "T" not in normalized_text and " " in normalized_text:
            normalized_text = normalized_text.replace(" ", "T", 1)
        try:
            parsed = datetime.fromisoformat(normalized_text)
            if end_of_day and len(text) == 10:
                parsed = parsed + timedelta(days=1)
            return parsed.timestamp()
        except ValueError:
            return None

    def _memory_result_to_tool_payload(self, item):
        payload = item.payload or {}
        return {
            "id": getattr(item, "id", None),
            "score": float(getattr(item, "score", 0.0)),
            "text": str(payload.get("text") or payload.get("personalizedText") or "").strip(),
            "textType": str(payload.get("textType", "Fact") or "Fact"),
            "memory_kind": str(payload.get("memory_kind", "") or "atomic_memory"),
            "memory_status": str(payload.get("memory_status", "active") or "active"),
            "timestamp": self._format_readable_time(payload.get("timestamp") or payload.get("UpdateTime")),
            "source": str(payload.get("source", "") or "").strip(),
            "source_file": str(payload.get("source_file", "") or "").strip(),
            "source_topic_group": payload.get("source_topic_group"),
            "topic_archive": self._get_topic_archive_payload(str(payload.get("source_file", "") or ""))
            if str(payload.get("memory_kind", "") or "").strip() == "topic_summary" and str(payload.get("source_file", "") or "").strip()
            else None,
        }

    @staticmethod
    def _extract_memory_timestamp_epoch(payload: dict):
        """从 payload 中提取时间戳，返回 epoch float 或 None。"""
        for key in ("timestamp", "UpdateTime", "superseded_at"):
            value = (payload or {}).get(key)
            if value in (None, ""):
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric_value = float(value)
                if numeric_value > 1e12:
                    numeric_value /= 1000.0
                return numeric_value
            try:
                numeric_value = float(value)
                if numeric_value > 1e12:
                    numeric_value /= 1000.0
                return numeric_value
            except (TypeError, ValueError):
                pass
            try:
                text = str(value).replace("Z", "+00:00")
                return datetime.fromisoformat(text).timestamp()
            except ValueError:
                continue
        return None

    def searchLongTermMemory(
        self,
        Query: str,
        IncludeHistorical: bool = False,
        Limit: int = 8,
        SinceDate: str = "",
        UntilDate: str = "",
        MemoryKind: str = "all",
        SortBy: str | None = None,
        Sort: str | None = None,
    ):
        normalized_query = str(Query or "").strip()
        if not normalized_query:
            return {"ok": False, "error": "Query is required."}
        if self.local_embedding_model is None:
            self.initialize_runtime()

        since_ts = self._parse_date_param(SinceDate, end_of_day=False)
        until_ts = self._parse_date_param(UntilDate, end_of_day=True)
        normalized_kind = str(MemoryKind or "all").strip().lower() or "all"
        if normalized_kind not in {"all", "atomic_memory", "topic_summary"}:
            return {"ok": False, "error": "MemoryKind must be one of: all, atomic_memory, topic_summary."}
        sort_input = SortBy
        if not str(sort_input or "").strip():
            sort_input = Sort
        normalized_sort = str(sort_input or "relevance").strip().lower() or "relevance"
        if normalized_sort not in {"relevance", "recency"}:
            return {"ok": False, "error": "SortBy (or legacy Sort) must be one of: relevance, recency."}

        limit = max(1, min(int(Limit or 8), 50))
        has_any_filter = any(
            value is not None
            for value in (since_ts, until_ts, None if normalized_kind == "all" else normalized_kind)
        )
        # 有过滤或 recency 排序时拉宽候选池，确保过滤后仍有足够结果。
        candidate_multiplier = 4 if (has_any_filter or normalized_sort == "recency") else 2
        candidate_top_n = max(limit * candidate_multiplier, limit + 8)

        try:
            query_parts = self._prepare_query_embeddings(normalized_query, self.local_embedding_model)
            if not query_parts:
                return {"ok": True, "count": 0, "results": []}
            memory_qdrant = self._get_or_create_search_qdrant("_memory_qdrant", self._memory_collection)
            raw_memory_results = self._collect_memory_results(query_parts, memory_qdrant)
            top_memory_results = self._select_top_memory_results(raw_memory_results, top_n=candidate_top_n)

            if normalized_sort == "recency":
                ordered_candidates = top_memory_results
            else:
                ordered_candidates = self._rerank_memory_results(normalized_query, top_memory_results, memory_qdrant)

            filtered_items = []
            for item in ordered_candidates:
                payload = item.payload or {}
                item_kind = str(payload.get("memory_kind", "") or "atomic_memory").strip().lower()
                if item_kind == "topic_summary":
                    continue
                memory_status = str(payload.get("memory_status", "active") or "active").strip().lower()
                if memory_status == "historical" and not IncludeHistorical:
                    continue
                if normalized_kind != "all" and item_kind != normalized_kind:
                    continue
                if since_ts is not None or until_ts is not None:
                    item_ts = self._extract_memory_timestamp_epoch(payload)
                    if item_ts is None:
                        continue
                    if since_ts is not None and item_ts < since_ts:
                        continue
                    if until_ts is not None and item_ts >= until_ts:
                        continue
                filtered_items.append(item)

            if normalized_sort == "recency":
                filtered_items.sort(
                    key=lambda it: self._extract_memory_timestamp_epoch(it.payload or {}) or 0.0,
                    reverse=True,
                )

            selected = [self._memory_result_to_tool_payload(item) for item in filtered_items[:limit]]
            return {
                "ok": True,
                "count": len(selected),
                "results": selected,
                "filters": {
                    "since_date": SinceDate or "",
                    "until_date": UntilDate or "",
                    "memory_kind": normalized_kind,
                    "sort_by": normalized_sort,
                    "include_historical": bool(IncludeHistorical),
                },
            }
        except Exception as error:
            logger.exception("searchLongTermMemory failed")
            return {"ok": False, "error": str(error)}

    def listTopicsByDate(
        self,
        SinceDate: str = "",
        UntilDate: str = "",
        Limit: int = 20,
        **kwargs,
    ):
        """按日期范围直接列出已归档话题（不走向量检索）。

        - SinceDate / UntilDate 接受 'YYYY-MM-DD' 或 ISO 时间；右端为左闭右开。
        - SinceDate、UntilDate 都为空时，返回最近归档（等价于 list_recent_archives）。
        """
        alias_limit = (
            kwargs.get("MaxResults")
            or kwargs.get("max_results")
            or kwargs.get("maxResults")
            or kwargs.get("PageSize")
            or kwargs.get("page_size")
            or kwargs.get("TopK")
            or kwargs.get("top_k")
            or ""
        )
        since_ts = self._parse_date_param(SinceDate, end_of_day=False)
        until_ts = self._parse_date_param(UntilDate, end_of_day=True)
        limit = max(1, min(int(Limit or alias_limit or 20), 50))
        try:
            archives = self.topic_archive_repository.list_archives_by_date_range(
                since_ts=since_ts,
                until_ts=until_ts,
                limit=limit,
            )
        except Exception as error:
            logger.exception("listTopicsByDate failed")
            return {"ok": False, "error": str(error)}

        results = []
        for archive in archives or []:
            topic_records = list(archive.get("topic_records") or [])
            results.append(
                {
                    "archive_id": archive.get("archive_id"),
                    "source_file": archive.get("source_file", ""),
                    "source_topic_group": archive.get("source_topic_group"),
                    "topic_message_count": int(archive.get("topic_message_count") or 0),
                    "summary_text": str(archive.get("summary_text", "") or "").strip(),
                    "topic_excerpt": self._build_topic_archive_excerpt(topic_records),
                    "archived_at": self._format_readable_time(archive.get("archived_at")),
                }
            )
        return {
            "ok": True,
            "count": len(results),
            "results": results,
            "filters": {
                "since_date": SinceDate or "",
                "until_date": UntilDate or "",
            },
        }

    def searchFullText(self, Query: str, Limit: int = 10):
        """使用 FTS5 全文搜索已归档的话题摘要，适用于精确关键词匹配。"""
        normalized_query = str(Query or "").strip()
        if not normalized_query:
            return {"ok": False, "error": "Query is required."}
        limit = max(1, min(int(Limit or 10), 50))
        try:
            archives = self.topic_archive_repository.search_fulltext(normalized_query, limit=limit)
        except Exception as error:
            logger.exception("searchFullText failed")
            return {"ok": False, "error": str(error)}
        results = []
        for archive in archives or []:
            topic_records = list(archive.get("topic_records") or [])
            results.append(
                {
                    "archive_id": archive.get("archive_id"),
                    "summary_text": str(archive.get("summary_text", "") or "").strip(),
                    "topic_message_count": int(archive.get("topic_message_count") or 0),
                    "topic_excerpt": self._build_topic_archive_excerpt(topic_records),
                    "archived_at": self._format_readable_time(archive.get("archived_at")),
                }
            )
        return {"ok": True, "count": len(results), "results": results}

    def storeLongTermMemory(
        self,
        Text: str,
        PersonalizedText: str = "",
        TextType: str = "Fact",
        Importance: float = 0.7,
        TTLDays: int = 30,
    ):
        normalized_text = str(Text or "").strip()
        if not normalized_text:
            return {"ok": False, "error": "Text is required."}
        if self.local_embedding_model is None:
            self.initialize_runtime()
        try:
            memory_qdrant = self._get_or_create_search_qdrant("_memory_qdrant", self._memory_collection)
            vector = self.local_embedding_model.encode(normalized_text)
            timestamp = time.time()
            memory_qdrant.add(
                normalized_text,
                str(PersonalizedText or normalized_text),
                vector,
                textType=str(TextType or "Fact").strip() or "Fact",
                importance=max(0.0, min(1.0, float(Importance or 0.7))),
                timestamp=timestamp,
                ttl=max(1, int(TTLDays or 30)),
                deduplication=False,
                memory_status="active",
                memory_status_detail="manual_store",
                memory_kind="atomic_memory",
                source="tool_store_long_term_memory",
                source_session_prefix=self.ContextJsonName,
                source_topic_group=self.topicGroup,
            )
            self._invalidate_memory_debug_snapshot()
            return {
                "ok": True,
                "text": normalized_text,
                "text_type": str(TextType or "Fact").strip() or "Fact",
                "importance": max(0.0, min(1.0, float(Importance or 0.7))),
                "ttl_days": max(1, int(TTLDays or 30)),
            }
        except Exception as error:
            logger.exception("storeLongTermMemory failed")
            return {"ok": False, "error": str(error)}

    def refreshMcpTools(self):
        discovered_count = self._refresh_mcp_dynamic_tools()
        self.tool_skill_map.update(self.dynamic_tool_registry.get_tool_skill_map())
        return {"ok": True, "count": discovered_count, "tools": self.listMcpTools().get("tools", [])}

    def searchLearnedProcedures(self, Query: str, Limit: int = 3):
        """搜索已学习的操作经验，返回与 Query 最相关的条目。

        Agent 在执行操作型任务前应主动调用此 tool 检查是否有可复用的经验。
        """
        normalized_query = str(Query or "").strip()
        if not normalized_query:
            return {"ok": False, "error": "Query is required."}
        procedures = self._context_memory_state.get("learned_procedures") or []
        if not procedures:
            return {"ok": True, "count": 0, "procedures": [], "hint": "暂无已学习的操作经验。"}
        limit = max(1, min(int(Limit or 3), 10))
        matched = self._match_relevant_procedures(procedures, normalized_query, threshold=0.25)
        if not matched:
            return {"ok": True, "count": 0, "procedures": [], "hint": "未找到与查询相关的操作经验。"}
        results = []
        for item in matched[:limit]:
            text = item.get("text", "") if isinstance(item, dict) else str(item or "")
            if text:
                results.append({"text": text, "id": item.get("id", "")})
        return {
            "ok": True,
            "count": len(results),
            "procedures": results,
            "hint": "以下是历史操作经验，不代表已执行。请参考这些快捷方式直接调用工具完成任务。",
        }

    def activateSkill(self, SkillName: str):
        """按需加载一个 skill 的完整说明，兼容 Agent Skills 的 SKILL.md。"""
        payload = build_skill_activation_payload(SkillName)
        if not payload.get("ok", False):
            return payload
        activation_policy = self._evaluate_skill_activation_request(payload)
        if not activation_policy.get("ok", False):
            return {
                "ok": False,
                "error": activation_policy.get("reason", "Skill activation blocked by policy."),
                "policy": activation_policy,
                "skill": payload.get("skill", {}),
            }
        self._apply_skill_activation_session_context(payload, activation_policy)
        governance = dict(((payload.get("skill") or {}).get("governance")) or {})
        payload["activation"] = {
            "ok": True,
            "explicit_user_request": bool(activation_policy.get("explicit_user_request", False)),
            "path_scope_matched": bool(activation_policy.get("path_scope_matched", True)),
            "referenced_paths": list(activation_policy.get("referenced_paths") or []),
            "preapproved_tools": list(governance.get("allowed_tools") or []),
        }
        return payload

    @staticmethod
    def _derive_skill_name_from_procedure(procedure_text: str, procedure_id: str = ""):
        trigger = str(procedure_text or "").split("→")[0].strip()
        ascii_name = re.sub(r"[^A-Za-z0-9\-]+", "-", trigger).strip("-").lower()
        ascii_name = re.sub(r"-{2,}", "-", ascii_name)
        if ascii_name:
            return ascii_name[:64]
        suffix = re.sub(r"[^A-Za-z0-9]+", "", str(procedure_id or ""))[-10:]
        return f"learned-procedure-{suffix or 'shortcut'}"

    def _find_learned_procedure(self, procedure_id: str = "", procedure_text: str = ""):
        normalized_id = str(procedure_id or "").strip()
        normalized_text = str(procedure_text or "").strip()
        for item in self._context_memory_state.get("learned_procedures") or []:
            text = item.get("text", "") if isinstance(item, dict) else str(item or "")
            item_id = item.get("id", "") if isinstance(item, dict) else ""
            if normalized_id and item_id == normalized_id:
                return {"id": item_id, "text": text}
            if normalized_text and text == normalized_text:
                return {"id": item_id, "text": text}
        return None

    def promoteLearnedProcedureToSkill(
        self,
        ProcedureId: str = "",
        ProcedureText: str = "",
        SkillName: str = "",
        Description: str = "",
        Enabled: bool = True,
    ):
        """把运行时学到的操作经验升级成 Agent Skills 风格的 SKILL.md。"""
        procedure = self._find_learned_procedure(ProcedureId, ProcedureText)
        if procedure is None:
            return {
                "ok": False,
                "error": "Learned procedure not found. Provide ProcedureId from searchLearnedProcedures or the exact ProcedureText.",
            }

        procedure_text = procedure["text"]
        trigger = procedure_text.split("→")[0].strip() if "→" in procedure_text else procedure_text
        skill_name = str(SkillName or "").strip() or self._derive_skill_name_from_procedure(procedure_text, procedure["id"])
        description = str(Description or "").strip() or f"Reusable shortcut for: {trigger}"
        instructions = "\n".join(
            [
                f"# {skill_name}",
                "",
                "## Purpose",
                description,
                "",
                "## Reusable Shortcut",
                procedure_text,
                "",
                "## Operating Rules",
                "- Treat this as a reusable shortcut, not as proof that the task is already complete.",
                "- Reuse concrete URLs, command arguments, and tool parameters exactly when they still match the request.",
                "- If the page, app, or API behavior has changed, execute the tools again and update the shortcut after the task.",
            ]
        )
        result = create_or_update_skill(
            skill_name=skill_name,
            description=description,
            when_to_use=[trigger],
            intent_examples=[trigger],
            tool_definitions=[],
            enabled=Enabled,
            skill_instructions=instructions,
        )
        self._refresh_runtime_tools()
        result.update(
            {
                "procedure_id": procedure["id"],
                "procedure_text": procedure_text,
                "skill_name": skill_name,
            }
        )
        return result

    def listMcpTools(self):
        tools = self.mcp_runtime.list_tools()
        return {"ok": True, "count": len(tools), "tools": tools}

    def listSkills(self):
        return list_installed_skills()

    def manageSkill(
        self,
        SkillName: str,
        Description: str,
        WhenToUse=None,
        IntentExamples=None,
        ToolDefinitions=None,
        RuntimeCode: str = "",
        Enabled: bool = True,
        SkillInstructions: str = "",
        AllowedTools=None,
        DisableModelInvocation: bool = False,
        UserInvocable: bool = True,
        Paths=None,
    ):
        try:
            if str(RuntimeCode or "").strip():
                return {
                    "ok": False,
                    "error": "RuntimeCode is disabled for managed skills. Use trusted built-in runtimes only.",
                }
            result = create_or_update_skill(
                skill_name=SkillName,
                description=Description,
                when_to_use=WhenToUse,
                intent_examples=IntentExamples,
                tool_definitions=ToolDefinitions,
                runtime_code=RuntimeCode,
                enabled=Enabled,
                skill_instructions=SkillInstructions,
                allowed_tools=AllowedTools,
                disable_model_invocation=DisableModelInvocation,
                user_invocable=UserInvocable,
                paths=Paths,
            )
            self._refresh_runtime_tools()
            return result
        except Exception as error:
            logger.exception("manageSkill failed")
            return {"ok": False, "error": str(error)}

    def deleteSkill(self, SkillName: str):
        try:
            result = delete_skill(SkillName)
            self._refresh_runtime_tools()
            return result
        except Exception as error:
            logger.exception("deleteSkill failed")
            return {"ok": False, "error": str(error)}

    def importSkill(self, Source: str, Overwrite: bool = False):
        try:
            result = marketplace_import_skill(Source, overwrite=bool(Overwrite))
            if result.get("ok"):
                self._refresh_runtime_tools()
            return result
        except Exception as error:
            logger.exception("importSkill failed")
            return {"ok": False, "error": str(error)}

    def exportSkill(self, SkillName: str, OutputPath: str = ""):
        try:
            return marketplace_export_skill(SkillName, OutputPath)
        except Exception as error:
            logger.exception("exportSkill failed")
            return {"ok": False, "error": str(error)}

    def browseSkillMarketplace(
        self,
        RegistryUrl: str = "",
        Query: str = "",
        InstallSkillName: str = "",
        Overwrite: bool = False,
    ):
        try:
            if str(InstallSkillName or "").strip() and str(RegistryUrl or "").strip():
                result = marketplace_install_from_registry(
                    RegistryUrl, InstallSkillName, overwrite=bool(Overwrite),
                )
                if result.get("ok"):
                    self._refresh_runtime_tools()
                return result
            if str(RegistryUrl or "").strip():
                return marketplace_browse_registry(RegistryUrl, query=Query)
            return marketplace_list_skills()
        except Exception as error:
            logger.exception("browseSkillMarketplace failed")
            return {"ok": False, "error": str(error)}

    def getTime(self):
        """返回当前本地时间字符串。"""
        now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info("nowTime: %s", now_time)
        return now_time

    @staticmethod
    @staticmethod
    def _supports_builtin_tools(llm_config: dict) -> bool:
        """Determine whether the current provider natively supports builtin tools."""
        return str(llm_config.get("provider", "")).strip().lower() == "kimi"

    def _resolve_agent_runtime(self, requested_model_key: str):
        """Select an Agent runtime that is compatible with the active tool set."""
        llm_config = get_LLM(requested_model_key)
        llm_capabilities = get_llm_capabilities(requested_model_key, self.config)
        agent_task_config = self._get_model_select_task_config("Agent")
        active_tools = list(self.tools) + self.dynamic_tool_registry.list_tool_definitions()
        session_context = self._get_active_tool_session_context()
        active_tools = [
            tool
            for tool in active_tools
            if get_tool_metadata(tool).get("toolset") in session_context.get("enabled_toolsets", set())
        ]

        allowed_tools = session_context.get("allowed_tools") or set()
        disallowed_tools = session_context.get("disallowed_tools") or set()
        if allowed_tools or disallowed_tools:
            def _tool_name(t):
                return str((t.get("function") or {}).get("name", "")).strip()

            if disallowed_tools:
                active_tools = [t for t in active_tools if _tool_name(t) not in disallowed_tools]
            if allowed_tools:
                active_tools = [t for t in active_tools if _tool_name(t) in allowed_tools]

        return {
            "model_key": requested_model_key,
            "llm": llm_config,
            "capabilities": llm_capabilities,
            "tools": active_tools,
            "thinking_enabled": True,
            "reasoning_effort": normalize_reasoning_effort(agent_task_config.get("reasoning_effort", "high")),
        }

    @staticmethod
    def _normalize_selected_ability_name(selected_ability: str) -> str:
        normalized_ability = str(selected_ability or "").strip()
        if ":" in normalized_ability:
            return normalized_ability.split(":", 1)[1].strip()
        return normalized_ability

    def _get_current_turn_tool_candidates(self):
        turn_request = dict(getattr(self, "_current_turn_agent_request", {}) or {})
        selected_ability = self._normalize_selected_ability_name(turn_request.get("selected_ability", ""))
        ranked_candidates = [
            dict(item)
            for item in (turn_request.get("ranked_candidates") or [])
            if isinstance(item, dict)
        ]
        return selected_ability, ranked_candidates

    def _build_agent_tool_pruning_set(self, active_tools: list, *, last_tool_name: str = ""):
        selected_ability, ranked_candidates = self._get_current_turn_tool_candidates()
        active_tool_names = {
            self._tool_name_from_definition(tool_definition)
            for tool_definition in active_tools
            if self._tool_name_from_definition(tool_definition)
        }
        if not active_tool_names:
            return set()

        candidate_lookup = {}
        for candidate in ranked_candidates:
            function_name = str(candidate.get("function_name", "") or "").strip()
            if function_name:
                candidate_lookup[function_name] = candidate

        keep_names = {
            "controlSelf",
            "getTime",
            "getLocation",
            "askUser",
            "resolveToolApproval",
            "summarizeToolResults",
        }
        current_user_input = str((getattr(self, "_current_turn_agent_request", {}) or {}).get("user_input", "") or "").strip()
        if self._looks_like_file_read_request(current_user_input):
            keep_names.add("readLocalFile")
        if "getWeather" in active_tool_names:
            keep_names.add("getWeather")

        subagent_tools = {
            "delegateTask",
            "delegateTasksParallel",
            "continueDelegatedTask",
            "cancelDelegatedTask",
            "getDelegatedTaskStatus",
            "listDelegatedTasks",
            "waitForDelegatedTasks",
        }
        browser_tools = set(BROWSER_TOOL_NAMES)
        schedule_tools = {
            "createScheduleTask",
            "deleteScheduleTask",
            "queryScheduleTasks",
            "updateScheduleTask",
        }
        skill_tools = {"listSkills", "manageSkill", "deleteSkill", "promoteLearnedProcedureToSkill", "importSkill", "exportSkill", "browseSkillMarketplace"}
        memory_tools = {"searchLongTermMemory", "storeLongTermMemory", "listTopicsByDate"}
        mcp_tools = {"listMcpTools", "refreshMcpTools"}

        if selected_ability:
            keep_names.add(selected_ability)
            candidate = candidate_lookup.get(selected_ability, {})
            for tool_name in candidate.get("registered_tools", []) or []:
                normalized_tool_name = str(tool_name or "").strip()
                if normalized_tool_name:
                    keep_names.add(normalized_tool_name)
            if selected_ability in subagent_tools:
                keep_names.update(subagent_tools)
            elif selected_ability in browser_tools:
                if not str(last_tool_name or "").strip().startswith("browser"):
                    keep_names.update(browser_tools)
            elif selected_ability in schedule_tools:
                keep_names.update(schedule_tools)
            elif selected_ability in skill_tools:
                keep_names.update(skill_tools)
            elif selected_ability in memory_tools:
                keep_names.update(memory_tools)
            elif selected_ability in mcp_tools:
                keep_names.update(mcp_tools)

        for candidate in ranked_candidates[:2]:
            function_name = str(candidate.get("function_name", "") or "").strip()
            if not function_name:
                continue
            keep_names.add(function_name)
            for tool_name in candidate.get("registered_tools", []) or []:
                normalized_tool_name = str(tool_name or "").strip()
                if normalized_tool_name:
                    keep_names.add(normalized_tool_name)

        pruned_names = {
            tool_name
            for tool_name in keep_names
            if tool_name in active_tool_names
        }
        if not pruned_names:
            return active_tool_names
        return pruned_names

    def _compute_agent_tool_pruning_names(self, active_tools: list, recent_messages=None):
        """返回单轮内被选中的工具名集合（不包含回退到全量的行为）。"""
        if not active_tools:
            return set()
        last_tool_name = ""
        for message in reversed(list(recent_messages or [])):
            if str(message.get("role", "")).strip().lower() != "tool":
                continue
            last_tool_name = str(message.get("name", "") or "").strip()
            if last_tool_name:
                break
        selected_tool_names = self._build_agent_tool_pruning_set(
            active_tools,
            last_tool_name=last_tool_name,
        )
        if last_tool_name in {"browserNavigate", "browserOpenTab", "browserSearch", "browserGoBack", "browserClick", "browserSelectTab", "browserCloseTab", "browserPressKey", "browserWait"}:
            selected_tool_names.update(
                {
                    "summarizeToolResults",
                    "controlSelf",
                    "browserExtractPage",
                    "browserClick",
                    "browserGoBack",
                    "browserListTabs",
                    "browserPressKey",
                    "browserScreenshot",
                    "browserScroll",
                    "browserSelectTab",
                    "browserSnapshot",
                    "browserType",
                    "browserWait",
                }
            )
        elif last_tool_name in {"browserSnapshot", "browserExtractPage", "browserReadLinkedPage", "browserScreenshot"}:
            selected_tool_names.update(
                {
                    "summarizeToolResults",
                    "controlSelf",
                    "browserClick",
                    "browserCloseTab",
                    "browserType",
                    "browserPressKey",
                    "browserScroll",
                    "browserGoBack",
                    "browserListTabs",
                    "browserScreenshot",
                    "browserSnapshot",
                    "browserSelectTab",
                    "browserExtractPage",
                    "browserWait",
                }
            )
        return selected_tool_names

    def _filter_agent_tools_for_turn(self, active_tools: list, recent_messages=None):
        if not active_tools:
            return []
        selected_tool_names = self._compute_agent_tool_pruning_names(active_tools, recent_messages)
        filtered_tools = [
            tool_definition
            for tool_definition in active_tools
            if self._tool_name_from_definition(tool_definition) in selected_tool_names
        ]
        selected_ability, ranked_candidates = self._get_current_turn_tool_candidates()
        last_tool_name = ""
        for message in reversed(list(recent_messages or [])):
            if str(message.get("role", "")).strip().lower() == "tool":
                last_tool_name = str(message.get("name", "") or "").strip()
                if last_tool_name:
                    break
        logger.info(
            "Agent tool pruning | selected_ability=%s | last_tool=%s | candidate_count=%s | tool_count_before=%s | tool_count_after=%s | kept_tools=%s",
            selected_ability or "none",
            last_tool_name or "none",
            len(ranked_candidates),
            len(active_tools),
            len(filtered_tools),
            [
                self._tool_name_from_definition(tool_definition)
                for tool_definition in filtered_tools
            ],
        )
        return filtered_tools or list(active_tools)

    def _mark_deferred_tool_loaded(self, tool_name: str):
        """将某个延迟工具标记为本轮内已加载完整 schema。"""
        normalized = str(tool_name or "").strip()
        if normalized:
            self._turn_loaded_deferred_tool_names.add(normalized)

    def _split_tools_core_and_deferred(self, active_tools: list, recent_messages=None):
        """把 active_tools 拆成 (core, deferred)。

        core 工具的完整 schema 会直接进入 LLM 请求的 tools 字段；deferred 工具只在
        system reminder 中以“名字 + 简介”形式出现，LLM 必须先调用 toolSearch 拉取
        完整 schema 后才能调用。
        """
        if not active_tools:
            return [], []
        active_tool_names = {
            self._tool_name_from_definition(tool_definition)
            for tool_definition in active_tools
            if self._tool_name_from_definition(tool_definition)
        }
        pruned_names = self._compute_agent_tool_pruning_names(active_tools, recent_messages)
        # 没有命中意图或裁剪结果等于全量时，退回到最小核心集，确保 deferred 不会为空。
        if not pruned_names or pruned_names >= active_tool_names:
            pruned_names = {name for name in DEFAULT_CORE_TOOL_NAMES if name in active_tool_names}
        core_names = set(pruned_names)
        core_names.add(TOOL_SEARCH_TOOL_NAME)
        # 如果有已学习的操作经验，将 searchLearnedProcedures 提升为核心工具
        if self._context_memory_state.get("learned_procedures"):
            core_names.add("searchLearnedProcedures")
        core_names.update(self._turn_loaded_deferred_tool_names)
        core_names &= active_tool_names
        core_tools = [
            tool_definition
            for tool_definition in active_tools
            if self._tool_name_from_definition(tool_definition) in core_names
        ]
        deferred_tools = [
            tool_definition
            for tool_definition in active_tools
            if self._tool_name_from_definition(tool_definition) not in core_names
        ]
        return core_tools, deferred_tools

    def _build_deferred_tool_catalog_reminder(self, deferred_tools: list) -> str:
        """构造 deferred 工具目录提示语。"""
        if not deferred_tools:
            return ""
        lines = [
            "【可按需加载的工具】以下工具仅列出名称与用途，完整参数 schema 尚未加载。",
            f"调用任何此处列出的工具前，必须先调用 `{TOOL_SEARCH_TOOL_NAME}` 获取 schema——"
            "使用 `Query=\"select:name1,name2\"` 精确加载，或用关键词搜索。",
            "已在当前 tools 列表中的工具不需要再次 toolSearch。",
            (
                f"为避免下一轮 LLM 请求因 schema 过多而失稳，"
                f"单轮最多只会激活 {MAX_DEFERRED_TOOL_ACTIVATIONS_PER_TURN} 个 deferred 工具；"
                "如果需要更多，请缩小查询范围后分批加载。"
            ),
            "",
        ]
        for tool_definition in deferred_tools:
            name = self._tool_name_from_definition(tool_definition)
            description = str(
                (tool_definition.get("function") or {}).get("description", "")
            ).strip() or "（无描述）"
            lines.append(f"- {name}: {description}")
        return "\n".join(lines)

    def toolSearch(self, Query: str = "", MaxResults: int = 5):
        """按名字或关键词拉取 deferred 工具的完整 JSONSchema。

        - ``Query`` 形如 ``select:tool_a,tool_b`` 时按名字精确加载；
        - 否则把 ``Query`` 当作关键词，按名字/描述匹配打分返回若干候选；
        - 命中的工具会被登记到 ``_turn_loaded_deferred_tool_names``，下一轮 LLM 请求时
          它们的完整 schema 会被自动放进 ``tools`` 字段。
        - 为保持后续请求稳定，单轮最多只会激活
          ``MAX_DEFERRED_TOOL_ACTIVATIONS_PER_TURN`` 个 deferred 工具。
        """
        query = str(Query or "").strip()
        if not query:
            return {"ok": False, "error": "Query is required."}
        try:
            max_results = int(MaxResults) if MaxResults not in (None, "") else 5
        except (TypeError, ValueError):
            max_results = 5
        max_results = max(1, min(20, max_results))

        active_tools_snapshot = list(self._turn_active_tools_snapshot or [])
        if not active_tools_snapshot:
            # Fallback: 取 runtime 全量工具，保证 toolSearch 在非 run_agent 上下文中也可用。
            active_tools_snapshot = list(self.tools) + self.dynamic_tool_registry.list_tool_definitions()
        catalog_by_name = {}
        for tool_definition in active_tools_snapshot:
            tool_name = self._tool_name_from_definition(tool_definition)
            if tool_name:
                catalog_by_name[tool_name] = tool_definition

        matched_tools = []
        not_found = []
        if query.lower().startswith("select:"):
            raw_names = query.split(":", 1)[1]
            requested_names = [
                part.strip()
                for part in raw_names.split(",")
                if part.strip()
            ]
            for name in requested_names:
                tool_definition = catalog_by_name.get(name)
                if tool_definition is None:
                    not_found.append(name)
                    continue
                matched_tools.append(tool_definition)
        else:
            tokens = [token for token in re.split(r"\s+", query.lower()) if token]
            if not tokens:
                return {"ok": False, "error": "Empty keyword query."}
            scored = []
            for tool_definition in active_tools_snapshot:
                function_definition = tool_definition.get("function") or {}
                name = str(function_definition.get("name", "")).strip()
                if not name:
                    continue
                description = str(function_definition.get("description", "") or "").strip()
                haystack = f"{name} {description}".lower()
                score = sum(1 for token in tokens if token in haystack)
                if score > 0:
                    scored.append((score, name, tool_definition))
            scored.sort(key=lambda item: (-item[0], item[1]))
            for _, name, tool_definition in scored[:max_results]:
                matched_tools.append(tool_definition)
        activated_tools = matched_tools[:MAX_DEFERRED_TOOL_ACTIVATIONS_PER_TURN]
        omitted_tools = matched_tools[MAX_DEFERRED_TOOL_ACTIVATIONS_PER_TURN:]
        for tool_definition in activated_tools:
            tool_name = self._tool_name_from_definition(tool_definition)
            if tool_name:
                self._mark_deferred_tool_loaded(tool_name)

        response = {
            "ok": True,
            "tools": [
                {
                    "name": str((tool_definition.get("function") or {}).get("name", "")),
                    "description": str((tool_definition.get("function") or {}).get("description", "")),
                    "parameters": (tool_definition.get("function") or {}).get("parameters", {}),
                }
                for tool_definition in activated_tools
            ],
        }
        if not_found:
            response["not_found"] = not_found
        if omitted_tools:
            omitted_names = [
                self._tool_name_from_definition(tool_definition)
                for tool_definition in omitted_tools
                if self._tool_name_from_definition(tool_definition)
            ]
            response["activation_limit"] = MAX_DEFERRED_TOOL_ACTIVATIONS_PER_TURN
            response["not_loaded"] = omitted_names
            response["note"] = (
                f"Loaded only the first {MAX_DEFERRED_TOOL_ACTIVATIONS_PER_TURN} matched tools "
                "to keep the next LLM request stable. Narrow the query or call toolSearch again "
                "with a smaller exact select:name1,name2 batch."
            )
        elif not matched_tools:
            response["note"] = "No tools matched the query. Check the deferred tool catalog reminder."
        logger.info(
            "toolSearch | query=%s | matched=%s | loaded=%s | omitted=%s | not_found=%s",
            query,
            [str((tool.get("function") or {}).get("name", "")) for tool in matched_tools],
            [str((tool.get("function") or {}).get("name", "")) for tool in activated_tools],
            [str((tool.get("function") or {}).get("name", "")) for tool in omitted_tools],
            not_found,
        )
        return response

    def _build_agent_request_payload(
        self,
        tmp_agent_message,
        active_tools,
        current_llm: dict,
        thinking_enabled: bool,
        reasoning_effort: str = "high",
    ):
        return agent_runtime_helpers._build_agent_request_payload(self, tmp_agent_message, active_tools, current_llm, thinking_enabled, reasoning_effort)

    def _request_agent_response(
        self,
        tmp_agent_message,
        current_llm: dict,
        active_tools,
        thinking_enabled: bool,
        reasoning_effort: str = "high",
    ):
        return agent_runtime_helpers._request_agent_response(self, tmp_agent_message, current_llm, active_tools, thinking_enabled, reasoning_effort)

    def _resolve_agent_llm_log_caller(self, *, default: str = "Agent") -> str:
        session_context = self._get_active_tool_session_context()
        custom_caller = str((session_context or {}).get("llm_caller_prefix", "") or "").strip()
        if custom_caller:
            return custom_caller
        return str(default or "Agent").strip() or "Agent"

    @staticmethod
    def _parse_tool_call_arguments(tool_call: dict):
        function_args_raw = ((tool_call or {}).get("function") or {}).get("arguments", "{}")
        if isinstance(function_args_raw, str) and function_args_raw.strip():
            return json.loads(function_args_raw)
        if isinstance(function_args_raw, dict):
            return function_args_raw
        return {}

    def _normalize_tool_arg_keys(self, tool_definition: dict, function_args: dict) -> dict:
        return agent_runtime_helpers._normalize_tool_arg_keys(self, tool_definition, function_args)

    def execute_tool_call(self, tool_call: dict):
        return agent_runtime_helpers.execute_tool_call(self, tool_call)

    def _resolve_kimi_llm_config(self):
        """Find a Kimi model config suitable for builtin tool execution."""
        for candidate_key in ("kimi", "kimi_2.0"):
            try:
                candidate = get_LLM(candidate_key)
            except KeyError:
                continue
            if self._supports_builtin_tools(candidate):
                if "thinking" not in str(candidate.get("modelName", "")).lower():
                    return candidate
        return None

    def _execute_builtin_tool_via_kimi(self, function_name: str, function_args: dict):
        return agent_runtime_helpers._execute_builtin_tool_via_kimi(self, function_name, function_args)

    def run_agent(self, model):
        return agent_runtime_helpers.run_agent(self, model)

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
        return agent_runtime_helpers._agent_loop_body(self, tmp_agent_message=tmp_agent_message, tool_trace=tool_trace, executed_tool_calls=executed_tool_calls, max_tool_calls=max_tool_calls, max_consecutive_same_calls=max_consecutive_same_calls, runtime_settings=runtime_settings, current_llm=current_llm, thinking_enabled=thinking_enabled, model=model)

    def _evaluate_detached_task_lifecycle(self, task_control: dict | None = None):
        return agent_runtime_helpers._evaluate_detached_task_lifecycle(self, task_control)

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
        return agent_runtime_helpers._run_detached_agent_loop(self, tmp_agent_message=tmp_agent_message, model=model, runtime_config=runtime_config, runtime_settings=runtime_settings, tool_trace=tool_trace, executed_tool_calls=executed_tool_calls, task_control=task_control)

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
        return agent_runtime_helpers.run_agent_detached(self, model, base_messages=base_messages, max_tool_calls=max_tool_calls, agent_type=agent_type, agent_session=agent_session, task_input=task_input, task_context=task_context, task_control=task_control)

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
        return agent_runtime_helpers.resume_detached_agent(self, model, agent_session=agent_session, max_tool_calls=max_tool_calls, agent_type=agent_type, user_reply=user_reply, approval_decision=approval_decision, task_context=task_context, task_control=task_control)

    # ------------------------------------------------------------------
    # 娑撹顕拠婵囩ウ
    # ------------------------------------------------------------------
    def SummaryMemory(self, EmbeddingModel: SentenceTransformer = None):
        """启动历史摘要 worker。"""
        return self._start_summary_memory_worker()

    async def TopicSame(self, Message: list):
        """判断最近消息是否仍属于同一话题。"""
        task_config = self._get_topic_postprocess_llm_task_config("topic_same")
        if not task_config.get("enabled", True):
            return True, 0
        model_key = task_config.get("model")
        if not model_key:
            return True, 0
        topic_prompt = self.topicEnded_message.copy()
        topic_prompt.append({"role": "user", "content": str(Message)})
        result = await asyncio.to_thread(
            call_LLM,
            topic_prompt,
            model_key,
            self.session,
            task_config.get("thinking", True),
            task_config.get("json_mode", True),
            caller="topic_same",
            reasoning_effort=task_config.get("reasoning_effort", "high"),
        )
        result = json.loads(result)
        return bool(result.get("isTopicSame", True)), result.get("lastTopicIndex", 0)

    def _append_to_live_contexts(self, role: str, content: str):
        """把新消息同步追加到各条活跃上下文。"""
        payload = {"role": role, "content": content}
        self.agent_message.append(dict(payload))
        self.simple_message.append(dict(payload))
        self.role_play_message.append(dict(payload))

    def _trim_agent_context(self):
        """按配置裁剪 Agent 上下文，保留关键 system 与最近消息。"""
        if len(self.agent_message) <= self.config["Summary"]["Agent_Max"]:
            return
        keep_index = {
            idx
            for idx, message in enumerate(self.agent_message)
            if message.get("role") == "system"
        }
        keep_index.update({len(self.agent_message) - 2, len(self.agent_message) - 1})
        self.agent_message[:] = [msg for idx, msg in enumerate(self.agent_message) if idx in keep_index]

    async def appendUserMessage(self, user_input: str, role: str):
        """追加消息并触发后处理任务。"""
        postprocess_task = self._append_message_immediately(user_input, role)
        self._postprocess_queue.put(postprocess_task)
        return



    def _build_intention_ability_lookup(self):
        lookup = {}
        for ability in load_intention_ability_definitions():
            function_name = self._get_intention_storage_function_name(ability)
            if not function_name or function_name in lookup:
                continue
            lookup[function_name] = {
                "ability_type": str(ability.get("ability_type", "") or "").strip(),
                "description": str(ability.get("description", "") or "").strip(),
                "when_to_use": [
                    str(item).strip()
                    for item in (ability.get("when_to_use", []) or [])
                    if str(item).strip()
                ],
                "registered_tools": [
                    str(item).strip()
                    for item in (ability.get("registered_tools", []) or [])
                    if str(item).strip()
                ],
            }
        return lookup

    def _build_llm_intent_context(self):
        """从 role_play_message 中提取 user/assistant 对话，构建意图识别上下文文本。"""
        parts = []
        for message in self.role_play_message:
            role = message.get("role", "")
            content = str(message.get("content", "")).strip()
            if not content:
                continue
            if role == "user":
                parts.append(f"用户：\n{content}")
            elif role == "assistant":
                parts.append(f"助手：\n{content}")
        return "\n\n".join(parts)

    def _should_route_to_agent_llm(self, user_input: str) -> bool:
        """纯 LLM 意图识别：每次用户输入时调用 LLM 判断是否需要工具调用。"""
        task_config = self._get_model_select_task_config(
            "LLMIntentRouter", fallback_task_names=("IntentRouter",)
        )
        model_key = task_config.get("model") or "qwen_flash"
        context_text = self._build_llm_intent_context()
        prompt_messages = [
            {"role": "system", "content": setSystemPrompt("LLMIntentRouter")},
            {"role": "user", "content": context_text},
        ]
        try:
            result_text = call_LLM(
                prompt_messages,
                model_key,
                self.session,
                task_config.get("thinking", False),
                True,
                caller="LLMIntentRouter",
                reasoning_effort=task_config.get("reasoning_effort", "high"),
            )
            parsed_result = json.loads(result_text)
        except Exception as exc:
            logger.warning("LLM intent router failed | error=%s", exc)
            self._set_current_turn_agent_request(
                user_input=user_input,
                route="agent",
                reason="llm_intent_router_error",
            )
            return True

        needs_tool = bool(parsed_result.get("FunctionCalling", True))
        route = "agent" if needs_tool else "rag"
        self._set_current_turn_agent_request(
            user_input=user_input,
            route=route,
            reason="llm_intent_router",
        )
        logger.info(
            "LLM intent router decided | FunctionCalling=%s | route=%s",
            needs_tool,
            route,
        )
        return needs_tool

    def _collect_ranked_intention_candidates(self, query_parts, intention_qdrant: Qdrant, candidate_limit: int = 4):
        """聚合所有查询片段的意图召回结果，并按 FunctionName 保留最高分候选。"""
        ability_lookup = self._build_intention_ability_lookup()
        aggregated_candidates = {}
        per_part_limit = max(candidate_limit * 2, candidate_limit)

        for query_part in query_parts:
            part_text = query_part["text"]
            results = intention_qdrant.SearchRawScore(query_part["embedding"])
            if not results:
                logger.info("Intention search succeeded | part=%s | result_count=0", part_text)
                continue

            for result in results[:per_part_limit]:
                normalized_payload = self._normalize_intention_payload(result.payload or {})
                function_name = normalized_payload.get("FunctionName", "").strip()
                if not function_name:
                    continue

                score = float(getattr(result, "score", 0.0))
                ability = ability_lookup.get(function_name, {})
                existing_candidate = aggregated_candidates.get(function_name)
                if existing_candidate is not None and score <= existing_candidate["score"]:
                    continue

                aggregated_candidates[function_name] = {
                    "function_name": function_name,
                    "score": score,
                    "matched_text": str(normalized_payload.get("text", "") or "").strip(),
                    "query_part": part_text,
                    "ability_type": str(ability.get("ability_type", "") or "tool").strip() or "tool",
                    "description": str(ability.get("description", "") or "").strip(),
                    "when_to_use": list(ability.get("when_to_use", []))[:3],
                    "registered_tools": list(ability.get("registered_tools", []))[:4],
                }

        ranked_candidates = sorted(
            aggregated_candidates.values(),
            key=lambda candidate: candidate["score"],
            reverse=True,
        )[:candidate_limit]
        logger.info(
            "Intention candidates ranked | part_count=%s | candidate_count=%s | top_score=%s",
            len(query_parts),
            len(ranked_candidates),
            ranked_candidates[0]["score"] if ranked_candidates else 0.0,
        )
        return ranked_candidates

    def _build_intent_router_messages(self, user_input: str, ranked_candidates):
        router_request = {
            "user_input": str(user_input or "").strip(),
            "candidate_abilities": [
                {
                    "function_name": candidate["function_name"],
                    "ability_type": candidate["ability_type"],
                    "score": round(float(candidate["score"]), 4),
                    "description": candidate["description"],
                    "when_to_use": candidate["when_to_use"],
                    "registered_tools": candidate["registered_tools"],
                    "matched_text": candidate["matched_text"],
                    "matched_query_part": candidate["query_part"],
                }
                for candidate in ranked_candidates
            ],
        }
        return [
            {"role": "system", "content": setSystemPrompt("IntentRouter")},
            {
                "role": "user",
                "content": json.dumps(router_request, ensure_ascii=False, indent=2),
            },
        ]

    def _route_with_llm_fallback(self, user_input: str, ranked_candidates, router_config):
        llm_fallback_config = router_config.get("llm_fallback", {})
        if not llm_fallback_config.get("enabled", True):
            return None

        model_key = (
            llm_fallback_config.get("model")
            or self._get_model_select_model_key("IntentRouter", fallback_task_names=())
            or self._get_model_select_model_key("Agent")
            or "qwen_flash"
        )
        prompt_messages = self._build_intent_router_messages(user_input, ranked_candidates)
        try:
            result_text = call_LLM(
                prompt_messages,
                model_key,
                self.session,
                llm_fallback_config.get("thinking", False),
                True,
                caller="IntentRouter",
                reasoning_effort=llm_fallback_config.get("reasoning_effort", "high"),
            )
            parsed_result = json.loads(result_text)
        except Exception as exc:
            logger.warning("Intent router LLM failed | error=%s", exc)
            return None

        route = str(parsed_result.get("route", "") or "").strip().lower()
        if route not in {"agent", "rag"}:
            logger.warning("Intent router LLM returned invalid route | raw=%s", parsed_result)
            return None

        decision = {
            "route": route,
            "confidence": str(parsed_result.get("confidence", "") or "").strip().lower(),
            "reason": str(parsed_result.get("reason", "") or "").strip(),
            "selected_ability": str(parsed_result.get("selected_ability", "") or "").strip(),
        }
        logger.info(
            "Intent router LLM decided | route=%s | confidence=%s | selected_ability=%s | reason=%s",
            decision["route"],
            decision["confidence"],
            decision["selected_ability"],
            decision["reason"],
        )
        return decision

    def _should_route_to_agent(self, user_input: str, query_parts, intention_qdrant: Qdrant) -> bool:
        """使用“向量初筛 + LLM 灰区复核”的方式决定应走 Agent 还是 RAG。"""
        router_config = self._get_intent_router_config()
        ranked_candidates = self._collect_ranked_intention_candidates(
            query_parts,
            intention_qdrant,
            candidate_limit=router_config["candidate_limit"],
        )
        top_score = ranked_candidates[0]["score"] if ranked_candidates else 0.0

        if not router_config.get("enabled", True):
            legacy_route = top_score > 0.7
            self._set_current_turn_agent_request(
                user_input=user_input,
                route="agent" if legacy_route else "rag",
                selected_ability=ranked_candidates[0]["function_name"] if ranked_candidates else "",
                reason="intent_router_disabled",
                ranked_candidates=ranked_candidates,
            )
            logger.info(
                "Intent router disabled | top_score=%s | route=%s",
                top_score,
                "agent" if legacy_route else "rag",
            )
            return legacy_route

        if top_score >= router_config["high_confidence_threshold"]:
            self._set_current_turn_agent_request(
                user_input=user_input,
                route="agent",
                selected_ability=ranked_candidates[0]["function_name"] if ranked_candidates else "",
                reason="vector_high_confidence",
                ranked_candidates=ranked_candidates,
            )
            logger.info(
                "Intent router decided | strategy=vector_high_confidence | top_score=%s | threshold=%s | route=agent",
                top_score,
                router_config["high_confidence_threshold"],
            )
            return True

        should_consult_llm = (
            router_config["low_confidence_threshold"] < top_score < router_config["high_confidence_threshold"]
        )
        if not should_consult_llm:
            self._set_current_turn_agent_request(
                user_input=user_input,
                route="rag",
                selected_ability=ranked_candidates[0]["function_name"] if ranked_candidates else "",
                reason="vector_low_confidence",
                ranked_candidates=ranked_candidates,
            )
            logger.info(
                "Intent router decided | strategy=vector_low_confidence | top_score=%s | threshold=%s | route=rag",
                top_score,
                router_config["low_confidence_threshold"],
            )
            return False

        llm_decision = self._route_with_llm_fallback(user_input, ranked_candidates, router_config)
        if llm_decision is not None:
            self._set_current_turn_agent_request(
                user_input=user_input,
                route=llm_decision["route"],
                selected_ability=llm_decision.get("selected_ability", ""),
                reason=llm_decision.get("reason", ""),
                ranked_candidates=ranked_candidates,
            )
            return llm_decision["route"] == "agent"

        fallback_route = top_score > 0.7
        self._set_current_turn_agent_request(
            user_input=user_input,
            route="agent" if fallback_route else "rag",
            selected_ability=ranked_candidates[0]["function_name"] if ranked_candidates else "",
            reason="llm_fallback_failed",
            ranked_candidates=ranked_candidates,
        )
        logger.warning(
            "Intent router fell back after LLM failure | top_score=%s | route=%s",
            top_score,
            "agent" if fallback_route else "rag",
        )
        return fallback_route

    def _collect_rag_results(self, query_parts, rag_qdrant: Qdrant):
        """汇总各查询片段的原始 RAG 检索结果。"""
        rag_results = []
        for query_part in query_parts:
            result = rag_qdrant.SearchRawScore(query_part["embedding"])
            rag_results.extend(result)
        logger.info(
            "RAG raw search succeeded | part_count=%s | hit_count=%s",
            len(query_parts),
            len(rag_results)
        )
        return rag_results

    @staticmethod
    def _build_rag_result_key(item):
        payload = item.payload or {}
        text = str(payload.get("text", "")).strip()
        memory_timestamp = payload.get("timestamp")
        if memory_timestamp in (None, ""):
            memory_timestamp = payload.get("UpdateTime")
        return (
            getattr(item, "id", None),
            str(memory_timestamp or ""),
            text,
        )

    def _select_top_rag_results(self, rag_results, top_n: int = 10):
        """Merge duplicate hits by identity, keep best vector score, and take top N."""
        best_results = OrderedDict()
        for item in rag_results:
            result_key = self._build_rag_result_key(item)
            existing_item = best_results.get(result_key)
            if existing_item is None or float(getattr(item, "score", 0.0)) > float(getattr(existing_item, "score", 0.0)):
                best_results[result_key] = item

        ranked_results = sorted(
            best_results.values(),
            key=lambda candidate: float(getattr(candidate, "score", 0.0)),
            reverse=True
        )
        top_results = ranked_results[:max(0, int(top_n))]
        logger.info(
            "RAG vector ranking completed | raw_count=%s | unique_count=%s | selected_count=%s",
            len(rag_results),
            len(ranked_results),
            len(top_results)
        )
        return top_results

    def _rerank_rag_results(self, user_input: str, rag_results, rag_qdrant: Qdrant):
        """Rerank the selected vector hits once with the full user input."""
        normalized_input = str(user_input or "").strip()
        if not normalized_input or not rag_results:
            return rag_results

        reranked_results = rag_qdrant._rerank_and_boost(rag_results, query_text=normalized_input)
        filtered_results = [
            item
            for item in reranked_results
            if float(getattr(item, "score", 0.0)) > float(rag_qdrant.rank_score)
        ]
        logger.info(
            "RAG rerank completed | selected_count=%s | reranked_count=%s | threshold=%s | filtered_count=%s",
            len(rag_results),
            len(reranked_results),
            rag_qdrant.rank_score,
            len(filtered_results)
        )
        return filtered_results

    @staticmethod
    def _insert_after_last_user_message(messages: list, system_content: str):
        """在 messages 列表中最后一条 user 消息之后插入一条 system 消息。

        如果找不到 user 消息，则追加到末尾。直接修改传入的列表。
        """
        insert_index = len(messages)
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                insert_index = i + 1
                break
        messages.insert(insert_index, {"role": "system", "content": system_content})

    def _build_rag_request_message(self, rag_results):
        """将 RAG 结果转换为 RolePlay 请求消息。

        RAG 上下文会被插入到最后一条 user 消息之后（而非追加到末尾），
        这样多轮 RAG 调用时每次的检索结果都紧跟对应的用户提问。
        """
        rag_system_context = ""
        if not rag_results:
            return self._build_messages_with_persistent_contexts(self.role_play_message), rag_system_context

        rag_prompt = setSystemPrompt("Rag")
        rag_context = self._build_rag_context(rag_results)
        if rag_context["memories"]:
            rag_context_text = json.dumps(rag_context, ensure_ascii=False, indent=2)
            logger.info("RAG_data: %s", rag_context_text)
            rag_system_context = f"{rag_prompt}\n{rag_context_text}"
            logger.info("RAG Prompt: %s", rag_system_context)

        # 先构建带持久上下文的消息列表（不含本次 RAG）
        request_message = self._build_messages_with_persistent_contexts(self.role_play_message)
        # 将本次 RAG 上下文插入到最后一条 user 消息之后
        if rag_system_context:
            self._insert_after_last_user_message(request_message, rag_system_context)
        return request_message, rag_system_context

    def IntentionRAG(
        self,
        UserInput: str,
        EmbeddingModel: SentenceTransformer,
        append_async: bool = True
    ):
        """执行意图路由与 RAG 回复流程。"""
        intent_method = self._get_intent_router_method()
        query_parts = None

        if intent_method == "llm":
            route_to_agent = self._should_route_to_agent_llm(UserInput)
        else:
            query_parts = self._prepare_query_embeddings(UserInput, EmbeddingModel)
            if not query_parts:
                logger.info("Query embedding skipped | reason=empty_query")
                return True
            intention_qdrant = self._get_or_create_search_qdrant("_intention_qdrant", self._intention_collection)
            route_to_agent = self._should_route_to_agent(UserInput, query_parts, intention_qdrant)

        if route_to_agent:
            if self._try_answer_from_retrieval_cache(UserInput, append_async=append_async):
                return True
            return False

        if query_parts is None:
            query_parts = self._prepare_query_embeddings(UserInput, EmbeddingModel)
        if not query_parts:
            logger.info("Query embedding skipped | reason=empty_query_for_rag")
            return True

        rag_qdrant = self._get_or_create_search_qdrant("_rag_qdrant", self._rag_collection)
        rag_results = self._collect_rag_results(query_parts, rag_qdrant)
        top_rag_results = self._select_top_rag_results(rag_results, top_n=10)
        reranked_rag_results = self._rerank_rag_results(UserInput, top_rag_results, rag_qdrant)
        request_message, rag_system_context = self._build_rag_request_message(reranked_rag_results)
        role_play_task_config = self._get_model_select_task_config(ROLE_PLAY_TASK_NAME)
        with self._temporary_due_reminder_contexts(request_message) as due_tasks:
            result = call_LLM(
                request_message,
                role_play_task_config.get("model") or "kimi",
                self.session,
                role_play_task_config.get("thinking", True),
                role_play_task_config.get("json_mode", False),
                caller="RAG_Reply",
                reasoning_effort=role_play_task_config.get("reasoning_effort", "high"),
            )
        logger.info("RAG Result: %s", result)
        if rag_system_context:
            with self._runtime_lock:
                self._insert_after_last_user_message(self.role_play_message, rag_system_context)
                self._insert_after_last_user_message(self.simple_message, rag_system_context)
        self._acknowledge_due_reminders(due_tasks)
        if append_async:
            self.appendUserMessageAsync(result, "assistant")
        else:
            self.appendUserMessageSync(result, "assistant")
        return True

    def start(self, frontend_runtime=None):
        """启动 Selena 的交互主循环。"""
        self.initialize_runtime()
        try:
            while self.chat_cycle:
                if frontend_runtime is not None:
                    print("请输入: ", end="", flush=True)
                    frontend_runtime.announce_ready()
                    user_input = input()
                else:
                    user_input = input("请输入: ")
                logger.info("用户输入: %s", user_input)
                if not str(user_input or "").strip():
                    continue
                self.process_user_input(user_input)
        finally:
            self.shutdown()
        logger.info("ChatCycle is False, break")


if __name__ == "__main__":
    selena = Selena()
    frontend_runtime = None
    try:
        try:
            from DialogueSystem.runtime.frontend_runtime import DialogueFrontendRuntime
        except ImportError:
            from DialogueSystem.runtime.frontend_runtime import DialogueFrontendRuntime
        frontend_runtime = DialogueFrontendRuntime(selena)
        frontend_runtime.start()
        selena.start(frontend_runtime=frontend_runtime)
    finally:
        if frontend_runtime is not None:
            frontend_runtime.stop()
        selena.shutdown()
