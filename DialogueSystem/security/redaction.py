"""Secret and sensitive-output redaction helpers."""

from __future__ import annotations

import re


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.=]{12,}", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*[:=]\s*[\"']?[^\"'\s]{8,}", re.IGNORECASE),
]


def redact_text(value: str) -> str:
    text = str(value or "")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def redact_payload(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): redact_payload(item)
            for key, item in value.items()
        }
    return value

