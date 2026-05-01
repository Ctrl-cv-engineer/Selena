[简体中文](./zh-CN/autonomous-mode.md)

# Autonomous Task Mode

Autonomous task mode lets Selena do bounded work when the user has been idle for long enough.

## 1. Motivation

The goal is not to let the system run forever. The goal is to use quiet time for useful background work such as follow-up reading, note cleanup, or lightweight planning.

## 2. Trigger conditions

Autonomous mode only runs when the idle threshold and policy settings allow it. Typical controls include:

- whether the feature is enabled,
- how long the user must be idle,
- daily task limits,
- retry limits,
- interruption limits.

## 3. Three-stage pipeline

### Stage 1: task planning

Selena decides what is worth doing during the idle window.

### Stage 2: task execution

Planned tasks are executed through the configured agent type, tool budget, and timeout policy.

### Stage 3: sharing score evaluation

After a task completes, Selena evaluates whether the result is useful enough to mention later.

## 4. Sharing and cooldown

Two separate ideas matter here:

- `injection`: whether the completed result is inserted into future context
- `mentioning`: whether Selena proactively brings it up later

Cooldowns and score thresholds stop the system from repeatedly surfacing low-value results.

## 5. Resource budgets

Autonomous mode is budgeted separately from normal dialogue through:

- per-session token limits,
- per-task token limits,
- max task attempts,
- per-task timeouts,
- and daily caps.

## 6. Task persistence

Planned and executed work is persisted so the system can track attempts, state, and shareable output across restarts.

## 7. Example workflow

A typical autonomous session may look like this:

1. User is idle.
2. Selena plans three small tasks.
3. One task checks a previously discussed topic.
4. Another organizes a remembered list or follow-up note.
5. The results are scored for later mention.

## 8. Turning it off

Set `AutonomousTaskMode.enabled = false` in `config.json`.

## 9. Tuning suggestions

- Start conservatively with small task counts and tight budgets.
- Raise sharing thresholds if Selena mentions idle-time work too often.
- Lower timeouts or tool-call budgets if autonomous tasks become expensive.

## 10. Related documents

- [Agent loop](./agent-loop.md)
- [Subagent delegation](./subagent-delegation.md)
- [Memory system](./memory-system.md)
- [Config reference](../CONFIG_REFERENCE.md)
