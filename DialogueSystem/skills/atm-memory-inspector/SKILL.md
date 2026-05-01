---
name: atm-memory-inspector
description: Inspect Selena's ATM autonomous-task writings and stored artifacts.
metadata:
  author: dialogue-system
  version: "1.0"
---

# ATM Memory Inspector

Use this skill when the user is asking about Selena's recent autonomous-task work, especially when they want the actual stored writing instead of a paraphrased recollection.

## Workflow
- For broad questions like “你今天做了些什么” or “你今天写了什么”, search recent ATM artifacts first and answer only from the returned results.
- For requests like “原文”, “全文”, “念给我听”, “把你写的给我看”, first call `searchAutonomousTaskArtifacts` to locate the best candidate task, then call `readAutonomousTaskArtifact(TaskId=...)` to fetch the stored original text.
- Prefer quoting or summarizing the stored artifact faithfully. Do not invent poem or essay content that was not retrieved from the ATM artifact store.
- If multiple artifacts could match, mention the most relevant options briefly and ask the user to choose only when the best match is unclear.

## Good Retrieval Patterns
- “今天做了些什么”:
  Search recent ATM artifacts from today first, then summarize the returned tasks and outputs.
- “念给我听”:
  Search by the current topic or title hint, then read the selected stored artifact.
- “把你写的关于指挥的随笔原文给我”:
  Search with `Query="指挥"` and then read the matched task artifact by id.

## Tools
- `searchAutonomousTaskArtifacts`
- `readAutonomousTaskArtifact`
