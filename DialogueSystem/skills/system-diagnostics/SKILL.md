---
name: system-diagnostics
description: Read runtime logs and system diagnostics for troubleshooting the dialogue system.
metadata:
  author: dialogue-system
  version: "1.0"
---

# System Diagnostics

Use this skill when the user asks to check logs, troubleshoot issues, or inspect runtime state.

## Workflow
- Use `getSelfLog` to read recent runtime logs for troubleshooting.
- Summarize key errors or warnings found in the log output.

## Tools
- `getSelfLog`
