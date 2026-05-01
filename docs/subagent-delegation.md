[简体中文](./zh-CN/subagent-delegation.md)

# Subagent Delegation

Subagents let Selena split work into bounded branches instead of forcing one model thread to do everything sequentially.

## 1. Why use subagents

They are most useful when:

- several research branches can run in parallel,
- one task benefits from a specialized agent type,
- or the main loop needs to offload bounded work.

## 2. Built-in agent types

The current runtime documents agent roles such as:

- `general`
- `explore`
- `research`
- `plan`
- `review`
- `test`

## 3. Delegation patterns

### Single-task delegation

Hand one bounded subtask to one agent.

### Parallel fan-out

Send independent subtasks to several agents at once.

### Waiting for results

Wait only when the main path truly needs the result.

### Continuing a conversation

The parent can send follow-up instructions to an existing delegated agent when the context still matters.

### Status query or cancellation

Long-running work should still be observable and cancellable.

## 4. Resource quotas

Subagents are governed by quotas so delegation does not silently become unbounded.

### Global quotas

Important controls include depth, concurrency, queue size, tool limits, and cached-result behavior.

## 5. Toolset whitelist

Subagents should only receive the tool families they actually need. This keeps delegated execution more predictable and safer.

## 6. A realistic parallel example

A common pattern is splitting a research task into:

- paper reading,
- benchmark collection,
- and synthesis or review,

then merging the results in the main loop.

## 7. Result cache

Result caching can help avoid repeating the same delegated work when the task boundaries are effectively identical.

## 8. Custom agent types

The config can define type-specific limits and toolsets so custom subagents can be introduced without rewriting the overall delegation model.

## 9. Subagents inside autonomous mode

Autonomous task execution can also rely on subagents when the configured idle-time workflow benefits from branching.

## 10. Related documents

- [Agent loop](./agent-loop.md)
- [Autonomous mode](./autonomous-mode.md)
- [Security policy](./security-policy.md)
