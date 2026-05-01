---
name: web-access
description: Use Kimi built-in web search to retrieve fresh public-web information.
metadata:
  author: dialogue-system
  version: "2.1"
---

# Web Access

Use this skill for current facts, recent events, prices, schedules, or when the user explicitly asks to search online.
This is the **preferred and first-choice** method for any internet information retrieval.

## Workflow
- Use `webSearch` when the answer should be verified against external sources.
- Prefer source-grounded answers for unstable or recent information.
- Do not browse the local filesystem to simulate web access.
- Do NOT open a visible browser (chrome-browser-agent) just to look up information. Use `webSearch` instead.

## When NOT to use
- When the user needs to **interact** with a visible web page (play music, fill forms, click buttons, log in).
- For those tasks, use `chrome-browser-agent` instead.

## Tools
- `webSearch`
