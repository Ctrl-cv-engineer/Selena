[简体中文](./zh-CN/skill-system.md)

# Skill System

Selena uses skills as reusable packages of instructions, tools, and metadata that shape how the runtime solves classes of tasks.

## 1. Skill vs tool

- A `tool` is a callable capability.
- A `skill` is a higher-level package that can bundle instructions, tools, intent examples, and execution rules.

## 2. Built-in skills

The repository currently documents built-in skills such as:

- `chrome-browser-agent`
- `browser-enhancements`
- `web-access`
- `schedule-manager`
- `subagent-manager`
- `skill-manager`
- `document-generation`
- `atm-memory-inspector`
- `system-diagnostics`

## 3. Skill file structure

### `manifest.json`

Describes metadata, tools, runtime mode, and optional intent examples.

### `SKILL.md`

Contains the execution guidance the model follows when that skill is active.

## 4. Runtime mode: `runtime_mode`

The runtime mode controls how a skill is loaded and how it participates in execution. This is useful when a capability should behave differently from a plain always-on tool.

## 5. Skill evolution: `SkillEvolution`

Selena can evaluate repeated tool patterns and decide whether they should become a reusable skill.

### How it works

At a high level:

1. observe repeated tool behavior
2. collect intermediate artifacts
3. evaluate similarity and usefulness
4. materialize a reusable skill candidate

### Key parameters

- `enabled`
- `min_tool_calls`
- `similarity_threshold`

### `Procedure` artifacts

Skill evolution can generate intermediate procedure-like artifacts before they become full skills.

## 6. Writing your own skill

A minimal skill usually needs:

- a `manifest.json`
- a `SKILL.md`
- and any tool definitions or assets it depends on

Keep the behavior tight and explicit. A good skill is easier to compose than a giant vague instruction blob.

## 7. Related documents

- [Intent routing](./intent-routing.md)
- [Agent loop](./agent-loop.md)
- [MCP integration](./mcp-integration.md)
