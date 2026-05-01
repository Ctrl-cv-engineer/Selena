"""Runtime MCP client and dynamic tool bridge for DialogueSystem."""

from __future__ import annotations

import copy
import json
import logging
import threading

import requests

try:
    from .dynamic_tools import build_function_tool_definition
except ImportError:
    from DialogueSystem.runtime.dynamic_tools import build_function_tool_definition


logger = logging.getLogger(__name__)


class MCPRuntimeError(RuntimeError):
    """Raised when MCP configuration or requests fail."""


class MCPRuntime:
    """Discover MCP tools and expose them as DialogueSystem dynamic tools."""

    def __init__(self, owner):
        self.owner = owner
        self._session = requests.Session()
        self._lock = threading.RLock()
        self._tool_specs = {}

    def _get_config(self) -> dict:
        config = dict((self.owner.config or {}).get("MCP", {}))
        config["enabled"] = bool(config.get("enabled", False))
        config["servers"] = list(config.get("servers") or [])
        return config

    def _iter_enabled_servers(self):
        config = self._get_config()
        if not config.get("enabled"):
            return []
        enabled_servers = []
        for raw_server in config.get("servers", []):
            if not isinstance(raw_server, dict):
                continue
            if not raw_server.get("enabled", True):
                continue
            normalized = dict(raw_server)
            normalized["name"] = str(raw_server.get("name") or "").strip()
            normalized["url"] = str(raw_server.get("url") or "").strip()
            normalized["auth_token"] = str(raw_server.get("auth_token") or "").strip()
            if not normalized["name"] or not normalized["url"]:
                continue
            enabled_servers.append(normalized)
        return enabled_servers

    @staticmethod
    def _build_headers(server_config: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        auth_token = str(server_config.get("auth_token") or "").strip()
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        return headers

    def _rpc(self, server_config: dict, method: str, params: dict | None = None) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": "dialogue-system",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        response = self._session.post(
            server_config["url"],
            headers=self._build_headers(server_config),
            data=json.dumps(payload, ensure_ascii=False),
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data and data["error"]:
            raise MCPRuntimeError(
                f"MCP server '{server_config['name']}' returned error for {method}: {data['error']}"
            )
        return data

    @staticmethod
    def _normalize_server_tool_name(server_name: str, tool_name: str) -> str:
        safe_server_name = "".join(
            ch if ch.isalnum() or ch == "_" else "_"
            for ch in str(server_name or "").strip().lower()
        ).strip("_")
        safe_tool_name = "".join(
            ch if ch.isalnum() or ch == "_" else "_"
            for ch in str(tool_name or "").strip()
        ).strip("_")
        if not safe_server_name:
            safe_server_name = "mcp"
        if not safe_tool_name:
            raise ValueError("MCP tool name is required.")
        return f"mcp_{safe_server_name}_{safe_tool_name}"

    @staticmethod
    def _build_definition(server_name: str, tool_payload: dict, function_name: str) -> dict:
        input_schema = copy.deepcopy(tool_payload.get("inputSchema") or {})
        properties = dict(input_schema.get("properties") or {})
        required = list(input_schema.get("required") or [])
        additional_properties = bool(input_schema.get("additionalProperties", False))
        description = str(tool_payload.get("description") or "").strip()
        if not description:
            description = f"Call MCP tool '{tool_payload.get('name', function_name)}' from server '{server_name}'."
        return build_function_tool_definition(
            function_name,
            description,
            properties,
            required=required,
            additional_properties=additional_properties,
        )

    def refresh_dynamic_tools(self, dynamic_registry):
        with self._lock:
            self._tool_specs = {}
            dynamic_registry.clear(source_prefix="mcp:")
            for server_config in self._iter_enabled_servers():
                server_name = server_config["name"]
                try:
                    tool_list_response = self._rpc(server_config, "tools/list")
                except Exception as error:
                    logger.exception("Failed to list MCP tools | server=%s | error=%s", server_name, error)
                    continue
                tools = ((tool_list_response.get("result") or {}).get("tools") or [])
                for tool_payload in tools:
                    raw_tool_name = str(tool_payload.get("name") or "").strip()
                    if not raw_tool_name:
                        continue
                    function_name = self._normalize_server_tool_name(server_name, raw_tool_name)
                    definition = self._build_definition(server_name, tool_payload, function_name)
                    spec = {
                        "server_name": server_name,
                        "server_url": server_config["url"],
                        "tool_name": raw_tool_name,
                        "tool_definition": definition,
                    }
                    self._tool_specs[function_name] = spec
                    dynamic_registry.register(
                        definition,
                        self._build_handler(server_config, raw_tool_name, function_name),
                        source=f"mcp:{server_name}",
                        skill_name="mcp_tools",
                    )
            return len(self._tool_specs)

    def _build_handler(self, server_config: dict, raw_tool_name: str, function_name: str):
        def _handler(**kwargs):
            try:
                result = self._rpc(
                    server_config,
                    "tools/call",
                    {"name": raw_tool_name, "arguments": dict(kwargs or {})},
                )
                return {
                    "ok": True,
                    "tool_name": function_name,
                    "mcp_server": server_config["name"],
                    "mcp_tool_name": raw_tool_name,
                    "result": result.get("result"),
                }
            except Exception as error:
                logger.exception(
                    "MCP tool call failed | server=%s | tool=%s | error=%s",
                    server_config["name"],
                    raw_tool_name,
                    error,
                )
                return {
                    "ok": False,
                    "tool_name": function_name,
                    "mcp_server": server_config["name"],
                    "mcp_tool_name": raw_tool_name,
                    "error": str(error),
                }

        return _handler

    def list_tools(self) -> list:
        with self._lock:
            return [copy.deepcopy(item) for item in self._tool_specs.values()]

    def close(self):
        try:
            self._session.close()
        except Exception:
            logger.exception("Failed to close MCP runtime session")
