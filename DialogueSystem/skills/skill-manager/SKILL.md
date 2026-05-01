---
name: skill-manager
description: List, create, update, delete, import, export, and browse DialogueSystem managed skills.
metadata:
  author: dialogue-system
  version: "2.0"
---

# Skill Manager

Use this skill when the user asks to inspect, create, update, enable, disable, delete, import, export, or browse managed skills.

## Workflow
- Use `listSkills` to inspect installed skills and diagnostics.
- Use `manageSkill` to write `manifest.json`, `SKILL.md`, and optional tool definitions.
- `manageSkill` 也可以声明治理字段，例如 `AllowedTools`、`DisableModelInvocation`、`UserInvocable`、`Paths`。
- Managed skills cannot include runtime code; only trusted built-in skills may register Python handlers.
- Use `promoteLearnedProcedureToSkill` when the user asks to turn a learned operation shortcut into a skill.
- Use `exportSkill` to package a skill as a portable .zip archive for sharing.
- Use `importSkill` to install a skill from a local directory or .zip archive.
- Use `browseSkillMarketplace` to list local skills or browse a remote skill registry.

## Tools
- `listSkills`
- `manageSkill`
- `deleteSkill`
- `promoteLearnedProcedureToSkill`
- `importSkill`
- `exportSkill`
- `browseSkillMarketplace`
