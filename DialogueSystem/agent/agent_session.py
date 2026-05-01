"""Session-scoped execution state for Selena and detached sub-agents."""

from __future__ import annotations

from dataclasses import dataclass, field
import queue


def build_default_agent_turn_request() -> dict:
    """Return the default per-turn request payload for an agent session."""
    return {
        "user_input": "",
        "route": "",
        "selected_ability": "",
        "reason": "",
        "ranked_candidates": [],
    }


def normalize_agent_turn_request(value: dict | None = None) -> dict:
    """Normalize a partially-populated turn request payload."""
    normalized = build_default_agent_turn_request()
    payload = dict(value or {})
    normalized["user_input"] = str(payload.get("user_input", "") or "").strip()
    normalized["route"] = str(payload.get("route", "") or "").strip().lower()
    normalized["selected_ability"] = str(payload.get("selected_ability", "") or "").strip()
    normalized["reason"] = str(payload.get("reason", "") or "").strip()
    normalized["ranked_candidates"] = [
        dict(item)
        for item in (payload.get("ranked_candidates") or [])
        if isinstance(item, dict)
    ]
    return normalized


@dataclass
class AgentExecutionState:
    """Mutable runtime state that must be isolated per agent session."""

    current_turn_agent_request: dict = field(default_factory=build_default_agent_turn_request)
    current_turn_visible_agent_steps: list = field(default_factory=list)
    current_turn_visible_agent_step_seq: int = 0
    active_tool_session_context: dict | None = None
    subagent_depth: int = 0
    pending_tool_approvals: list = field(default_factory=list)
    pending_tool_approval_seq: int = 0
    turn_loaded_deferred_tool_names: set = field(default_factory=set)
    turn_active_tools_snapshot: list = field(default_factory=list)
    suspended_agent_state: dict | None = None
    agent_interrupt_queue: queue.Queue = field(default_factory=queue.Queue)
    current_turn_tool_trace: list = field(default_factory=list)
    current_turn_retrieval_cache_ids: list = field(default_factory=list)
    current_turn_browser_observations: list = field(default_factory=list)
    current_turn_browser_visual_artifacts: list = field(default_factory=list)


class AgentSession:
    """A named execution session for the main agent or a detached sub-agent."""

    def __init__(
        self,
        name: str,
        *,
        inherited_state: AgentExecutionState | None = None,
        turn_request: dict | None = None,
    ):
        inherited_request = normalize_agent_turn_request(
            inherited_state.current_turn_agent_request if inherited_state else None
        )
        normalized_turn_request = normalize_agent_turn_request(turn_request or inherited_request)
        inherited_depth = int(getattr(inherited_state, "subagent_depth", 0) or 0)
        self.name = str(name or "agent-session").strip() or "agent-session"
        self.state = AgentExecutionState(
            current_turn_agent_request=normalized_turn_request,
            subagent_depth=inherited_depth,
        )
