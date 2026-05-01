"""Tool metadata helpers and security defaults for DialogueSystem."""

from __future__ import annotations

import copy


DEFAULT_TOOL_METADATA = {
    "toolset": "core",
    "risk_level": "safe",
    "admin_only": False,
    "requires_approval": False,
    "backend": "direct",
    "supports_checkpoint": False,
    "supports_redaction": True,
}


INFERRED_METADATA_BY_TOOL_NAME = {
    "manageSkill": {
        "toolset": "skill_admin",
        "risk_level": "privileged",
        "admin_only": True,
        "requires_approval": True,
    },
    "deleteSkill": {
        "toolset": "skill_admin",
        "risk_level": "privileged",
        "admin_only": True,
        "requires_approval": True,
    },
    "listSkills": {
        "toolset": "skill_admin",
        "risk_level": "guarded",
        "admin_only": True,
    },
    "activateSkill": {
        "toolset": "core",
        "risk_level": "safe",
    },
    "promoteLearnedProcedureToSkill": {
        "toolset": "skill_admin",
        "risk_level": "privileged",
        "admin_only": True,
        "requires_approval": True,
    },
    "importSkill": {
        "toolset": "skill_admin",
        "risk_level": "privileged",
        "admin_only": True,
        "requires_approval": True,
    },
    "exportSkill": {
        "toolset": "skill_admin",
        "risk_level": "guarded",
        "admin_only": True,
    },
    "browseSkillMarketplace": {
        "toolset": "skill_admin",
        "risk_level": "guarded",
        "admin_only": True,
    },
    "delegateTask": {
        "toolset": "subagent",
        "risk_level": "guarded",
    },
    "delegateTasksParallel": {
        "toolset": "subagent",
        "risk_level": "guarded",
    },
    "continueDelegatedTask": {
        "toolset": "subagent",
        "risk_level": "guarded",
    },
    "cancelDelegatedTask": {
        "toolset": "subagent",
        "risk_level": "guarded",
    },
    "getDelegatedTaskStatus": {
        "toolset": "subagent",
        "risk_level": "safe",
    },
    "listDelegatedTasks": {
        "toolset": "subagent",
        "risk_level": "safe",
    },
    "waitForDelegatedTasks": {
        "toolset": "subagent",
        "risk_level": "safe",
    },
    "refreshMcpTools": {
        "toolset": "mcp",
        "risk_level": "guarded",
        "admin_only": True,
        "requires_approval": True,
    },
    "listMcpTools": {
        "toolset": "mcp",
        "risk_level": "guarded",
        "admin_only": True,
    },
    "storeLongTermMemory": {
        "toolset": "memory",
        "risk_level": "guarded",
    },
    "searchLongTermMemory": {
        "toolset": "memory",
        "risk_level": "safe",
    },
    "searchFullText": {
        "toolset": "memory",
        "risk_level": "safe",
    },
    "listTopicsByDate": {
        "toolset": "memory",
        "risk_level": "safe",
    },
    "getSelfLog": {
        "toolset": "core",
        "risk_level": "guarded",
    },
    "controlSelf": {
        "toolset": "core",
        "risk_level": "privileged",
        "admin_only": True,
        "requires_approval": True,
    },
}


def infer_tool_metadata(tool_definition: dict | None = None) -> dict:
    function_name = str(((tool_definition or {}).get("function") or {}).get("name", "")).strip()
    metadata = {}
    if function_name in INFERRED_METADATA_BY_TOOL_NAME:
        metadata.update(INFERRED_METADATA_BY_TOOL_NAME[function_name])
    if function_name.startswith("browser"):
        metadata.update({"toolset": "browser", "risk_level": "guarded"})
    elif function_name.endswith("ScheduleTask") or function_name.startswith(("createScheduleTask", "deleteScheduleTask", "queryScheduleTasks", "updateScheduleTask")):
        metadata.update({"toolset": "schedule", "risk_level": "guarded"})
    elif function_name.startswith("mcp_"):
        metadata.update({"toolset": "mcp", "risk_level": "guarded"})
    return metadata


def normalize_tool_metadata(metadata: dict | None = None) -> dict:
    payload = dict(DEFAULT_TOOL_METADATA)
    payload.update(
        {
            key: value
            for key, value in dict(metadata or {}).items()
            if key in DEFAULT_TOOL_METADATA
        }
    )
    payload["toolset"] = str(payload.get("toolset") or "core").strip() or "core"
    payload["risk_level"] = str(payload.get("risk_level") or "safe").strip().lower() or "safe"
    payload["backend"] = str(payload.get("backend") or "direct").strip().lower() or "direct"
    payload["admin_only"] = bool(payload.get("admin_only", False))
    payload["requires_approval"] = bool(payload.get("requires_approval", False))
    payload["supports_checkpoint"] = bool(payload.get("supports_checkpoint", False))
    payload["supports_redaction"] = bool(payload.get("supports_redaction", True))
    return payload


def attach_tool_metadata(tool_definition: dict, metadata: dict | None = None) -> dict:
    normalized_definition = copy.deepcopy(tool_definition or {})
    normalized_definition["x-dialogue-metadata"] = normalize_tool_metadata(metadata)
    return normalized_definition


def get_tool_metadata(tool_definition: dict | None = None) -> dict:
    if not isinstance(tool_definition, dict):
        return normalize_tool_metadata()
    payload = infer_tool_metadata(tool_definition)
    payload.update(tool_definition.get("x-dialogue-metadata") or {})
    return normalize_tool_metadata(payload)
