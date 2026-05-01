"""Centralized tool policy engine for DialogueSystem."""

from __future__ import annotations

import os

try:
    from .tool_metadata import get_tool_metadata
    from ..security.prompt_injection import scan_text
except ImportError:
    from DialogueSystem.policy.tool_metadata import get_tool_metadata
    from DialogueSystem.security.prompt_injection import scan_text


class ToolPolicyEngine:
    """Decide whether a tool call is allowed, blocked, or needs approval."""

    def __init__(self, owner):
        self.owner = owner

    def build_session_context(self) -> dict:
        security_config = dict((self.owner.config or {}).get("Security", {}))
        toolsets = security_config.get("enabled_toolsets") or [
            "core",
            "memory",
            "schedule",
            "browser",
            "file_read",
            "file_write",
            "terminal",
            "skill_admin",
            "mcp",
        ]
        approved_tools = security_config.get("approved_tools") or []
        return {
            "is_admin": bool(security_config.get("is_admin", False)),
            "enabled_toolsets": {
                str(item or "").strip()
                for item in toolsets
                if str(item or "").strip()
            },
            "approval_mode": str(security_config.get("approval_mode") or "manual").strip().lower() or "manual",
            "allow_local_terminal": bool(security_config.get("allow_local_terminal", False)),
            "workspace_root": os.path.abspath(str(getattr(self.owner, "project_root", "") or "")),
            "file_roots": [
                os.path.abspath(str(path or "").strip())
                for path in (security_config.get("file_roots") or [getattr(self.owner, "project_root", "")])
                if str(path or "").strip()
            ],
            "approved_tools": {
                str(item or "").strip()
                for item in approved_tools
                if str(item or "").strip()
            },
            "active_skills": {},
        }

    @staticmethod
    def _path_is_allowed(file_path: str, allowed_roots: list[str]) -> bool:
        normalized_path = os.path.abspath(str(file_path or "").strip())
        if not normalized_path:
            return False
        for root in allowed_roots:
            try:
                common_path = os.path.commonpath([normalized_path, root])
            except ValueError:
                continue
            if common_path == root:
                return True
        return False

    def evaluate_context_text(self, value: str, *, source: str) -> dict:
        scan_result = scan_text(value)
        if scan_result.get("flagged"):
            return {
                "ok": False,
                "blocked": True,
                "reason": f"Potential prompt injection detected in {source}.",
                "scan": scan_result,
            }
        return {"ok": True, "blocked": False, "scan": scan_result}

    def evaluate_tool_call(self, tool_definition: dict, arguments: dict | None = None, *, session_context: dict | None = None) -> dict:
        session_context = dict(session_context or self.build_session_context())
        metadata = get_tool_metadata(tool_definition)
        tool_name = str(((tool_definition or {}).get("function") or {}).get("name", "")).strip()
        arguments = dict(arguments or {})

        if metadata["toolset"] not in session_context.get("enabled_toolsets", set()):
            return {
                "ok": False,
                "decision": "blocked",
                "reason": f"Toolset '{metadata['toolset']}' is disabled.",
                "metadata": metadata,
            }
        if metadata["admin_only"] and not session_context.get("is_admin", False):
            approved_tools = session_context.get("approved_tools", set())
            if tool_name not in approved_tools:
                return {
                    "ok": False,
                    "decision": "requires_approval",
                    "reason": f"工具 '{tool_name}' 需要管理员权限，请先由用户确认是否执行。",
                    "metadata": metadata,
                }
        if metadata["backend"] == "local" and not session_context.get("allow_local_terminal", False):
            return {
                "ok": False,
                "decision": "blocked",
                "reason": f"Local backend is disabled for tool '{tool_name}'.",
                "metadata": metadata,
            }

        skill_metadata = dict((tool_definition or {}).get("x-dialogue-skill") or {})
        skill_name = str(skill_metadata.get("name") or "").strip()
        if skill_name and bool(skill_metadata.get("requires_activation", False)):
            active_skills = session_context.get("active_skills") or {}
            if skill_name not in active_skills:
                return {
                    "ok": False,
                    "decision": "blocked",
                    "reason": (
                        f"工具 '{tool_name}' 属于受治理 skill '{skill_name}'，"
                        f"请先调用 activateSkill 激活该 skill。"
                    ),
                    "metadata": metadata,
                    "skill": skill_metadata,
                }

        risk_level = metadata["risk_level"]
        approval_mode = session_context.get("approval_mode", "manual")
        if risk_level == "privileged":
            approved_tools = session_context.get("approved_tools", set())
            if tool_name not in approved_tools and approval_mode != "off":
                return {
                    "ok": False,
                    "decision": "requires_approval",
                    "reason": f"工具 '{tool_name}' 需要先获得用户授权。",
                    "metadata": metadata,
                }
        elif risk_level == "guarded" and metadata["requires_approval"] and approval_mode == "manual":
            approved_tools = session_context.get("approved_tools", set())
            if tool_name not in approved_tools:
                return {
                    "ok": False,
                    "decision": "requires_approval",
                    "reason": f"工具 '{tool_name}' 属于受保护操作，需要先获得用户授权。",
                    "metadata": metadata,
                }

        if metadata["toolset"] in {"file_read", "file_write"}:
            file_path = arguments.get("Path") or arguments.get("FilePath") or ""
            if not self._path_is_allowed(file_path, session_context.get("file_roots", [])):
                return {
                    "ok": False,
                    "decision": "blocked",
                    "reason": f"Path is outside allowed roots for tool '{tool_name}'.",
                    "metadata": metadata,
                }

        return {
            "ok": True,
            "decision": "allowed",
            "reason": "",
            "metadata": metadata,
        }
