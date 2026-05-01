---
name: browser-enhancements
description: Richer browser helpers for opening pages, extracting larger page text, and reading linked pages with safer candidate selection.
compatibility: Requires Google Chrome installed locally.
metadata:
  author: dialogue-system
  version: "1.1"
---

# Browser Enhancements

Use this skill when the normal browser snapshot is too small or when a task needs page text extraction.

## Workflow
- Use `browserOpenTab` when a URL should be opened without replacing the current page.
- Use `browserExtractPage` to collect larger page text for summarization or analysis.
- Use `browserReadLinkedPage` with `Ref` when you already know which visible link to open.
- Use `browserReadLinkedPage(Query=..., AutoOpenFirst=false)` when you want candidate refs first instead of blindly opening the first result.
- If the model only needs browser text, prefer `browserExtractPage` / `browserSnapshot`; do not make screenshots mandatory.

## Tools
- `browserOpenTab`
- `browserExtractPage`
- `browserReadLinkedPage`
