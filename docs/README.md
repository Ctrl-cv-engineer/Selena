[简体中文](./zh-CN/README.md)

<div align="center">

# Selena Documentation Hub

> This is the public project documentation set. If you are new here, start with the root [README](../README.md).

</div>

---

## Getting started

| Document | What it covers |
| --- | --- |
| [Quick start in the root README](../README.md#quick-start) | The shortest path to a working local setup |
| [Deployment guide](../DEPLOYMENT.md) | Environment isolation, Qdrant, runtime startup, production notes |
| [Config reference](../CONFIG_REFERENCE.md) | What the main `config.json` fields control |
| [LLM providers](./llm-providers.md) | Provider setup, alias mapping, reasoning mode, and rerank wiring |

## Architecture and core behavior

| Document | What it covers |
| --- | --- |
| [Architecture](./architecture.md) | Module boundaries, data flow, persistence, extension points |
| [Agent loop](./agent-loop.md) | Planning, budgets, policy checks, compression, subagents |
| [Intent routing](./intent-routing.md) | How Selena decides whether to enter agent mode |
| [Memory system](./memory-system.md) | Core memory, live context, archives, vector memory |

## Subsystems

| Document | What it covers |
| --- | --- |
| [Skill system](./skill-system.md) | Built-in skills, manifests, runtime modes, skill evolution |
| [Browser agent](./browser-agent.md) | Snapshot-based browser operation and CDP-driven workflows |
| [Subagent delegation](./subagent-delegation.md) | Built-in agent types, fan-out, quotas, and result handling |
| [Autonomous mode](./autonomous-mode.md) | Idle-time planning, execution, sharing score, cooldown |
| [MCP integration](./mcp-integration.md) | Registering external MCP servers and exposing their tools |
| [Web workbench](./frontend-workbench.md) | Frontend panels, local API, and observability surfaces |

## Operations and safety

| Document | What it covers |
| --- | --- |
| [Security policy](./security-policy.md) | Tool whitelists, file roots, approvals, execution backends |

## Documentation conventions

- Public docs live in the repository root and under `docs/`.
- Internal-only development notes in `DialogueSystem/docs/` stay out of the public doc tree.
- Prefer short positioning at the top, tables for important knobs, and Mermaid diagrams for non-trivial flows.
- Use relative links so the docs work both on GitHub and in local editors.

If you notice a gap in the public documentation, opening an issue or PR is always welcome.
