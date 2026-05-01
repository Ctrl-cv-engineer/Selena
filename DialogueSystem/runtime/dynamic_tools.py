"""Dynamic tool registration helpers for DialogueSystem."""

from __future__ import annotations

import copy
import logging
import threading
from collections import OrderedDict


logger = logging.getLogger(__name__)


def build_function_tool_definition(
    name: str,
    description: str,
    properties: dict | None = None,
    *,
    required: list | None = None,
    additional_properties: bool = False,
) -> dict:
    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise ValueError("Tool name is required.")
    return {
        "type": "function",
        "function": {
            "name": normalized_name,
            "description": str(description or "").strip(),
            "parameters": {
                "type": "object",
                "properties": dict(properties or {}),
                "required": list(required or []),
                "additionalProperties": bool(additional_properties),
            },
        },
    }


class DynamicToolRegistry:
    """Thread-safe registry for runtime-defined tool metadata and handlers."""

    def __init__(self):
        self._lock = threading.RLock()
        self._tool_definitions = OrderedDict()
        self._handlers = {}
        self._skill_map = {}
        self._sources = {}

    def clear(self, *, source_prefix: str | None = None):
        with self._lock:
            if not source_prefix:
                self._tool_definitions.clear()
                self._handlers.clear()
                self._skill_map.clear()
                self._sources.clear()
                return
            doomed_names = [
                name
                for name, source in self._sources.items()
                if str(source or "").startswith(str(source_prefix))
            ]
            for name in doomed_names:
                self._tool_definitions.pop(name, None)
                self._handlers.pop(name, None)
                self._skill_map.pop(name, None)
                self._sources.pop(name, None)

    def register(self, tool_definition: dict, handler, *, source: str, skill_name: str = ""):
        function_name = str((tool_definition.get("function") or {}).get("name", "")).strip()
        if not function_name:
            raise ValueError("Dynamic tool definition is missing function.name")
        if not callable(handler):
            raise TypeError(f"Handler for dynamic tool {function_name} must be callable")
        with self._lock:
            self._tool_definitions[function_name] = copy.deepcopy(tool_definition)
            self._handlers[function_name] = handler
            self._sources[function_name] = str(source or "").strip()
            normalized_skill_name = str(skill_name or "").strip()
            if normalized_skill_name:
                self._skill_map[function_name] = normalized_skill_name
            elif function_name in self._skill_map:
                self._skill_map.pop(function_name, None)
        logger.info(
            "Dynamic tool registered | name=%s | source=%s | skill=%s",
            function_name,
            source,
            skill_name,
        )

    def has(self, function_name: str) -> bool:
        with self._lock:
            return str(function_name or "").strip() in self._handlers

    def execute(self, function_name: str, arguments: dict):
        normalized_name = str(function_name or "").strip()
        with self._lock:
            handler = self._handlers.get(normalized_name)
        if handler is None:
            raise KeyError(normalized_name)
        return handler(**dict(arguments or {}))

    def list_tool_definitions(self) -> list:
        with self._lock:
            return [copy.deepcopy(item) for item in self._tool_definitions.values()]

    def get_tool_skill_map(self) -> dict:
        with self._lock:
            return dict(self._skill_map)
