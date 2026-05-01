---
name: subagent-manager
description: Delegate bounded subtasks to background child agents and inspect their progress and results.
metadata:
  author: dialogue-system
  version: "1.0"
---

# Subagent Manager

Use this skill when a task can be split into an isolated background task or when the user asks about delegated work.

## Workflow
- Delegate only bounded subtasks with clear expected output.
- Use `delegateTasksParallel` when several subtasks are independent and can be launched together.
- Use a higher `Priority` for urgent delegated work when multiple tasks may compete for limited worker slots.
- Leave `UseCache=true` for repeatable codebase analysis tasks; set `UseCache=false` when the task depends on fast-changing external state or must be freshly recomputed.
- Check status with `getDelegatedTaskStatus` before relying on the result.
- Use `waitForDelegatedTasks` to implement a `wait_all` step after parallel fan-out.
- Use `listDelegatedTasks` to inspect running or completed tasks.
- If a delegated task is waiting for approval or follow-up input, resume it with `continueDelegatedTask`.
- Cancel work that is no longer needed with `cancelDelegatedTask`.

## Tools
- `delegateTask`
- `delegateTasksParallel`
- `continueDelegatedTask`
- `cancelDelegatedTask`
- `getDelegatedTaskStatus`
- `listDelegatedTasks`
- `waitForDelegatedTasks`
