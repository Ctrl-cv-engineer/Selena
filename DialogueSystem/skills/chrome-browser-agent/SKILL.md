---
name: chrome-browser-agent
description: Control a visible Google Chrome page with navigation, search, text snapshots, tab management, clicks, typing, waiting, key presses, and optional screenshots.
compatibility: Requires Google Chrome installed locally.
metadata:
  author: dialogue-system
  version: "1.2"
---

# Chrome Browser Agent

Use this skill ONLY when the task requires operating a visible browser page: playing music, filling forms, clicking buttons, logging in, taking screenshots, or any other interactive web operation.

Do NOT use this skill just to look up information — use `web-access` (`webSearch`) for that.

## Workflow
- Start with `browserNavigate` or `browserSearch`.
- Call `browserSnapshot` after navigation and after major page changes.
- Use refs from `browserSnapshot` for `browserClick` and `browserType`; do not guess selectors.
- When the page needs time to settle, prefer `browserWait` over repeatedly snapshotting too early.
- If the task spans multiple tabs, use `browserListTabs` and `browserSelectTab` instead of assuming focus.
- Treat `browserScreenshot` as optional auxiliary evidence only when the model supports images. The text path must still work with `browserSnapshot` / `browserExtractPage`.
- After a successful multi-step path, summarize the shortest reusable path in `ProcedureSummary`.

## Tools
- `browserNavigate`
- `browserSearch`
- `browserSnapshot`
- `browserClick`
- `browserType`
- `browserScroll`
- `browserGoBack`
- `browserListTabs`
- `browserSelectTab`
- `browserCloseTab`
- `browserWait`
- `browserPressKey`
- `browserScreenshot`
