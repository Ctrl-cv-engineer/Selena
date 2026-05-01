"""Load agent definitions from declarative Markdown files with YAML frontmatter.

Agent files live in ``DialogueSystem/agents/*.md`` (built-in) and optionally in
a user-configured directory.  Each file has the structure::

    ---
    name: explore
    description: Fast code exploration specialist ...
    max_tool_calls: 8
    toolsets:
      - core
    resource_limits:
      max_file_reads: 100
      max_file_writes: 0
      max_network_calls: 0
    ---

    You are a code exploration specialist ...

The YAML block declares capabilities & limits; the body (after the second
``---``) is the system instruction injected into the sub-agent context.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Dict, Optional

try:
    import yaml  # PyYAML
except ImportError:
    yaml = None

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n?(.*)", re.DOTALL)

_BUILTIN_DIR = Path(__file__).resolve().parents[1] / "agents"

DEFAULT_AGENT_DEFINITION = {
    "name": "general",
    "description": "General purpose agent",
    "system_prompt": "Agent",
    "max_tool_calls": 6,
    "toolsets": ["core", "memory", "browser", "schedule"],
    "allowed_tools": [],
    "disallowed_tools": [],
    "resource_limits": {
        "max_file_reads": 50,
        "max_file_writes": 0,
        "max_network_calls": 10,
    },
    "instruction": (
        "You are a delegated sub-agent. "
        "Solve only the assigned subtask, stay concise, and use tools when needed. "
        "When finished, summarize concrete findings or outputs."
    ),
}


def _parse_yaml_frontmatter(text: str) -> tuple[dict, str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text.strip()
    raw_yaml, body = match.group(1), match.group(2)
    if yaml is not None:
        meta = yaml.safe_load(raw_yaml) or {}
    else:
        meta = _fallback_parse_yaml(raw_yaml)
    return meta, body.strip()


def _fallback_parse_yaml(raw: str) -> dict:
    """Minimal key-value parser when PyYAML is unavailable."""
    result: dict = {}
    current_key = ""
    current_list: list | None = None
    current_dict: dict | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- ") and current_key:
            if current_list is None:
                current_list = []
                result[current_key] = current_list
            current_list.append(stripped[2:].strip())
            continue

        indent = len(line) - len(line.lstrip())
        if indent >= 2 and ":" in stripped and current_key:
            if current_dict is None:
                current_dict = {}
                result[current_key] = current_dict
            k, _, v = stripped.partition(":")
            v = v.strip()
            if v.isdigit():
                v = int(v)
            current_dict[k.strip()] = v
            continue

        if ":" in stripped:
            current_list = None
            current_dict = None
            k, _, v = stripped.partition(":")
            current_key = k.strip()
            v = v.strip()
            if v:
                if v.isdigit():
                    v = int(v)
                elif v.lower() in ("true", "false"):
                    v = v.lower() == "true"
                result[current_key] = v
            continue

    return result


def _normalize_definition(meta: dict, instruction: str) -> dict:
    """Ensure all required fields exist with proper types."""
    toolsets_raw = meta.get("toolsets") or DEFAULT_AGENT_DEFINITION["toolsets"]
    if isinstance(toolsets_raw, str):
        toolsets_raw = [s.strip() for s in toolsets_raw.split(",") if s.strip()]

    resource_limits_raw = meta.get("resource_limits") or {}
    default_rl = DEFAULT_AGENT_DEFINITION["resource_limits"]
    resource_limits = {
        "max_file_reads": int(resource_limits_raw.get("max_file_reads", default_rl["max_file_reads"])),
        "max_file_writes": int(resource_limits_raw.get("max_file_writes", default_rl["max_file_writes"])),
        "max_network_calls": int(resource_limits_raw.get("max_network_calls", default_rl["max_network_calls"])),
    }

    def _normalize_tool_list(raw) -> list[str]:
        if not raw:
            return []
        if isinstance(raw, str):
            return [s.strip() for s in raw.split(",") if s.strip()]
        return [str(s).strip() for s in raw if str(s).strip()]

    return {
        "name": str(meta.get("name") or "general").strip().lower(),
        "description": str(meta.get("description") or DEFAULT_AGENT_DEFINITION["description"]),
        "system_prompt": str(
            meta.get("system_prompt")
            or meta.get("prompt_name")
            or DEFAULT_AGENT_DEFINITION["system_prompt"]
        ).strip() or DEFAULT_AGENT_DEFINITION["system_prompt"],
        "max_tool_calls": int(meta.get("max_tool_calls") or DEFAULT_AGENT_DEFINITION["max_tool_calls"]),
        "toolsets": list(toolsets_raw),
        "allowed_tools": _normalize_tool_list(meta.get("allowed_tools")),
        "disallowed_tools": _normalize_tool_list(meta.get("disallowed_tools")),
        "resource_limits": resource_limits,
        "instruction": instruction or DEFAULT_AGENT_DEFINITION["instruction"],
    }


def load_agent_file(path: str | Path) -> dict:
    """Parse a single agent ``.md`` file and return a normalized definition dict."""
    text = Path(path).read_text(encoding="utf-8")
    meta, instruction = _parse_yaml_frontmatter(text)
    return _normalize_definition(meta, instruction)


class AgentRegistry:
    """Registry that loads built-in and user-defined agent definitions."""

    def __init__(self, extra_dirs: list[str | Path] | None = None):
        self._definitions: Dict[str, dict] = {}
        self._load_directory(_BUILTIN_DIR)
        for d in extra_dirs or []:
            self._load_directory(Path(d))

    def _load_directory(self, directory: Path):
        if not directory.is_dir():
            return
        for filepath in sorted(directory.glob("*.md")):
            try:
                defn = load_agent_file(filepath)
                name = defn["name"]
                if name in self._definitions:
                    logger.info("Agent definition overridden | name=%s source=%s", name, filepath)
                self._definitions[name] = defn
            except Exception:
                logger.exception("Failed to load agent definition | path=%s", filepath)

    def reload(self, extra_dirs: list[str | Path] | None = None):
        """Clear and re-load all definitions."""
        self._definitions.clear()
        self._load_directory(_BUILTIN_DIR)
        for d in extra_dirs or []:
            self._load_directory(Path(d))

    def get(self, name: str) -> dict:
        normalized = str(name or "general").strip().lower()
        return dict(self._definitions.get(normalized) or self._definitions.get("general") or DEFAULT_AGENT_DEFINITION)

    def list_names(self) -> list[str]:
        return sorted(self._definitions.keys())

    def list_all(self) -> list[dict]:
        return [dict(d) for d in self._definitions.values()]

    def get_instruction(self, name: str) -> str:
        return self.get(name).get("instruction", DEFAULT_AGENT_DEFINITION["instruction"])

    def get_max_tool_calls(self, name: str) -> int:
        return self.get(name).get("max_tool_calls", DEFAULT_AGENT_DEFINITION["max_tool_calls"])

    def get_toolsets(self, name: str) -> list[str]:
        return list(self.get(name).get("toolsets", DEFAULT_AGENT_DEFINITION["toolsets"]))

    def get_resource_limits(self, name: str) -> dict:
        return dict(self.get(name).get("resource_limits", DEFAULT_AGENT_DEFINITION["resource_limits"]))
