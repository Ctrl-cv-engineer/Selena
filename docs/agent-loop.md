[简体中文](./zh-CN/agent-loop.md)

# Agent Main Loop

This document explains how Selena's tool-using agent loop is structured and where the main runtime guardrails live.

## 1. Loop skeleton

At a high level the loop looks like this:

1. Read the current dialogue state and working context.
2. Decide whether a tool call is needed.
3. Plan the next tool call or produce a direct answer.
4. Run safety and approval checks.
5. Execute the tool.
6. Compress or cache the tool result if needed.
7. Continue until the task is complete or a budget limit is reached.

## 2. Three budget gates

### `AgentRuntime.max_tool_calls`

Hard cap on the total number of tool calls inside one agent session.

### `AgentRuntime.max_consecutive_same_tool_calls`

Stops loops where the model keeps retrying the same tool without making progress.

### Token budget

The runtime also watches context growth and avoids letting tool output or retrieval injections overwhelm the window.

## 3. Tool planning stage

The planning step decides whether Selena should:

- answer directly,
- call a normal tool,
- delegate work to a subagent,
- or stop because the task is already complete.

This is where prompt design, tool descriptions, and model choice strongly affect behavior.

## 4. Policy layer: `ToolPolicyEngine`

Before execution, tool calls go through policy checks such as:

- whether the tool is enabled,
- whether the current runtime mode allows it,
- whether approval is required,
- whether local file or terminal access is safe under current policy.

## 5. Tool result handling

### Automatic compression

Large tool outputs are compressed before they are fed back into the loop. This keeps context windows usable during long tasks.

### Retrieval cache: `AgentRetrievalCache`

Results from selected retrieval-heavy tools can be cached and reused when the model asks essentially the same question again in the same session.

## 6. Multi-model specialization

Selena can use different model aliases for different agent-related tasks such as:

- planning,
- lightweight replies,
- summarization,
- intent review,
- skill evolution evaluation.

This lets the system balance cost, latency, and reasoning depth.

## 7. Context compression strategy

The loop tries to keep only the useful parts of:

- recent messages,
- memory injections,
- tool outputs,
- and intermediate working notes.

If the loop feels expensive or confused, this is one of the first places to inspect.

## 8. Subagent delegation

The main loop can hand off bounded work to subagents for research, exploration, planning, review, testing, or general execution. Delegation is especially useful when several independent branches can run in parallel.

## 9. Tool display and approval UI

When the frontend is enabled, the runtime can surface planned tool usage and approval requests through the web workbench, which makes it easier to understand why the agent is doing what it is doing.

## 10. Related documents

- [Architecture](./architecture.md)
- [Intent routing](./intent-routing.md)
- [Skill system](./skill-system.md)
- [Security policy](./security-policy.md)
- [Subagent delegation](./subagent-delegation.md)
