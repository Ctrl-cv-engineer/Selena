"""Filesystem checkpoint helpers for privileged file writes."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

try:
    from ..paths import DATA_DIR
except ImportError:
    try:
        from DialogueSystem.config.paths import DATA_DIR
    except ImportError:
        from DialogueSystem.config.paths import DATA_DIR


CHECKPOINT_DIR = os.path.join(DATA_DIR, "tool_checkpoints")


def ensure_checkpoint_dir() -> str:
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return CHECKPOINT_DIR


def create_file_checkpoint(file_path: str) -> dict:
    normalized_path = os.path.abspath(str(file_path or "").strip())
    if not normalized_path:
        raise ValueError("file_path is required for checkpointing.")
    checkpoint_dir = ensure_checkpoint_dir()
    file_name = Path(normalized_path).name or "file"
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    checkpoint_path = os.path.join(checkpoint_dir, f"{timestamp}_{file_name}.bak")
    existed = os.path.exists(normalized_path)
    if existed:
        shutil.copy2(normalized_path, checkpoint_path)
    return {
        "ok": True,
        "source_path": normalized_path,
        "checkpoint_path": checkpoint_path if existed else "",
        "source_existed": existed,
    }

