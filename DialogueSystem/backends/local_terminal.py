"""Local terminal backend wrapper."""

from __future__ import annotations

import subprocess


def run_command(command: str, *, timeout_seconds: float = 20.0, cwd: str | None = None) -> dict:
    normalized_command = str(command or "").strip()
    if not normalized_command:
        return {"ok": False, "error": "Command is required.", "backend": "local"}
    try:
        completed = subprocess.run(
            normalized_command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout_seconds or 20.0)),
            cwd=str(cwd).strip() if str(cwd or "").strip() else None,
        )
        return {
            "ok": completed.returncode == 0,
            "backend": "local",
            "returncode": int(completed.returncode),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "cwd": str(cwd or "").strip(),
        }
    except Exception as error:
        return {
            "ok": False,
            "backend": "local",
            "error": str(error),
            "cwd": str(cwd or "").strip(),
        }
