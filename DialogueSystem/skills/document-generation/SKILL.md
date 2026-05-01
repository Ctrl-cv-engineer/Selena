---
name: document-generation
description: Generate PDF, DOCX, and PPTX documents from structured content. Use when the user asks to create, export, or format documents.
compatibility: Requires python-docx, python-pptx, and reportlab packages.
metadata:
  author: dialogue-system
  version: "1.0"
---

# Document Generation

Generate professional documents in PDF, DOCX, or PPTX format from structured content provided by the user or assembled by the agent.

## Workflow
1. Determine the desired output format from the user request (PDF, DOCX, or PPTX).
2. Assemble the content: title, sections/slides, body text, and optional metadata (author, date).
3. Call `generateDocument` with the appropriate `Format` and structured `Content`.
4. Return the output file path to the user.

## Content Structure

The `Content` parameter is a JSON object with these fields:

```json
{
  "title": "Document Title",
  "author": "Author Name",
  "date": "2025-01-01",
  "sections": [
    {
      "heading": "Section Heading",
      "body": "Paragraph text here. Supports multiple paragraphs separated by newlines.",
      "level": 1
    }
  ]
}
```

For PPTX, each section becomes a slide:
```json
{
  "title": "Presentation Title",
  "sections": [
    {
      "heading": "Slide Title",
      "body": "Bullet point 1\nBullet point 2\nBullet point 3"
    }
  ]
}
```

## Guidelines
- Default to DOCX if the user does not specify a format.
- For PDF, use A4 page size with reasonable margins.
- For PPTX, keep slide content concise — use bullet points.
- Output files are saved to the project data directory by default.
- Always return the full output path so the user can locate the file.

## Tools
- `generateDocument`
