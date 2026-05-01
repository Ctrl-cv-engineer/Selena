[简体中文](./zh-CN/architecture.md)

# Overall Architecture

This document gives a high-level map of Selena's runtime structure and the main data flow through the system.

## 1. Three-layer architecture

Selena can be understood as three cooperating layers:

- `Interface and orchestration`
  - Dialogue entry, runtime startup, frontend bridge, API surfaces.
- `Cognitive core`
  - Intent routing, agent planning, memory, summarization, skill logic.
- `Execution and integration`
  - Browser runtime, MCP servers, local tools, storage, and security backends.

## 2. End-to-end flow for a single user turn

1. The runtime receives a new user message.
2. Recent topic context and selected memory are prepared.
3. Intent routing decides whether the request stays lightweight or enters agent mode.
4. The chosen path either produces a direct reply or enters the agent loop.
5. Tools, browser calls, MCP tools, or subagents can run as needed.
6. Results are compressed, cached, or persisted.
7. The reply is returned and the memory layers are updated.

## 3. Core modules

### 3.1 Entry and orchestration

- `DialogueSystem/main.py`
- runtime bootstrap
- frontend runtime startup and shutdown

### 3.2 Cognitive core

- intent routing
- agent planning and iteration
- summarization
- topic management

### 3.3 Memory system

- core memory
- live topic context
- topic archive
- long-term vector memory in Qdrant

### 3.4 Skills and tools

- tool definitions
- skill manifests
- skill runtime modes
- skill evolution support

### 3.5 External capabilities

- browser control
- MCP integrations
- subagent delegation
- user-facing services such as reminders

### 3.6 Security modules

- enabled toolsets
- approval rules
- file root restrictions
- execution backend selection

## 4. Persistence layout

Important persisted data includes:

- SQLite runtime data under `DialogueSystem/data/`
- grouped conversation history under `DialogueSystem/history/`
- Qdrant vector collections
- logs under `DialogueSystem/logs/`

## 5. Background async work

Selena is not only request/response. It also runs background-style flows such as:

- summary workers,
- archive updates,
- autonomous task persistence,
- and optional frontend support processes.

## 6. Extension points

Common extension points include:

- adding a new skill,
- registering a new MCP server,
- adding a toolset,
- plugging in a new provider/model alias,
- or decomposing the runtime into more explicit modules.

## 7. Related documents

- [Agent loop](./agent-loop.md)
- [Memory system](./memory-system.md)
- [Skill system](./skill-system.md)
- [MCP integration](./mcp-integration.md)
- [Security policy](./security-policy.md)
