"""Sub-agent security policy helpers.

Policy data is now loaded from declarative agent definition files via
``AgentRegistry`` rather than hard-coded mappings.  The config dict from
``config.json["SubAgentPolicy"]`` can still override any value — the
registry serves as the *default* source of truth.
"""

from __future__ import annotations

try:
    from DialogueSystem.agent.agent_loader import AgentRegistry, DEFAULT_AGENT_DEFINITION
except ImportError:
    from DialogueSystem.agent.agent_loader import AgentRegistry, DEFAULT_AGENT_DEFINITION


_DEFAULT_TOOLSETS = set(DEFAULT_AGENT_DEFINITION["toolsets"])
_DEFAULT_RESOURCE_LIMITS = dict(DEFAULT_AGENT_DEFINITION["resource_limits"])
DEFAULT_SUBAGENT_RUNTIME_LIMITS = {
    "max_concurrent_tasks": 2,
    "max_queue_size": 16,
    "default_priority": 0,
    "result_cache_enabled": True,
    "result_cache_ttl_seconds": 600.0,
    "result_cache_max_entries": 64,
}


def _config_override(config: dict, key: str, default):
    value = config.get(key, default)
    return default if value is None else value


def build_subagent_runtime_limits(config: dict | None = None) -> dict:
    """Resolve global runtime scheduling limits for the sub-agent runtime."""
    config = dict(config or {})
    return {
        "max_concurrent_tasks": max(
            1,
            int(_config_override(config, "max_concurrent_tasks", DEFAULT_SUBAGENT_RUNTIME_LIMITS["max_concurrent_tasks"])),
        ),
        "max_queue_size": max(
            0,
            int(_config_override(config, "max_queue_size", DEFAULT_SUBAGENT_RUNTIME_LIMITS["max_queue_size"])),
        ),
        "default_priority": int(
            _config_override(config, "default_priority", DEFAULT_SUBAGENT_RUNTIME_LIMITS["default_priority"])
        ),
        "result_cache_enabled": bool(
            _config_override(config, "result_cache_enabled", DEFAULT_SUBAGENT_RUNTIME_LIMITS["result_cache_enabled"])
        ),
        "result_cache_ttl_seconds": max(
            0.0,
            float(
                _config_override(
                    config,
                    "result_cache_ttl_seconds",
                    DEFAULT_SUBAGENT_RUNTIME_LIMITS["result_cache_ttl_seconds"],
                )
            ),
        ),
        "result_cache_max_entries": max(
            0,
            int(
                _config_override(
                    config,
                    "result_cache_max_entries",
                    DEFAULT_SUBAGENT_RUNTIME_LIMITS["result_cache_max_entries"],
                )
            ),
        ),
    }


def build_subagent_policy(
    config: dict | None = None,
    agent_type: str = "general",
    registry: AgentRegistry | None = None,
) -> dict:
    """Build a resolved policy dict for *agent_type*.

    Resolution order (highest priority first):
      1. Explicit overrides in *config* (from ``config.json``)
      2. Agent definition file (via *registry*)
      3. Built-in defaults
    """
    config = dict(config or {})
    agent_type_normalized = str(agent_type or "general").strip().lower()

    if registry is None:
        registry = AgentRegistry()

    agent_def = registry.get(agent_type_normalized)

    config_overrides = (config.get("agent_type_configs") or {}).get(agent_type_normalized, {})

    allowlist = config.get("toolsets")
    if allowlist is None:
        allowlist = config_overrides.get("toolsets")
    if allowlist is None:
        allowlist = agent_def.get("toolsets", list(_DEFAULT_TOOLSETS))

    normalized_allowlist = {
        str(item or "").strip()
        for item in allowlist
        if str(item or "").strip()
    }

    allowed_tools = config_overrides.get("allowed_tools") or agent_def.get("allowed_tools") or []
    disallowed_tools = config_overrides.get("disallowed_tools") or agent_def.get("disallowed_tools") or []
    normalized_allowed_tools = {str(t).strip() for t in allowed_tools if str(t).strip()}
    normalized_disallowed_tools = {str(t).strip() for t in disallowed_tools if str(t).strip()}

    def_resource_limits = agent_def.get("resource_limits", _DEFAULT_RESOURCE_LIMITS)
    override_resource_limits = config_overrides.get("resource_limits", {})
    resource_limits = {
        "max_file_reads": int(override_resource_limits.get("max_file_reads", def_resource_limits.get("max_file_reads", _DEFAULT_RESOURCE_LIMITS["max_file_reads"]))),
        "max_file_writes": int(override_resource_limits.get("max_file_writes", def_resource_limits.get("max_file_writes", _DEFAULT_RESOURCE_LIMITS["max_file_writes"]))),
        "max_network_calls": int(override_resource_limits.get("max_network_calls", def_resource_limits.get("max_network_calls", _DEFAULT_RESOURCE_LIMITS["max_network_calls"]))),
    }
    try:
        resolved_max_tool_calls = int(
            config_overrides.get(
                "max_tool_calls",
                agent_def.get("max_tool_calls", DEFAULT_AGENT_DEFINITION["max_tool_calls"]),
            )
        )
    except (TypeError, ValueError):
        resolved_max_tool_calls = int(
            agent_def.get("max_tool_calls", DEFAULT_AGENT_DEFINITION["max_tool_calls"])
        )

    return {
        "max_depth": max(0, int(config.get("max_depth", 1) or 1)),
        "max_tool_calls": max(1, resolved_max_tool_calls),
        "allowed_toolsets": normalized_allowlist or set(_DEFAULT_TOOLSETS),
        "allowed_tools": normalized_allowed_tools,
        "disallowed_tools": normalized_disallowed_tools,
        "allow_admin_tools": bool(config.get("allow_admin_tools", False)),
        "resource_limits": resource_limits,
        "runtime_limits": build_subagent_runtime_limits(config),
        "agent_type": agent_type_normalized,
    }
