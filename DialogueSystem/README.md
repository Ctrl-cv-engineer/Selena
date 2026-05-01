[简体中文](./README.zh-CN.md)

# DialogueSystem Directory Guide

This directory contains Selena's main runtime, static prompt assets, and runtime-generated artifacts. The sections below focus on the parts contributors are most likely to touch.

## Python modules

- Root files
  - `main.py`: top-level runtime entry that starts Selena, manages dialogue flow, performs intent routing, runs agent planning, and starts the background summary worker.
  - `Demo.py`: manual development-time test script, not part of the main runtime loop.
  - `__init__.py`: package-level exports.
- `agent/`
  - Agent loop, session state, delegation logic, token budgets, and tool display/result-compression helpers.
- `autonomous/`
  - Planning, execution, and SQLite persistence for autonomous task mode.
- `browser/`
  - Browser control, compatibility layers, and enhanced browser tools.
- `config/`
  - Centralized path constants and loaders for prompts, tools, and skills.
- `llm/`
  - HTTP wrappers for LLM, embedding, and rerank calls.
- `memory/`
  - Context memory, topic history, summary workers, local memory storage, and summary-worker helpers.
- `policy/`
  - Tool metadata and security-policy decisions.
- `runtime/`
  - Dynamic tools, MCP bridges, and the local frontend API runtime.
- `services/`
  - User-facing services, currently centered on reminders and task management.
- `skill_system/`
  - Skill loading, management, and related marketplace logic.

## Static asset directories

- `MdFile/`
  - System prompts and prompt templates, grouped by area:
    - `agent/`
    - `intent/`
    - `memory/`
    - `topic/`
    - `dialogue/`
    - `skill/`
  - These files directly shape model behavior and should be treated as part of the runtime logic.
- `tools/`
  - JSON definitions for standard function tools used during agent planning.
- `skills/`
  - Skill manifests and tool definitions bundled with each skill.

## Runtime directories

- `history/`
  - Persisted conversation history.
  - `raw_dialogue_*.jsonl` stores raw session messages, grouped by `topicGroup`.
  - `.summary_memory_state.json` and `.summary_memory_worker.lock` belong to the summary worker.
- `logs/`
  - Runtime logs such as `dialogue_system.log*` and `history_summary_worker*.log`.
- `__pycache__/`
  - Python cache files; not something you should maintain manually.