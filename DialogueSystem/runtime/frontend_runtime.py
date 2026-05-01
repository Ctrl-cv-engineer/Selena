"""Local API bridge and frontend launcher for DialogueSystem."""

import glob
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from qdrant_client import QdrantClient
from qdrant_client.models import PointIdsList, PointStruct

from project_config import get_frontend_config, get_project_config, save_project_config

try:
    from DialogueSystem.llm.CallingAPI import get_llm_call_logs
    from DialogueSystem.config.paths import AUTONOMOUS_TASK_DB_PATH, PROJECT_ROOT, RETRIEVAL_CACHE_DB_PATH, SCRIPT_DIR
except ImportError:
    from DialogueSystem.llm.CallingAPI import get_llm_call_logs
    from DialogueSystem.config.paths import AUTONOMOUS_TASK_DB_PATH, PROJECT_ROOT, RETRIEVAL_CACHE_DB_PATH, SCRIPT_DIR


logger = logging.getLogger(__name__)


def _get_atm_session_data(date_filter: str | None = None) -> dict:
    """Read ATM sessions/tasks/attempts from SQLite for the inspector page."""
    db_path = str(AUTONOMOUS_TASK_DB_PATH)
    if not os.path.exists(db_path):
        return {"ok": True, "sessions": [], "tasks": [], "attempts": []}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if date_filter:
            sessions = [dict(r) for r in cur.execute(
                "SELECT * FROM autonomous_daily_sessions WHERE session_date = ? ORDER BY session_date DESC", (date_filter,)
            )]
            tasks = [dict(r) for r in cur.execute(
                "SELECT * FROM autonomous_tasks WHERE task_date = ? ORDER BY id DESC", (date_filter,)
            )]
            attempts = [dict(r) for r in cur.execute(
                "SELECT a.* FROM autonomous_task_attempts a WHERE a.task_date = ? ORDER BY a.started_at DESC", (date_filter,)
            )]
        else:
            sessions = [dict(r) for r in cur.execute(
                "SELECT * FROM autonomous_daily_sessions ORDER BY session_date DESC"
            )]
            tasks = [dict(r) for r in cur.execute(
                "SELECT * FROM autonomous_tasks ORDER BY id DESC"
            )]
            attempts = [dict(r) for r in cur.execute(
                "SELECT * FROM autonomous_task_attempts ORDER BY started_at DESC"
            )]
        conn.close()
        return {"ok": True, "sessions": sessions, "tasks": tasks, "attempts": attempts}
    except Exception:
        logger.exception("Failed to read ATM session data from SQLite")
        return {"ok": False, "sessions": [], "tasks": [], "attempts": [], "error": "db_read_failed"}

TOPIC_ARCHIVE_COLLECTION_NAME = "topic_archive"
TOPIC_ARCHIVE_COLLECTION_LABEL = "话题归档 / 情节记忆"
TOPIC_ARCHIVE_COLLECTION_DESCRIPTION = "SQLite 话题归档仓储"
RETRIEVAL_CACHE_COLLECTION_NAME = "agent_retrieval_cache"
RETRIEVAL_CACHE_COLLECTION_LABEL = "Agent 临时检索缓存"
RETRIEVAL_CACHE_COLLECTION_DESCRIPTION = "SQLite 临时缓存，保存 Agent 最近的工具检索结果与摘要。"

ATM_COLLECTION_SESSIONS = "atm_sessions"
ATM_COLLECTION_TASKS = "atm_tasks"
ATM_COLLECTION_ATTEMPTS = "atm_attempts"
ATM_COLLECTIONS: dict[str, dict] = {
    ATM_COLLECTION_SESSIONS: {
        "label": "ATM 会话",
        "description": "自主任务模式 - 每日会话记录",
        "table": "autonomous_daily_sessions",
        "pk": "id",
        "order": "session_date DESC",
    },
    ATM_COLLECTION_TASKS: {
        "label": "ATM 任务",
        "description": "自主任务模式 - 任务列表",
        "table": "autonomous_tasks",
        "pk": "id",
        "order": "id DESC",
    },
    ATM_COLLECTION_ATTEMPTS: {
        "label": "ATM 执行尝试",
        "description": "自主任务模式 - 每次执行尝试记录",
        "table": "autonomous_task_attempts",
        "pk": "attempt_id",
        "order": "started_at DESC",
    },
}


class DialogueFrontendRuntime:
    """Hosts a lightweight local API and optionally launches the React frontend."""

    def __init__(self, selena):
        self.selena = selena
        self.frontend_process = None
        self.frontend_log_handle = None
        self.http_server = None
        self.http_thread = None
        self.config = get_frontend_config()
        self._ready_announced = False

    @property
    def frontend_root(self):
        return os.path.join(SCRIPT_DIR, "frontend")

    @property
    def frontend_url(self):
        return f"http://{self.config['host']}:{self.config['port']}"

    @property
    def api_url(self):
        return f"http://{self.config['host']}:{self.config['api_port']}"

    @staticmethod
    def _is_port_in_use(host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((host, int(port))) == 0

    def start(self):
        self.config = get_frontend_config()
        self._ready_announced = False
        if not self.config.get("enabled", True):
            logger.info("Frontend runtime disabled by config")
            return
        self._start_api_server()
        if self.config.get("auto_start", True):
            self._start_frontend_process()

    def announce_ready(self):
        if self._ready_announced:
            return
        if not self.config.get("enabled", True):
            return
        logger.info(
            "Frontend runtime ready | frontend_url=%s | api_url=%s",
            self.frontend_url,
            self.api_url,
        )
        self._ready_announced = True

    def stop(self):
        if self.http_server is not None:
            try:
                self.http_server.shutdown()
                self.http_server.server_close()
            except Exception:
                logger.exception("Failed to stop frontend API server")
            finally:
                self.http_server = None
        if self.http_thread is not None and self.http_thread.is_alive():
            self.http_thread.join(timeout=2)
            self.http_thread = None

        if self.frontend_process is not None:
            try:
                if self.frontend_process.poll() is None:
                    self.frontend_process.terminate()
                    self.frontend_process.wait(timeout=5)
            except Exception:
                logger.exception("Failed to stop frontend dev server cleanly")
                try:
                    self.frontend_process.kill()
                except Exception:
                    logger.exception("Failed to kill frontend dev server")
            finally:
                self.frontend_process = None

        if self.frontend_log_handle is not None:
            try:
                self.frontend_log_handle.close()
            finally:
                self.frontend_log_handle = None

    def _start_api_server(self):
        host = self.config["host"]
        api_port = int(self.config["api_port"])
        if self._is_port_in_use(host, api_port):
            raise RuntimeError(f"DialogueSystem API port {api_port} is already in use.")

        handler = self._build_handler()
        self.http_server = ThreadingHTTPServer((host, api_port), handler)
        self.http_server.daemon_threads = True
        self.http_thread = threading.Thread(
            target=self.http_server.serve_forever,
            name="dialogue-frontend-api",
            daemon=True,
        )
        self.http_thread.start()

    def _start_frontend_process(self):
        host = self.config["host"]
        port = int(self.config["port"])
        if self._is_port_in_use(host, port):
            logger.info("Frontend port already in use, skip auto-start | port=%s", port)
            return

        node_path = shutil.which("node")
        frontend_config_path = os.path.join(self.frontend_root, "vite.config.ts")
        vite_entry_candidates = [
            os.path.join(self.frontend_root, "node_modules", "vite", "bin", "vite.js"),
            os.path.join(PROJECT_ROOT, "node_modules", "vite", "bin", "vite.js"),
        ]
        vite_entry_candidates.extend(
            sorted(
                glob.glob(
                    os.path.join(
                        self.frontend_root,
                        "node_modules",
                        ".pnpm",
                        "vite@*",
                        "node_modules",
                        "vite",
                        "bin",
                        "vite.js",
                    )
                )
            )
        )
        vite_entry_candidates.extend(
            sorted(
                glob.glob(
                    os.path.join(
                        PROJECT_ROOT,
                        "node_modules",
                        ".pnpm",
                        "vite@*",
                        "node_modules",
                        "vite",
                        "bin",
                        "vite.js",
                    )
                )
            )
        )
        vite_entry = next((path for path in vite_entry_candidates if os.path.exists(path)), None)

        command = None
        if node_path and vite_entry:
            command = [
                node_path,
                vite_entry,
                "--config",
                frontend_config_path,
                "--host",
                host,
                "--port",
                str(port),
                "--strictPort",
            ]
        else:
            vite_cmd = os.path.join(self.frontend_root, "node_modules", ".bin", "vite.CMD")
            if os.path.exists(vite_cmd):
                command = [
                    vite_cmd,
                    "--config",
                    frontend_config_path,
                    "--host",
                    host,
                    "--port",
                    str(port),
                    "--strictPort",
                ]

        if command is None:
            package_manager = str(self.config.get("package_manager", "pnpm") or "pnpm").strip()
            package_manager_path = shutil.which(f"{package_manager}.cmd") or shutil.which(package_manager)
            if package_manager_path:
                command = [
                    package_manager_path,
                    "run",
                    "dev",
                    "--",
                    "--host",
                    host,
                    "--port",
                    str(port),
                    "--strictPort",
                ]

        if command is None:
            logger.warning("Skip auto-start frontend | reason=missing_node_or_package_manager")
            return

        logs_dir = os.path.join(SCRIPT_DIR, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        frontend_log_path = os.path.join(logs_dir, "frontend_dev.log")
        self.frontend_log_handle = open(frontend_log_path, "w", encoding="utf-8")
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.frontend_process = subprocess.Popen(
            command,
            cwd=self.frontend_root,
            stdin=subprocess.DEVNULL,
            stdout=self.frontend_log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
        logger.info("Frontend dev server launched | pid=%s | url=%s", self.frontend_process.pid, self.frontend_url)

    def _build_handler(self):
        runtime = self

        class RequestHandler(BaseHTTPRequestHandler):
            server_version = "DialogueFrontendRuntime/1.0"

            def log_message(self, format, *args):
                logger.debug("Frontend API | " + format, *args)

            def _send_json(self, payload, status=HTTPStatus.OK):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()
                self.wfile.write(body)

            def _read_json_body(self):
                content_length = int(self.headers.get("Content-Length", "0") or 0)
                if content_length <= 0:
                    return {}
                raw_body = self.rfile.read(content_length).decode("utf-8")
                if not raw_body.strip():
                    return {}
                return json.loads(raw_body)

            def _handle_error(self, error, status=HTTPStatus.BAD_REQUEST):
                logger.exception("Frontend API request failed")
                self._send_json(
                    {
                        "ok": False,
                        "error": str(error),
                    },
                    status=status,
                )

            @staticmethod
            def _normalize_bool_param(value, default=False):
                if value is None:
                    return bool(default)
                normalized = str(value).strip().lower()
                if normalized in {"1", "true", "yes", "on"}:
                    return True
                if normalized in {"0", "false", "no", "off"}:
                    return False
                return bool(default)

            def do_OPTIONS(self):
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_GET(self):
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                try:
                    if parsed.path == "/api/health":
                        self._send_json(
                            {
                                "ok": True,
                                "frontend_url": runtime.frontend_url,
                                "api_url": runtime.api_url,
                            }
                        )
                        return
                    if parsed.path == "/api/runtime":
                        self._send_json({"ok": True, **runtime.selena.export_runtime_state()})
                        return
                    if parsed.path == "/api/config":
                        self._send_json({"ok": True, "config": get_project_config()})
                        return
                    if parsed.path == "/api/schedules":
                        date_value = (query.get("date") or [None])[0]
                        tasks = runtime.selena.schedule_repository.list_tasks(
                            task_date=date_value,
                            limit=200,
                        )
                        self._send_json(
                            {
                                "ok": True,
                                "date": date_value,
                                "tasks": tasks,
                            }
                        )
                        return
                    if parsed.path == "/api/collections":
                        self._send_json({"ok": True, "collections": runtime._list_collections()})
                        return
                    if parsed.path == "/api/llm-logs":
                        since_id = int((query.get("since_id") or ["0"])[0])
                        self._send_json({"ok": True, "logs": get_llm_call_logs(since_id)})
                        return
                    if parsed.path == "/api/atm-llm-logs":
                        since_id = int((query.get("since_id") or ["0"])[0])
                        all_logs = get_llm_call_logs(since_id)
                        atm_logs = [e for e in all_logs if str(e.get("caller", "")).startswith("autonomous_task.")]
                        self._send_json({"ok": True, "logs": atm_logs})
                        return
                    if parsed.path == "/api/atm-sessions":
                        date_filter = (query.get("date") or [None])[0]
                        self._send_json(_get_atm_session_data(date_filter))
                        return
                    if parsed.path == "/api/workbench":
                        self._send_json(runtime._get_workbench_payload())
                        return
                    if parsed.path == "/api/workbench/diff":
                        diff_path = str((query.get("path") or [""])[0]).strip()
                        staged = self._normalize_bool_param((query.get("staged") or ["false"])[0], default=False)
                        self._send_json(runtime._get_git_diff_preview(diff_path, staged=staged))
                        return
                    if parsed.path == "/api/subagents":
                        include_completed = self._normalize_bool_param(
                            (query.get("include_completed") or ["true"])[0],
                            default=True,
                        )
                        self._send_json(runtime.selena.listDelegatedTasks(IncludeCompleted=include_completed))
                        return
                    if parsed.path.startswith("/api/subagents/"):
                        task_id = unquote(parsed.path[len("/api/subagents/") :]).strip()
                        if not task_id:
                            raise ValueError("task id is required")
                        self._send_json(runtime.selena.getDelegatedTaskStatus(task_id))
                        return
                    if parsed.path == "/api/mcp-tools":
                        self._send_json(runtime.selena.listMcpTools())
                        return
                    if parsed.path == "/api/tool-approvals":
                        self._send_json(runtime.selena.listPendingToolApprovals())
                        return
                    if parsed.path == "/api/skills":
                        self._send_json(runtime.selena.listSkills())
                        return
                    if parsed.path == "/api/memory/search":
                        search_query = str((query.get("query") or [""])[0]).strip()
                        include_historical = self._normalize_bool_param(
                            (query.get("include_historical") or ["false"])[0],
                            default=False,
                        )
                        limit = int((query.get("limit") or ["8"])[0])
                        self._send_json(
                            runtime.selena.searchLongTermMemory(
                                Query=search_query,
                                IncludeHistorical=include_historical,
                                Limit=limit,
                            )
                        )
                        return
                    if parsed.path == "/api/browser/status":
                        self._send_json(runtime._get_browser_status_payload())
                        return
                    collection_route = runtime._parse_collection_route(parsed.path)
                    if collection_route is not None:
                        collection_name, tail_segments = collection_route
                        if tail_segments:
                            self._send_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                            return
                        limit_value = int((query.get("limit") or ["100"])[0])
                        self._send_json(
                            {
                                "ok": True,
                                **runtime._get_collection_payload(collection_name, limit=limit_value),
                            }
                        )
                        return
                    self._send_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                except Exception as error:
                    self._handle_error(error)

            def do_POST(self):
                parsed = urlparse(self.path)
                try:
                    if parsed.path == "/api/chat/clear":
                        state = runtime.selena.clear_dialogue_context()
                        self._send_json({"ok": True, **state})
                        return
                    if parsed.path == "/api/chat/continue":
                        state = runtime.selena.continue_last_dialogue()
                        self._send_json({"ok": True, **state})
                        return
                    if parsed.path == "/api/chat":
                        payload = self._read_json_body()
                        message = str(payload.get("message", "")).strip()
                        if not message:
                            raise ValueError("message is required")
                        state = runtime.selena.process_user_input(message)
                        self._send_json({"ok": True, **state})
                        return
                    if parsed.path == "/api/agent/interrupt":
                        payload = self._read_json_body()
                        message = str(payload.get("message", "")).strip()
                        if not message:
                            raise ValueError("message is required")
                        runtime.selena.submit_agent_interrupt(message)
                        self._send_json({"ok": True, "message": "Interrupt submitted."})
                        return
                    if parsed.path == "/api/subagents":
                        payload = self._read_json_body()
                        task = str(payload.get("task", "")).strip()
                        if not task:
                            raise ValueError("task is required")
                        model = str(payload.get("model", "")).strip()
                        raw_max_tool_calls = payload.get("max_tool_calls")
                        max_tool_calls = None if raw_max_tool_calls in (None, "") else int(raw_max_tool_calls)
                        timeout_seconds = float(payload.get("timeout_seconds", 60.0) or 60.0)
                        priority_value = payload.get("priority")
                        priority = None if priority_value in (None, "") else int(priority_value)
                        use_cache = self._normalize_bool_param(payload.get("use_cache"), default=True)
                        result = runtime.selena.delegateTask(
                            Task=task,
                            AgentType=str(payload.get("agent_type", "general") or "general").strip() or "general",
                            Context=payload.get("task_context", payload.get("Context")),
                            Model=model,
                            MaxToolCalls=max_tool_calls,
                            TimeoutSeconds=timeout_seconds,
                            Priority=priority,
                            UseCache=use_cache,
                        )
                        self._send_json(
                            result,
                            status=HTTPStatus.CREATED if result.get("ok", False) else HTTPStatus.BAD_REQUEST,
                        )
                        return
                    if parsed.path == "/api/subagents/batch":
                        payload = self._read_json_body()
                        raw_tasks = payload.get("tasks")
                        if not isinstance(raw_tasks, list) or not raw_tasks:
                            raise ValueError("tasks is required")
                        wait_for_completion = self._normalize_bool_param(payload.get("wait_for_completion"), default=False)
                        result = runtime.selena.delegateTasksParallel(
                            Tasks=list(raw_tasks),
                            GroupLabel=str(payload.get("group_label", "")).strip(),
                            WaitForCompletion=wait_for_completion,
                            TimeoutSeconds=float(payload.get("timeout_seconds", 20.0) or 20.0),
                            PollIntervalSeconds=float(payload.get("poll_interval_seconds", 0.5) or 0.5),
                        )
                        self._send_json(
                            result,
                            status=HTTPStatus.CREATED if result.get("ok", False) else HTTPStatus.BAD_REQUEST,
                        )
                        return
                    if parsed.path == "/api/subagents/wait":
                        payload = self._read_json_body()
                        raw_task_ids = payload.get("task_ids")
                        if not isinstance(raw_task_ids, list) or not raw_task_ids:
                            raise ValueError("task_ids is required")
                        result = runtime.selena.waitForDelegatedTasks(
                            TaskIds=list(raw_task_ids),
                            TimeoutSeconds=float(payload.get("timeout_seconds", 20.0) or 20.0),
                            PollIntervalSeconds=float(payload.get("poll_interval_seconds", 0.5) or 0.5),
                        )
                        self._send_json(result, status=HTTPStatus.OK if result.get("ok", False) else HTTPStatus.BAD_REQUEST)
                        return
                    if parsed.path.startswith("/api/subagents/") and parsed.path.endswith("/continue"):
                        task_id = unquote(parsed.path[len("/api/subagents/") : -len("/continue")]).strip().strip("/")
                        if not task_id:
                            raise ValueError("task id is required")
                        payload = self._read_json_body()
                        result = runtime.selena.continueDelegatedTask(
                            TaskId=task_id,
                            UserReply=str(payload.get("user_reply", "")).strip(),
                            ApprovalDecision=str(payload.get("approval_decision", "")).strip(),
                        )
                        self._send_json(result, status=HTTPStatus.OK if result.get("ok", False) else HTTPStatus.BAD_REQUEST)
                        return
                    if parsed.path.startswith("/api/subagents/") and parsed.path.endswith("/cancel"):
                        task_id = unquote(parsed.path[len("/api/subagents/") : -len("/cancel")]).strip().strip("/")
                        if not task_id:
                            raise ValueError("task id is required")
                        payload = self._read_json_body()
                        result = runtime.selena.cancelDelegatedTask(
                            TaskId=task_id,
                            Reason=str(payload.get("reason", "")).strip(),
                        )
                        self._send_json(result, status=HTTPStatus.OK if result.get("ok", False) else HTTPStatus.BAD_REQUEST)
                        return
                    if parsed.path == "/api/mcp-tools/refresh":
                        self._send_json(runtime.selena.refreshMcpTools())
                        return
                    if parsed.path == "/api/tool-approvals/resolve":
                        payload = self._read_json_body()
                        self._send_json(
                            runtime.selena.resolveToolApproval(
                                ApprovalId=payload.get("approval_id", ""),
                                Decision=payload.get("decision", ""),
                            )
                        )
                        return
                    if parsed.path == "/api/skills":
                        payload = self._read_json_body()
                        self._send_json(
                            runtime.selena.manageSkill(
                                SkillName=payload.get("skill_name", ""),
                                Description=payload.get("description", ""),
                                WhenToUse=payload.get("when_to_use"),
                                IntentExamples=payload.get("intent_examples"),
                                ToolDefinitions=payload.get("tool_definitions"),
                                RuntimeCode=payload.get("runtime_code", ""),
                                Enabled=payload.get("enabled", True),
                                SkillInstructions=payload.get("skill_instructions", ""),
                            )
                        )
                        return
                    if parsed.path == "/api/memory/search":
                        payload = self._read_json_body()
                        self._send_json(
                            runtime.selena.searchLongTermMemory(
                                Query=payload.get("query", ""),
                                IncludeHistorical=bool(payload.get("include_historical", False)),
                                Limit=int(payload.get("limit", 8) or 8),
                            )
                        )
                        return
                    if parsed.path == "/api/memory/store":
                        payload = self._read_json_body()
                        self._send_json(
                            runtime.selena.storeLongTermMemory(
                                Text=payload.get("text", ""),
                                PersonalizedText=payload.get("personalized_text", ""),
                                TextType=payload.get("text_type", "Fact"),
                                Importance=float(payload.get("importance", 0.7) or 0.7),
                                TTLDays=int(payload.get("ttl_days", 30) or 30),
                            )
                        )
                        return
                    if parsed.path == "/api/browser/open-tab":
                        payload = self._read_json_body()
                        self._send_json(runtime._call_runtime_tool("browserOpenTab", Url=payload.get("url", "")))
                        return
                    if parsed.path == "/api/browser/extract-page":
                        payload = self._read_json_body()
                        self._send_json(
                            runtime._call_runtime_tool(
                                "browserExtractPage",
                                MaxTextLength=int(payload.get("max_text_length", 5000) or 5000),
                            )
                        )
                        return
                    if parsed.path == "/api/browser/read-linked-page":
                        payload = self._read_json_body()
                        self._send_json(
                            runtime._call_runtime_tool(
                                "browserReadLinkedPage",
                                Query=payload.get("query", ""),
                                Ref=payload.get("ref", ""),
                                MaxTextLength=int(payload.get("max_text_length", 5000) or 5000),
                                AutoOpenFirst=self._normalize_bool_param(payload.get("auto_open_first", False), False),
                                MaxCandidates=int(payload.get("max_candidates", 5) or 5),
                            )
                        )
                        return
                    if parsed.path == "/api/workbench/worktrees":
                        payload = self._read_json_body()
                        self._send_json(
                            runtime._create_worktree(
                                branch=payload.get("branch", ""),
                                base_ref=payload.get("base_ref", ""),
                                path=payload.get("path", ""),
                            ),
                            status=HTTPStatus.CREATED,
                        )
                        return
                    if parsed.path == "/api/workbench/worktrees/remove":
                        payload = self._read_json_body()
                        self._send_json(runtime._remove_worktree(payload.get("path", "")))
                        return
                    collection_route = runtime._parse_collection_route(parsed.path)
                    if collection_route is not None:
                        collection_name, tail_segments = collection_route
                        payload = self._read_json_body()
                        if tail_segments == ["records"]:
                            self._send_json(
                                {"ok": True, **runtime._create_collection_record(collection_name, payload)},
                                status=HTTPStatus.CREATED,
                            )
                            return
                        if tail_segments == ["records", "batch-delete"]:
                            self._send_json(
                                {"ok": True, **runtime._delete_collection_records(collection_name, payload.get("ids"))}
                            )
                            return
                    self._send_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                except Exception as error:
                    self._handle_error(error)

            def do_PUT(self):
                parsed = urlparse(self.path)
                try:
                    if parsed.path == "/api/config":
                        payload = self._read_json_body()
                        new_config = payload.get("config") if isinstance(payload, dict) else None
                        if new_config is None:
                            new_config = payload
                        if not isinstance(new_config, dict):
                            raise ValueError("config payload must be a JSON object")

                        previous_frontend = get_frontend_config()
                        save_project_config(new_config)
                        runtime.selena.reload_config()
                        latest_config = get_project_config()
                        restart_required = get_frontend_config(latest_config) != previous_frontend
                        self._send_json(
                            {
                                "ok": True,
                                "config": latest_config,
                                "restart_required": restart_required,
                            }
                        )
                        return
                    collection_route = runtime._parse_collection_route(parsed.path)
                    if collection_route is not None:
                        collection_name, tail_segments = collection_route
                        if len(tail_segments) == 2 and tail_segments[0] == "records":
                            payload = self._read_json_body()
                            self._send_json(
                                {
                                    "ok": True,
                                    **runtime._update_collection_record(
                                        collection_name,
                                        tail_segments[1],
                                        payload,
                                    ),
                                }
                            )
                            return
                    self._send_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                except Exception as error:
                    self._handle_error(error)

            def do_DELETE(self):
                parsed = urlparse(self.path)
                try:
                    if parsed.path.startswith("/api/skills/"):
                        skill_name = unquote(parsed.path[len("/api/skills/") :]).strip()
                        if not skill_name:
                            raise ValueError("skill name is required")
                        self._send_json(runtime.selena.deleteSkill(skill_name))
                        return
                    collection_route = runtime._parse_collection_route(parsed.path)
                    if collection_route is not None:
                        collection_name, tail_segments = collection_route
                        if len(tail_segments) == 2 and tail_segments[0] == "records":
                            self._send_json(
                                {
                                    "ok": True,
                                    **runtime._delete_collection_records(collection_name, [tail_segments[1]]),
                                }
                            )
                            return
                    self._send_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                except Exception as error:
                    self._handle_error(error)

        return RequestHandler

    def _create_qdrant_client(self):
        qdrant_setting = get_project_config().get("Qdrant_Setting", {})
        try:
            return QdrantClient(
                host=qdrant_setting.get("host", "127.0.0.1"),
                port=int(qdrant_setting.get("port", 6333)),
                prefer_grpc=bool(qdrant_setting.get("prefer_grpc", False)),
                grpc_port=int(qdrant_setting.get("grpc_port", 6334)),
            )
        except TypeError:
            return QdrantClient(
                host=qdrant_setting.get("host", "127.0.0.1"),
                port=int(qdrant_setting.get("port", 6333)),
                prefer_grpc=bool(qdrant_setting.get("prefer_grpc", False)),
            )

    @staticmethod
    def _parse_collection_route(path: str):
        prefix = "/api/collections/"
        if not path.startswith(prefix):
            return None
        parts = [unquote(part) for part in path[len(prefix) :].split("/") if part]
        if not parts:
            return None
        return parts[0], parts[1:]

    @staticmethod
    def _extract_vector_size(vectors_config):
        if isinstance(vectors_config, dict):
            if not vectors_config:
                return 0
            first_key = next(iter(vectors_config))
            return int(getattr(vectors_config[first_key], "size", 0) or 0)
        return int(getattr(vectors_config, "size", 0) or 0)

    @staticmethod
    def _describe_vectors(vectors_config):
        if isinstance(vectors_config, dict):
            named_vector_sizes = {
                str(name): int(getattr(vector_config, "size", 0) or 0)
                for name, vector_config in vectors_config.items()
            }
            return {
                "vector_kind": "named",
                "vector_size": next(iter(named_vector_sizes.values()), 0) if len(named_vector_sizes) == 1 else 0,
                "vector_names": list(named_vector_sizes.keys()),
                "named_vector_sizes": named_vector_sizes,
            }
        return {
            "vector_kind": "single",
            "vector_size": int(getattr(vectors_config, "size", 0) or 0),
            "vector_names": [],
            "named_vector_sizes": {},
        }

    @staticmethod
    def _normalize_point_id(value):
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        text = str(value or "").strip()
        if not text:
            raise ValueError("record id is required")
        if text.lstrip("-").isdigit():
            return int(text)
        return text

    @staticmethod
    def _normalize_dense_vector(vector, expected_size: int):
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        if isinstance(vector, tuple):
            vector = list(vector)
        if isinstance(vector, list) and len(vector) == 1 and isinstance(vector[0], (list, tuple)):
            vector = list(vector[0])
        if not isinstance(vector, list):
            raise ValueError("vector must be a JSON array")
        if expected_size and len(vector) != expected_size:
            raise ValueError(f"vector size mismatch: expected {expected_size}, got {len(vector)}")
        return vector

    def _normalize_vector_input(self, vector, vectors_config):
        if isinstance(vectors_config, dict):
            named_vector_sizes = {
                str(name): int(getattr(vector_config, "size", 0) or 0)
                for name, vector_config in vectors_config.items()
            }
            if isinstance(vector, dict):
                normalized_vectors = {}
                missing_names = []
                for name, expected_size in named_vector_sizes.items():
                    if name not in vector:
                        missing_names.append(name)
                        continue
                    normalized_vectors[name] = self._normalize_dense_vector(vector[name], expected_size)
                if missing_names:
                    raise ValueError(
                        "missing named vectors: " + ", ".join(sorted(missing_names))
                    )
                return normalized_vectors
            if len(named_vector_sizes) != 1:
                raise ValueError("this collection uses multiple named vectors; provide a JSON object keyed by vector name")
            vector_name, expected_size = next(iter(named_vector_sizes.items()))
            return {vector_name: self._normalize_dense_vector(vector, expected_size)}

        if isinstance(vector, dict):
            if len(vector) != 1:
                raise ValueError("this collection expects a single vector array")
            vector = next(iter(vector.values()))
        return self._normalize_dense_vector(vector, int(getattr(vectors_config, "size", 0) or 0))

    @staticmethod
    def _serialize_collection_record(record):
        payload = dict(record.payload or {})
        payload["id"] = record.id
        return payload

    def _get_next_point_id(self, client, collection_name: str):
        max_id = -1
        offset = None
        while True:
            records, offset = client.scroll(
                collection_name=collection_name,
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            if not records:
                break
            for record in records:
                if isinstance(record.id, int):
                    max_id = max(max_id, record.id)
            if offset is None:
                break
        return max_id + 1

    @staticmethod
    def _extract_vector_source_text(payload, explicit_text=None):
        explicit_value = str(explicit_text or "").strip()
        if explicit_value:
            return explicit_value

        preferred_keys = (
            "personalizedText",
            "text",
            "content",
            "message",
            "title",
            "description",
            "name",
        )
        for key in preferred_keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        for value in payload.values():
            if isinstance(value, str) and value.strip():
                return value.strip()

        raise ValueError(
            "No text field found for local embedding. Provide vector JSON or include text/personalizedText/content."
        )

    def _get_local_embedding_model(self):
        model = getattr(self.selena, "local_embedding_model", None)
        if model is not None:
            return model

        runtime_lock = getattr(self.selena, "_runtime_lock", None)
        if runtime_lock is None:
            self.selena.local_embedding_model = self.selena.warmup_models([])
            return self.selena.local_embedding_model

        with runtime_lock:
            model = getattr(self.selena, "local_embedding_model", None)
            if model is None:
                self.selena.local_embedding_model = self.selena.warmup_models([])
            return self.selena.local_embedding_model

    def _build_local_embedding_vector(self, collection_name: str, payload: dict, vectors_config, explicit_text=None):
        embedding_model = self._get_local_embedding_model()
        source_text = self._extract_vector_source_text(payload, explicit_text=explicit_text)
        raw_vector = embedding_model.encode(source_text)
        try:
            return self._normalize_vector_input(raw_vector, vectors_config)
        except ValueError as error:
            model_dimension = self._normalize_dense_vector(raw_vector, 0)
            expected_size = self._extract_vector_size(vectors_config)
            raise ValueError(
                f"Local embedding dimension {len(model_dimension)} does not match collection '{collection_name}' vector size {expected_size}. Please provide vector JSON manually."
            ) from error

    @staticmethod
    def _is_topic_archive_collection(collection_name: str) -> bool:
        return str(collection_name or "").strip() == TOPIC_ARCHIVE_COLLECTION_NAME

    @staticmethod
    def _parse_datetime_value(value):
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric_value = float(value)
            if numeric_value > 1e12:
                numeric_value /= 1000.0
            return numeric_value
        text = str(value).strip()
        if not text:
            return None
        try:
            numeric_value = float(text)
            if numeric_value > 1e12:
                numeric_value /= 1000.0
            return numeric_value
        except (TypeError, ValueError):
            pass
        normalized = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).timestamp()
            except ValueError:
                continue
        raise ValueError(f"Unsupported datetime value: {text}")

    def _build_topic_archive_field_schema(self):
        return [
            {"key": "source_file", "sample": ""},
            {"key": "source_session_prefix", "sample": ""},
            {"key": "source_topic_group", "sample": 0},
            {"key": "topic_message_count", "sample": 0},
            {"key": "summary_text", "sample": ""},
            {"key": "topic_records", "sample": []},
            {"key": "archived_at", "sample": "2026-01-01 12:00:00"},
        ]

    def _serialize_topic_archive_record(self, archive_record):
        topic_records = list((archive_record or {}).get("topic_records") or [])
        return {
            "id": archive_record.get("archive_id"),
            "source_file": str(archive_record.get("source_file", "") or ""),
            "source_session_prefix": str(archive_record.get("source_session_prefix", "") or ""),
            "source_topic_group": archive_record.get("source_topic_group"),
            "topic_message_count": int(archive_record.get("topic_message_count") or 0),
            "summary_text": str(archive_record.get("summary_text", "") or ""),
            "topic_records": topic_records,
            "topic_excerpt": self.selena._build_topic_archive_excerpt(topic_records),
            "archived_at": self.selena._format_readable_time(archive_record.get("archived_at")),
            "updated_at": self.selena._format_readable_time(archive_record.get("updated_at")),
        }

    def _normalize_topic_archive_payload(self, mutation_payload):
        if not isinstance(mutation_payload, dict):
            raise ValueError("request payload must be a JSON object")
        record_payload = mutation_payload.get("payload")
        if not isinstance(record_payload, dict):
            raise ValueError("payload must be a JSON object")
        topic_records = record_payload.get("topic_records")
        if topic_records in (None, ""):
            topic_records = []
        if not isinstance(topic_records, list):
            raise ValueError("topic_records must be a JSON array")
        source_topic_group = record_payload.get("source_topic_group")
        if source_topic_group in (None, ""):
            normalized_topic_group = None
        else:
            normalized_topic_group = int(source_topic_group)
        topic_message_count = record_payload.get("topic_message_count")
        if topic_message_count in (None, ""):
            normalized_message_count = len(topic_records)
        else:
            normalized_message_count = max(0, int(topic_message_count))
        archived_at = self._parse_datetime_value(record_payload.get("archived_at")) or self._parse_datetime_value(
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        return {
            "source_file": str(record_payload.get("source_file", "") or "").strip(),
            "source_session_prefix": str(record_payload.get("source_session_prefix", "") or "").strip(),
            "source_topic_group": normalized_topic_group,
            "topic_message_count": normalized_message_count,
            "summary_text": str(record_payload.get("summary_text", "") or "").strip(),
            "topic_records": topic_records,
            "archived_at": archived_at,
        }

    def _get_topic_archive_collection_payload(self, *, limit: int = 100):
        normalized_limit = max(1, min(int(limit or 100), 500))
        archives = self.selena.topic_archive_repository.list_archives(limit=normalized_limit)
        return {
            "name": TOPIC_ARCHIVE_COLLECTION_NAME,
            "label": TOPIC_ARCHIVE_COLLECTION_LABEL,
            "storage_kind": "sqlite",
            "description": TOPIC_ARCHIVE_COLLECTION_DESCRIPTION,
            "exists": True,
            "total": self.selena.topic_archive_repository.count_archives(),
            "vector_kind": "single",
            "vector_size": 0,
            "vector_names": [],
            "named_vector_sizes": {},
            "field_schema": self._build_topic_archive_field_schema(),
            "records": [self._serialize_topic_archive_record(item) for item in archives],
        }

    def _create_topic_archive_record(self, mutation_payload):
        archive_record = self.selena.topic_archive_repository.create_archive(
            **self._normalize_topic_archive_payload(mutation_payload)
        )
        serialized_record = self._serialize_topic_archive_record(archive_record)
        return {
            "collection": TOPIC_ARCHIVE_COLLECTION_NAME,
            "id": serialized_record.get("id"),
            "record": serialized_record,
        }

    def _update_topic_archive_record(self, record_id, mutation_payload):
        archive_record = self.selena.topic_archive_repository.update_archive(
            record_id,
            **self._normalize_topic_archive_payload(mutation_payload),
        )
        serialized_record = self._serialize_topic_archive_record(archive_record)
        return {
            "collection": TOPIC_ARCHIVE_COLLECTION_NAME,
            "id": serialized_record.get("id"),
            "record": serialized_record,
        }

    def _delete_topic_archive_records(self, record_ids):
        deleted_ids = self.selena.topic_archive_repository.delete_archives(record_ids)
        return {
            "collection": TOPIC_ARCHIVE_COLLECTION_NAME,
            "deleted_ids": deleted_ids,
            "deleted_count": len(deleted_ids),
        }

    def _create_collection_record(self, collection_name: str, mutation_payload):
        if self._is_atm_collection(collection_name):
            return self._create_atm_record(collection_name, mutation_payload)
        if self._is_topic_archive_collection(collection_name):
            return self._create_topic_archive_record(mutation_payload)
        if self._is_retrieval_cache_collection(collection_name):
            raise ValueError(f"Collection '{collection_name}' is read-only")
        if not isinstance(mutation_payload, dict):
            raise ValueError("request payload must be a JSON object")

        record_payload = mutation_payload.get("payload")
        if not isinstance(record_payload, dict):
            raise ValueError("payload must be a JSON object")

        sanitized_payload = dict(record_payload)
        sanitized_payload.pop("id", None)
        sanitized_payload.pop("__key", None)
        requested_id = mutation_payload.get("id")
        vector_input = mutation_payload.get("vector")
        auto_vectorize = bool(mutation_payload.get("auto_vectorize", False))
        vector_text = mutation_payload.get("vector_text")

        client = self._create_qdrant_client()
        try:
            if not client.collection_exists(collection_name):
                raise ValueError(f"Collection '{collection_name}' does not exist")

            info = client.get_collection(collection_name)
            vectors_config = info.config.params.vectors
            if vector_input is not None:
                vector = self._normalize_vector_input(vector_input, vectors_config)
                vector_generated = False
            else:
                if not auto_vectorize:
                    raise ValueError("vector is required for new records, or enable local embedding auto generation")
                vector = self._build_local_embedding_vector(
                    collection_name,
                    sanitized_payload,
                    vectors_config,
                    explicit_text=vector_text,
                )
                vector_generated = True

            if requested_id is None or (isinstance(requested_id, str) and not requested_id.strip()):
                point_id = self._get_next_point_id(client, collection_name)
            else:
                point_id = self._normalize_point_id(requested_id)
                existing_records = client.retrieve(
                    collection_name=collection_name,
                    ids=[point_id],
                    with_payload=False,
                    with_vectors=False,
                )
                if existing_records:
                    raise ValueError(f"Record '{point_id}' already exists in collection '{collection_name}'")

            client.upsert(
                collection_name=collection_name,
                points=[PointStruct(id=point_id, vector=vector, payload=sanitized_payload)],
            )
            return {
                "collection": collection_name,
                "id": point_id,
                "vector_generated": vector_generated,
                "record": {
                    **sanitized_payload,
                    "id": point_id,
                },
            }
        finally:
            client.close()

    def _update_collection_record(self, collection_name: str, record_id, mutation_payload):
        if self._is_atm_collection(collection_name):
            return self._update_atm_record(collection_name, record_id, mutation_payload)
        if self._is_topic_archive_collection(collection_name):
            return self._update_topic_archive_record(record_id, mutation_payload)
        if self._is_retrieval_cache_collection(collection_name):
            raise ValueError(f"Collection '{collection_name}' is read-only")
        if not isinstance(mutation_payload, dict):
            raise ValueError("request payload must be a JSON object")

        record_payload = mutation_payload.get("payload")
        if not isinstance(record_payload, dict):
            raise ValueError("payload must be a JSON object")

        normalized_id = self._normalize_point_id(record_id)
        sanitized_payload = dict(record_payload)
        sanitized_payload.pop("id", None)
        sanitized_payload.pop("__key", None)
        vector_input = mutation_payload.get("vector")
        auto_vectorize = bool(mutation_payload.get("auto_vectorize", False))
        vector_text = mutation_payload.get("vector_text")

        client = self._create_qdrant_client()
        try:
            if not client.collection_exists(collection_name):
                raise ValueError(f"Collection '{collection_name}' does not exist")

            current_records = client.retrieve(
                collection_name=collection_name,
                ids=[normalized_id],
                with_payload=True,
                with_vectors=True,
            )
            if not current_records:
                raise ValueError(f"Record '{normalized_id}' was not found in collection '{collection_name}'")

            current_record = current_records[0]
            info = client.get_collection(collection_name)
            vectors_config = info.config.params.vectors
            if vector_input is not None:
                vector = self._normalize_vector_input(vector_input, vectors_config)
                vector_generated = False
            elif auto_vectorize:
                vector = self._build_local_embedding_vector(
                    collection_name,
                    sanitized_payload,
                    vectors_config,
                    explicit_text=vector_text,
                )
                vector_generated = True
            else:
                vector = getattr(current_record, "vector", None)
                if vector is None:
                    raise ValueError("Existing vector could not be loaded. Provide vector JSON manually.")
                vector_generated = False

            client.upsert(
                collection_name=collection_name,
                points=[PointStruct(id=normalized_id, vector=vector, payload=sanitized_payload)],
            )
            return {
                "collection": collection_name,
                "id": normalized_id,
                "vector_generated": vector_generated,
                "record": {
                    **sanitized_payload,
                    "id": normalized_id,
                },
            }
        finally:
            client.close()

    def _delete_collection_records(self, collection_name: str, record_ids):
        if self._is_atm_collection(collection_name):
            return self._delete_atm_records(collection_name, record_ids)
        if self._is_topic_archive_collection(collection_name):
            return self._delete_topic_archive_records(record_ids)
        if self._is_retrieval_cache_collection(collection_name):
            raise ValueError(f"Collection '{collection_name}' is read-only")
        if not isinstance(record_ids, (list, tuple)) or not record_ids:
            raise ValueError("ids must be a non-empty array")

        normalized_ids = [self._normalize_point_id(record_id) for record_id in record_ids]
        client = self._create_qdrant_client()
        try:
            if not client.collection_exists(collection_name):
                raise ValueError(f"Collection '{collection_name}' does not exist")
            client.delete(
                collection_name=collection_name,
                points_selector=PointIdsList(points=normalized_ids),
            )
            return {
                "collection": collection_name,
                "deleted_ids": normalized_ids,
                "deleted_count": len(normalized_ids),
            }
        finally:
            client.close()

    def _list_collections(self):
        config_data = get_project_config()
        configured = config_data.get("Qdrant_Setting", {}).get("collections", {})
        retrieval_cache_exists = os.path.exists(str(RETRIEVAL_CACHE_DB_PATH))
        collections = [
            {
                "key": TOPIC_ARCHIVE_COLLECTION_NAME,
                "name": TOPIC_ARCHIVE_COLLECTION_NAME,
                "label": TOPIC_ARCHIVE_COLLECTION_LABEL,
                "storage_kind": "sqlite",
                "description": TOPIC_ARCHIVE_COLLECTION_DESCRIPTION,
                "vector_size": 0,
                "exists": True,
            },
            {
                "key": RETRIEVAL_CACHE_COLLECTION_NAME,
                "name": RETRIEVAL_CACHE_COLLECTION_NAME,
                "label": RETRIEVAL_CACHE_COLLECTION_LABEL,
                "storage_kind": "sqlite",
                "description": RETRIEVAL_CACHE_COLLECTION_DESCRIPTION,
                "vector_size": 0,
                "exists": retrieval_cache_exists,
            }
        ]
        atm_db_exists = os.path.exists(str(AUTONOMOUS_TASK_DB_PATH))
        for atm_key, atm_meta in ATM_COLLECTIONS.items():
            collections.append({
                "key": atm_key,
                "name": atm_key,
                "label": atm_meta["label"],
                "storage_kind": "sqlite",
                "description": atm_meta["description"],
                "vector_size": 0,
                "exists": atm_db_exists,
            })

        client = self._create_qdrant_client()
        try:
            existing_names = {
                collection.name
                for collection in getattr(client.get_collections(), "collections", [])
            }
        finally:
            client.close()

        for collection_key, collection_config in configured.items():
            name = str(collection_config.get("name", "")).strip()
            if not name:
                continue
            collections.append(
                {
                    "key": collection_key,
                    "name": name,
                    "storage_kind": "qdrant",
                    "vector_size": int(collection_config.get("vector_size", 0) or 0),
                    "exists": name in existing_names,
                }
            )

        existing_only = sorted(existing_names - {item["name"] for item in collections})
        for name in existing_only:
            collections.append(
                {
                    "key": name,
                    "name": name,
                    "storage_kind": "qdrant",
                    "vector_size": 0,
                    "exists": True,
                }
            )
        return collections

    @staticmethod
    def _is_atm_collection(collection_name: str) -> bool:
        return str(collection_name or "").strip() in ATM_COLLECTIONS

    @staticmethod
    def _is_retrieval_cache_collection(collection_name: str) -> bool:
        return str(collection_name or "").strip() == RETRIEVAL_CACHE_COLLECTION_NAME

    @staticmethod
    def _atm_not_found():
        return {
            "vector_kind": "single", "vector_size": 0,
            "vector_names": [], "named_vector_sizes": {},
        }

    @staticmethod
    def _open_atm_db():
        db_path = str(AUTONOMOUS_TASK_DB_PATH)
        if not os.path.exists(db_path):
            raise FileNotFoundError("ATM database does not exist")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _atm_row_to_record(row: dict, pk: str) -> dict:
        if pk != "id":
            row = {"id": row.get(pk), **row}
        return row

    @staticmethod
    def _get_atm_collection_payload(collection_name: str, *, limit: int = 200):
        meta = ATM_COLLECTIONS.get(collection_name)
        if meta is None:
            return {"name": collection_name, "exists": False, "total": 0, "records": []}
        base = {
            "name": collection_name,
            "label": meta["label"],
            "storage_kind": "sqlite",
            "description": meta["description"],
            "vector_kind": "single",
            "vector_size": 0,
            "vector_names": [],
            "named_vector_sizes": {},
        }
        db_path = str(AUTONOMOUS_TASK_DB_PATH)
        if not os.path.exists(db_path):
            return {**base, "exists": False, "total": 0, "records": []}
        normalized_limit = max(1, min(int(limit or 200), 1000))
        table = meta["table"]
        order = meta["order"]
        pk = meta["pk"]
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            total = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            raw_rows = [dict(r) for r in cur.execute(f"SELECT * FROM {table} ORDER BY {order} LIMIT ?", (normalized_limit,))]
            conn.close()
        except Exception:
            logger.exception("Failed to read ATM collection %s", collection_name)
            return {**base, "exists": False, "total": 0, "records": []}
        rows = [DialogueFrontendRuntime._atm_row_to_record(r, pk) for r in raw_rows]
        field_schema = []
        if raw_rows:
            for key in raw_rows[0]:
                if key == "id" or (pk != "id" and key == pk):
                    continue
                field_schema.append({"key": key, "sample": raw_rows[0][key]})
        return {**base, "exists": True, "total": total, "field_schema": field_schema, "records": rows}

    @staticmethod
    def _get_retrieval_cache_collection_payload(*, limit: int = 200):
        base = {
            "name": RETRIEVAL_CACHE_COLLECTION_NAME,
            "label": RETRIEVAL_CACHE_COLLECTION_LABEL,
            "storage_kind": "sqlite",
            "description": RETRIEVAL_CACHE_COLLECTION_DESCRIPTION,
            "readonly": True,
            "vector_kind": "single",
            "vector_size": 0,
            "vector_names": [],
            "named_vector_sizes": {},
        }
        db_path = str(RETRIEVAL_CACHE_DB_PATH)
        if not os.path.exists(db_path):
            return {**base, "exists": False, "total": 0, "records": []}
        normalized_limit = max(1, min(int(limit or 200), 1000))
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            total = cur.execute("SELECT COUNT(*) FROM retrieval_cache").fetchone()[0]
            raw_rows = [
                dict(row)
                for row in cur.execute(
                    "SELECT * FROM retrieval_cache ORDER BY id DESC LIMIT ?",
                    (normalized_limit,),
                )
            ]
            conn.close()
        except Exception:
            logger.exception("Failed to read retrieval cache SQLite collection")
            return {**base, "exists": False, "total": 0, "records": []}
        field_schema = []
        if raw_rows:
            for key in raw_rows[0]:
                if key == "id":
                    continue
                field_schema.append({"key": key, "sample": raw_rows[0][key]})
        return {
            **base,
            "exists": True,
            "total": total,
            "field_schema": field_schema,
            "records": raw_rows,
        }

    @staticmethod
    def _create_atm_record(collection_name: str, mutation_payload: dict):
        meta = ATM_COLLECTIONS[collection_name]
        table, pk = meta["table"], meta["pk"]
        payload = mutation_payload.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        record = dict(payload)
        requested_id = mutation_payload.get("id")
        if requested_id is not None and str(requested_id).strip():
            record[pk] = requested_id
        if pk != "id":
            record.pop("id", None)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for ts_col in ("created_at", "updated_at"):
            record.setdefault(ts_col, now)
        columns = list(record.keys())
        placeholders = ", ".join("?" for _ in columns)
        col_names = ", ".join(columns)
        conn = DialogueFrontendRuntime._open_atm_db()
        try:
            cur = conn.cursor()
            cur.execute(f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})", [record.get(c) for c in columns])
            conn.commit()
            if pk == "id":
                new_id = cur.lastrowid
            else:
                new_id = record.get(pk)
            row = dict(cur.execute(f"SELECT * FROM {table} WHERE {pk} = ?", (new_id,)).fetchone())
            conn.close()
        except Exception:
            conn.close()
            raise
        serialized = DialogueFrontendRuntime._atm_row_to_record(row, pk)
        return {"collection": collection_name, "id": serialized.get("id"), "record": serialized}

    @staticmethod
    def _update_atm_record(collection_name: str, record_id, mutation_payload: dict):
        meta = ATM_COLLECTIONS[collection_name]
        table, pk = meta["table"], meta["pk"]
        payload = mutation_payload.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        fields = dict(payload)
        fields.pop("id", None)
        if pk != "id":
            fields.pop(pk, None)
        fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_clause = ", ".join(f"{col} = ?" for col in fields)
        values = list(fields.values()) + [record_id]
        conn = DialogueFrontendRuntime._open_atm_db()
        try:
            cur = conn.cursor()
            cur.execute(f"UPDATE {table} SET {set_clause} WHERE {pk} = ?", values)
            conn.commit()
            row = cur.execute(f"SELECT * FROM {table} WHERE {pk} = ?", (record_id,)).fetchone()
            conn.close()
            if row is None:
                raise ValueError(f"Record '{record_id}' not found")
        except Exception:
            conn.close()
            raise
        serialized = DialogueFrontendRuntime._atm_row_to_record(dict(row), pk)
        return {"collection": collection_name, "id": serialized.get("id"), "record": serialized}

    @staticmethod
    def _delete_atm_records(collection_name: str, record_ids):
        meta = ATM_COLLECTIONS[collection_name]
        table, pk = meta["table"], meta["pk"]
        if not isinstance(record_ids, (list, tuple)) or not record_ids:
            raise ValueError("ids must be a non-empty array")
        placeholders = ", ".join("?" for _ in record_ids)
        conn = DialogueFrontendRuntime._open_atm_db()
        try:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM {table} WHERE {pk} IN ({placeholders})", list(record_ids))
            deleted = cur.rowcount
            conn.commit()
            conn.close()
        except Exception:
            conn.close()
            raise
        return {"collection": collection_name, "deleted_ids": list(record_ids), "deleted_count": deleted}

    def _get_collection_payload(self, collection_name: str, *, limit: int = 100):
        if self._is_topic_archive_collection(collection_name):
            return self._get_topic_archive_collection_payload(limit=limit)
        if self._is_atm_collection(collection_name):
            return self._get_atm_collection_payload(collection_name, limit=limit)
        if self._is_retrieval_cache_collection(collection_name):
            return self._get_retrieval_cache_collection_payload(limit=limit)
        normalized_limit = max(1, min(int(limit or 100), 500))
        client = self._create_qdrant_client()
        try:
            if not client.collection_exists(collection_name):
                return {
                    "name": collection_name,
                    "label": collection_name,
                    "storage_kind": "qdrant",
                    "exists": False,
                    "total": 0,
                    "vector_kind": "single",
                    "vector_size": 0,
                    "vector_names": [],
                    "named_vector_sizes": {},
                    "records": [],
                }

            info = client.get_collection(collection_name)
            vector_meta = self._describe_vectors(info.config.params.vectors)
            total = int(getattr(info, "points_count", 0) or 0)
            records, _ = client.scroll(
                collection_name=collection_name,
                limit=normalized_limit,
                with_payload=True,
                with_vectors=False,
            )
            serialized_records = [self._serialize_collection_record(record) for record in records]
            return {
                "name": collection_name,
                "label": collection_name,
                "storage_kind": "qdrant",
                "exists": True,
                "total": total,
                **vector_meta,
                "records": serialized_records,
            }
        finally:
            client.close()

    def _call_runtime_tool(self, function_name: str, **kwargs):
        try:
            tool_call = {
                "id": f"frontend-debug-{function_name}",
                "type": "function",
                "function": {
                    "name": function_name,
                    "arguments": json.dumps(kwargs, ensure_ascii=False),
                },
            }
            return self.selena.execute_tool_call(tool_call)
        except Exception as error:
            logger.exception("Runtime debug tool call failed | tool=%s", function_name)
            return {"ok": False, "error": str(error)}

    def _get_browser_status_payload(self):
        tool_definitions = list(getattr(self.selena, "tools", []) or [])
        tool_definitions.extend(self.selena.dynamic_tool_registry.list_tool_definitions())
        browser_tool_names = {
            "browserNavigate",
            "browserSearch",
            "browserSnapshot",
            "browserClick",
            "browserType",
            "browserScroll",
            "browserGoBack",
            "browserExtractPage",
            "browserOpenTab",
            "browserReadLinkedPage",
            "browserListTabs",
            "browserSelectTab",
            "browserCloseTab",
            "browserWait",
            "browserPressKey",
            "browserScreenshot",
        }
        available_tools = []
        for definition in tool_definitions:
            function_meta = definition.get("function") or {}
            function_name = str(function_meta.get("name") or "").strip()
            if function_name in browser_tool_names:
                available_tools.append(
                    {
                        "name": function_name,
                        "description": str(function_meta.get("description") or "").strip(),
                        "parameters": dict((function_meta.get("parameters") or {}).get("properties") or {}),
                    }
                )

        controller = getattr(self.selena, "_chrome_browser_controller", None)
        current_page = {}
        if controller is not None:
            try:
                current_page = controller.peek_page(max_text_length=800) or {}
            except Exception as error:
                current_page = {"error": str(error)}

        return {
            "ok": True,
            "tool_count": len(available_tools),
            "tools": available_tools,
            "controller_initialized": controller is not None,
            "current_page": current_page,
        }

    @staticmethod
    def _utc_timestamp():
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _run_git_command(self, args, *, cwd: str = "", allow_fail: bool = False):
        command = [str(item) for item in list(args or []) if str(item or "").strip()]
        if not command:
            raise ValueError("git command is required")
        completed = subprocess.run(
            command,
            cwd=str(cwd or PROJECT_ROOT).strip() or PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0 and not allow_fail:
            error_text = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
            raise RuntimeError(error_text)
        return completed

    def _get_git_repo_root(self) -> str:
        completed = self._run_git_command(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=PROJECT_ROOT,
            allow_fail=True,
        )
        if completed.returncode != 0:
            return ""
        return os.path.abspath(str(completed.stdout or "").strip())

    @staticmethod
    def _empty_diff_stat():
        return {
            "file_count": 0,
            "additions": 0,
            "deletions": 0,
            "files": [],
        }

    def _empty_git_payload(self, error: str = "") -> dict:
        return {
            "ok": True,
            "available": False,
            "root": "",
            "current_path": "",
            "branch": "",
            "head": "",
            "detached": False,
            "upstream": "",
            "ahead": 0,
            "behind": 0,
            "dirty": False,
            "staged_count": 0,
            "unstaged_count": 0,
            "untracked_count": 0,
            "changed_files": [],
            "staged_diff": self._empty_diff_stat(),
            "unstaged_diff": self._empty_diff_stat(),
            "worktrees": [],
            "default_worktree_root": "",
            "error": str(error or "").strip(),
        }

    @staticmethod
    def _parse_git_status_lines(status_text: str):
        branch = ""
        upstream = ""
        ahead = 0
        behind = 0
        detached = False
        changed_files = []

        for raw_line in str(status_text or "").splitlines():
            line = raw_line.rstrip("\n")
            if not line:
                continue
            if line.startswith("## "):
                header = line[3:].strip()
                if header.startswith("HEAD"):
                    detached = True
                status_match = re.search(r"\[(.*?)\]\s*$", header)
                if status_match:
                    details = status_match.group(1)
                    ahead_match = re.search(r"ahead (\d+)", details)
                    behind_match = re.search(r"behind (\d+)", details)
                    ahead = int(ahead_match.group(1)) if ahead_match else 0
                    behind = int(behind_match.group(1)) if behind_match else 0
                    header = header[: status_match.start()].strip()
                if "..." in header:
                    branch, upstream = [part.strip() for part in header.split("...", 1)]
                else:
                    branch = header.strip()
                if branch.startswith("HEAD"):
                    detached = True
                    branch = ""
                continue

            if len(line) < 3:
                continue
            index_status = line[0]
            worktree_status = line[1]
            display_path = line[3:].strip()
            normalized_path = display_path.split(" -> ", 1)[-1].strip().strip('"')
            changed_files.append(
                {
                    "path": normalized_path.replace("\\", "/"),
                    "display_path": display_path.replace("\\", "/"),
                    "index_status": index_status,
                    "worktree_status": worktree_status,
                    "staged": index_status not in {" ", "?"},
                    "unstaged": worktree_status not in {" "},
                    "untracked": index_status == "?" or worktree_status == "?",
                    "renamed": " -> " in display_path,
                }
            )

        return {
            "branch": branch,
            "upstream": upstream,
            "ahead": ahead,
            "behind": behind,
            "detached": detached,
            "changed_files": changed_files,
        }

    @staticmethod
    def _parse_diff_numstat(diff_text: str) -> dict:
        files = []
        total_additions = 0
        total_deletions = 0
        for raw_line in str(diff_text or "").splitlines():
            parts = raw_line.split("\t")
            if len(parts) < 3:
                continue
            additions_text, deletions_text, path_text = parts[0], parts[1], parts[2]
            additions = 0 if additions_text == "-" else int(additions_text or 0)
            deletions = 0 if deletions_text == "-" else int(deletions_text or 0)
            normalized_path = str(path_text or "").strip().replace("\\", "/")
            files.append(
                {
                    "path": normalized_path,
                    "additions": additions,
                    "deletions": deletions,
                    "changes": additions + deletions,
                }
            )
            total_additions += additions
            total_deletions += deletions
        return {
            "file_count": len(files),
            "additions": total_additions,
            "deletions": total_deletions,
            "files": files,
        }

    @staticmethod
    def _parse_worktree_list(worktree_text: str, *, current_root: str = "") -> list[dict]:
        worktrees = []
        current = {}
        normalized_current_root = os.path.abspath(str(current_root or "").strip()) if str(current_root or "").strip() else ""

        def flush_current():
            nonlocal current
            if not current:
                return
            path_value = os.path.abspath(str(current.get("path", "") or "").strip())
            branch_ref = str(current.get("branch_ref", "") or "").strip()
            branch_name = branch_ref.replace("refs/heads/", "") if branch_ref.startswith("refs/heads/") else branch_ref
            worktrees.append(
                {
                    "path": path_value,
                    "branch": branch_name,
                    "branch_ref": branch_ref,
                    "head": str(current.get("head", "") or "").strip(),
                    "is_current": bool(normalized_current_root and path_value == normalized_current_root),
                    "is_detached": bool(current.get("detached", False)),
                    "is_prunable": bool(current.get("prunable", False)),
                    "locked": bool(current.get("locked", False)),
                    "bare": bool(current.get("bare", False)),
                }
            )
            current = {}

        for raw_line in list(str(worktree_text or "").splitlines()) + [""]:
            line = raw_line.strip()
            if not line:
                flush_current()
                continue
            if line.startswith("worktree "):
                current["path"] = raw_line[len("worktree ") :].strip()
            elif line.startswith("HEAD "):
                current["head"] = raw_line[len("HEAD ") :].strip()
            elif line.startswith("branch "):
                current["branch_ref"] = raw_line[len("branch ") :].strip()
            elif line == "detached":
                current["detached"] = True
            elif line.startswith("prunable"):
                current["prunable"] = True
            elif line.startswith("locked"):
                current["locked"] = True
            elif line == "bare":
                current["bare"] = True

        worktrees.sort(
            key=lambda item: (
                0 if item.get("is_current") else 1,
                str(item.get("branch") or ""),
                str(item.get("path") or ""),
            )
        )
        return worktrees

    @staticmethod
    def _count_subagent_statuses(tasks: list[dict]) -> dict:
        counts = {
            "active_count": 0,
            "queued_count": 0,
            "waiting_count": 0,
            "completed_count": 0,
            "failed_count": 0,
            "cancelled_count": 0,
            "cache_hit_count": 0,
        }
        for task in list(tasks or []):
            status = str((task or {}).get("status", "")).strip().lower()
            if status == "running":
                counts["active_count"] += 1
            elif status == "queued":
                counts["queued_count"] += 1
            elif status in {"waiting_input", "waiting_approval"}:
                counts["waiting_count"] += 1
            elif status == "completed":
                counts["completed_count"] += 1
            elif status == "failed":
                counts["failed_count"] += 1
            elif status in {"cancelled", "timed_out"}:
                counts["cancelled_count"] += 1
            if bool((task or {}).get("cache_hit", False)):
                counts["cache_hit_count"] += 1
        return counts

    def _build_llm_summary_payload(self) -> dict:
        logs = list(get_llm_call_logs(0) or [])
        completed_calls = 0
        failed_calls = 0
        running_calls = 0
        total_duration_ms = 0
        model_names = set()
        caller_counts = {}
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        usage_tracked_calls = 0
        missing_usage_calls = 0
        known_cost_total = 0.0
        known_cost_calls = 0

        for entry in logs:
            status = str(entry.get("status", "") or "").strip().lower()
            if status == "completed":
                completed_calls += 1
            elif status == "failed":
                failed_calls += 1
            elif status == "running":
                running_calls += 1
            try:
                total_duration_ms += int(entry.get("duration_ms") or 0)
            except (TypeError, ValueError):
                pass

            model_name = str(entry.get("model_name") or entry.get("model_key") or "").strip()
            if model_name:
                model_names.add(model_name)

            caller_name = str(entry.get("caller") or "unknown").strip() or "unknown"
            caller_counts[caller_name] = int(caller_counts.get(caller_name, 0) or 0) + 1

            extra = dict(entry.get("extra") or {})
            usage = dict(extra.get("usage") or {})
            if usage:
                prompt_tokens += int(usage.get("prompt_tokens") or 0)
                completion_tokens += int(usage.get("completion_tokens") or 0)
                total_tokens += int(usage.get("total_tokens") or 0)
                usage_tracked_calls += 1
            elif status != "running":
                missing_usage_calls += 1

            cost_value = extra.get("cost_usd")
            try:
                if cost_value not in (None, ""):
                    known_cost_total += float(cost_value)
                    known_cost_calls += 1
            except (TypeError, ValueError):
                pass

        average_duration_ms = round(total_duration_ms / completed_calls) if completed_calls else 0
        caller_rows = [
            {"name": caller_name, "count": count}
            for caller_name, count in sorted(caller_counts.items(), key=lambda item: (-item[1], item[0]))
        ]
        return {
            "total_calls": len(logs),
            "completed_calls": completed_calls,
            "failed_calls": failed_calls,
            "running_calls": running_calls,
            "total_duration_ms": total_duration_ms,
            "average_duration_ms": average_duration_ms,
            "unique_models": sorted(model_names),
            "callers": caller_rows,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "tracked_calls": usage_tracked_calls,
                "missing_usage_calls": missing_usage_calls,
            },
            "cost_estimate": {
                "available": known_cost_calls > 0,
                "amount_usd": round(known_cost_total, 6) if known_cost_calls > 0 else 0.0,
                "reason": "" if known_cost_calls > 0 else "No per-call cost metadata is being recorded yet.",
            },
            "recent_calls": list(reversed(logs[-8:])),
        }

    def _get_git_status_payload(self) -> dict:
        repo_root = self._get_git_repo_root()
        if not repo_root:
            return self._empty_git_payload("Current workspace is not inside a git repository.")

        try:
            status_result = self._run_git_command(["git", "status", "--short", "--branch"], cwd=repo_root)
            head_result = self._run_git_command(["git", "rev-parse", "HEAD"], cwd=repo_root)
            staged_diff_result = self._run_git_command(["git", "diff", "--cached", "--numstat"], cwd=repo_root)
            unstaged_diff_result = self._run_git_command(["git", "diff", "--numstat"], cwd=repo_root)
            worktree_result = self._run_git_command(["git", "worktree", "list", "--porcelain"], cwd=repo_root)
        except Exception as error:
            return self._empty_git_payload(str(error))

        status_meta = self._parse_git_status_lines(status_result.stdout)
        worktrees = self._parse_worktree_list(worktree_result.stdout, current_root=repo_root)
        changed_files = list(status_meta.get("changed_files") or [])
        return {
            "ok": True,
            "available": True,
            "root": repo_root,
            "current_path": repo_root,
            "branch": str(status_meta.get("branch") or "").strip(),
            "head": str(head_result.stdout or "").strip(),
            "detached": bool(status_meta.get("detached", False)),
            "upstream": str(status_meta.get("upstream") or "").strip(),
            "ahead": int(status_meta.get("ahead") or 0),
            "behind": int(status_meta.get("behind") or 0),
            "dirty": bool(changed_files),
            "staged_count": sum(1 for item in changed_files if item.get("staged")),
            "unstaged_count": sum(1 for item in changed_files if item.get("unstaged")),
            "untracked_count": sum(1 for item in changed_files if item.get("untracked")),
            "changed_files": changed_files,
            "staged_diff": self._parse_diff_numstat(staged_diff_result.stdout),
            "unstaged_diff": self._parse_diff_numstat(unstaged_diff_result.stdout),
            "worktrees": worktrees,
            "default_worktree_root": os.path.join(repo_root, ".worktrees"),
            "error": "",
        }

    def _get_workbench_payload(self) -> dict:
        runtime_state = self.selena.export_runtime_state()
        subagent_payload = self.selena.listDelegatedTasks(IncludeCompleted=True)
        tasks = list(subagent_payload.get("tasks") or [])
        subagent_counts = self._count_subagent_statuses(tasks)
        return {
            "ok": True,
            "generated_at": self._utc_timestamp(),
            "runtime": runtime_state,
            "subagents": {
                "count": len(tasks),
                **subagent_counts,
                "tasks": tasks,
            },
            "git": self._get_git_status_payload(),
            "llm": self._build_llm_summary_payload(),
        }

    @staticmethod
    def _sanitize_worktree_name(value: str) -> str:
        safe_chars = []
        for char in str(value or "").strip():
            if char.isalnum() or char in {"-", "_", "."}:
                safe_chars.append(char)
            else:
                safe_chars.append("-")
        safe_name = "".join(safe_chars).strip(".-")
        return safe_name or "worktree"

    def _resolve_managed_worktree_path(self, repo_root: str, *, branch: str = "", path: str = "") -> str:
        managed_root = os.path.join(repo_root, ".worktrees")
        raw_path = str(path or "").strip()
        if raw_path:
            target_path = os.path.abspath(raw_path if os.path.isabs(raw_path) else os.path.join(repo_root, raw_path))
        else:
            target_path = os.path.join(managed_root, self._sanitize_worktree_name(branch))

        normalized_managed_root = os.path.abspath(managed_root)
        normalized_target_path = os.path.abspath(target_path)
        try:
            common_path = os.path.commonpath([normalized_target_path, normalized_managed_root])
        except ValueError as error:
            raise ValueError("Worktree path must stay under the managed .worktrees directory.") from error
        if common_path != normalized_managed_root:
            raise ValueError("Worktree path must stay under the managed .worktrees directory.")
        return normalized_target_path

    @staticmethod
    def _find_worktree_entry(worktrees: list[dict], target_path: str) -> dict | None:
        normalized_target = os.path.abspath(str(target_path or "").strip())
        for worktree in list(worktrees or []):
            candidate_path = os.path.abspath(str((worktree or {}).get("path", "") or "").strip())
            if candidate_path == normalized_target:
                return dict(worktree)
        return None

    def _get_git_diff_preview(self, path: str, *, staged: bool = False, max_chars: int = 40000) -> dict:
        git_payload = self._get_git_status_payload()
        if not git_payload.get("available", False):
            return {
                "ok": True,
                "path": str(path or "").strip(),
                "staged": bool(staged),
                "patch": "",
                "truncated": False,
                "generated_at": self._utc_timestamp(),
                "error": git_payload.get("error", ""),
            }

        repo_root = str(git_payload.get("root") or "").strip()
        normalized_path = str(path or "").strip().replace("\\", "/")
        diff_args = ["git", "diff"]
        if staged:
            diff_args.append("--cached")
        if normalized_path:
            diff_args.extend(["--", normalized_path])
        result = self._run_git_command(diff_args, cwd=repo_root)
        patch_text = str(result.stdout or "")
        truncated = len(patch_text) > int(max_chars or 40000)
        if truncated:
            patch_text = patch_text[: int(max_chars or 40000)]
        return {
            "ok": True,
            "path": normalized_path,
            "staged": bool(staged),
            "patch": patch_text,
            "truncated": truncated,
            "generated_at": self._utc_timestamp(),
            "error": "",
        }

    def _create_worktree(self, *, branch: str, base_ref: str = "", path: str = "") -> dict:
        repo_root = self._get_git_repo_root()
        if not repo_root:
            raise ValueError("Current workspace is not inside a git repository.")

        normalized_branch = str(branch or "").strip()
        if not normalized_branch:
            raise ValueError("branch is required")

        target_path = self._resolve_managed_worktree_path(repo_root, branch=normalized_branch, path=path)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        branch_check = self._run_git_command(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{normalized_branch}"],
            cwd=repo_root,
            allow_fail=True,
        )
        branch_exists = branch_check.returncode == 0
        if branch_exists:
            command = ["git", "worktree", "add", target_path, normalized_branch]
        else:
            normalized_base_ref = str(base_ref or "").strip() or "HEAD"
            command = ["git", "worktree", "add", "-b", normalized_branch, target_path, normalized_base_ref]
        self._run_git_command(command, cwd=repo_root)

        git_payload = self._get_git_status_payload()
        worktree_entry = self._find_worktree_entry(git_payload.get("worktrees") or [], target_path)
        return {
            "ok": True,
            "message": f"Created worktree {normalized_branch}.",
            "worktree": worktree_entry,
            "git": git_payload,
        }

    def _remove_worktree(self, path: str) -> dict:
        repo_root = self._get_git_repo_root()
        if not repo_root:
            raise ValueError("Current workspace is not inside a git repository.")
        if not str(path or "").strip():
            raise ValueError("path is required")

        target_path = self._resolve_managed_worktree_path(repo_root, path=path)
        if os.path.abspath(target_path) == os.path.abspath(repo_root):
            raise ValueError("The current primary worktree cannot be removed.")
        self._run_git_command(["git", "worktree", "remove", target_path], cwd=repo_root)
        self._run_git_command(["git", "worktree", "prune"], cwd=repo_root, allow_fail=True)
        return {
            "ok": True,
            "message": f"Removed worktree {target_path}.",
            "git": self._get_git_status_payload(),
        }
