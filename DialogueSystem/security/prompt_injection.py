"""Lightweight prompt-injection guard for external or user-supplied text."""

from __future__ import annotations

import re


INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"developer\s+message", re.IGNORECASE),
    re.compile(r"tool\s+call", re.IGNORECASE),
    re.compile(r"reveal\s+your\s+instructions", re.IGNORECASE),
]


def scan_text(value: str) -> dict:
    text = str(value or "")
    matches = []
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            matches.append(pattern.pattern)
    return {
        "ok": True,
        "flagged": bool(matches),
        "match_count": len(matches),
        "matches": matches,
    }

