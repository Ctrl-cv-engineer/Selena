"""Summary worker launch helpers extracted from ``DialogueSystem.main``."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

from logging_utils import build_component_log_path, build_daily_log_path

from ..config.paths import PROJECT_ROOT, SCRIPT_DIR


logger = logging.getLogger("DialogueSystem.main")


def _refresh_summary_worker_log_paths(self):
    """刷新摘要 worker 相关日志路径。"""
    self._summary_worker_log_path = build_component_log_path(
        SCRIPT_DIR,
        self._summary_worker_log_component,
    )
    self._summary_worker_bootstrap_log_path = build_daily_log_path(
        SCRIPT_DIR,
        self._summary_worker_bootstrap_component,
    )


def _resolve_conda_executable() -> str:
    """解析可用的 conda 可执行文件路径。"""
    candidates = []
    conda_exe = os.environ.get("CONDA_EXE")
    if conda_exe:
        candidates.append(conda_exe)
    candidates.append(os.path.join(os.path.dirname(sys.executable), "Scripts", "conda.exe"))
    which_conda = shutil.which("conda.exe") or shutil.which("conda")
    if which_conda:
        candidates.append(which_conda)

    seen = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized in seen:
            continue
        seen.add(normalized)
        if os.path.exists(candidate):
            return candidate
    return ""


def _build_summary_worker_command(self, model_key: str):
    """构建 detached 摘要 worker 的启动命令。"""
    base_args = [
        self._summary_worker_script,
        "--model",
        model_key,
        "--log-path",
        self._summary_worker_log_path,
        "--memory-collection",
        self._memory_collection["name"],
        "--vector-size",
        str(self._memory_collection["vector_size"]),
    ]
    conda_exe = self._resolve_conda_executable()
    if conda_exe and self._summary_worker_conda_env:
        return (
            [
                conda_exe,
                "run",
                "--no-capture-output",
                "-n",
                self._summary_worker_conda_env,
                "python",
                *base_args,
            ],
            f"conda:{self._summary_worker_conda_env}",
        )
    return ([sys.executable, *base_args], sys.executable)


def _start_summary_memory_worker(self):
    """启动历史摘要 worker，并返回启动结果。"""
    if self._summary_worker_started:
        return {"started": False, "reason": "already_started"}
    if not os.path.exists(self._summary_worker_script):
        logger.error("Summary worker script not found: %s", self._summary_worker_script)
        return {"started": False, "reason": "script_not_found"}
    self._refresh_summary_worker_log_paths()
    if os.path.exists(self._summary_worker_lock_path):
        try:
            with open(self._summary_worker_lock_path, "r", encoding="utf-8") as file:
                pid = int((file.read() or "0").strip())
            if pid > 0:
                os.kill(pid, 0)
                self._summary_worker_started = True
                logger.info("History summary worker already running | pid=%s", pid)
                return {"started": False, "reason": "already_running", "pid": pid}
        except Exception:
            pass

    model_key = self._get_model_select_model_key("SummaryAndMermory")
    if not model_key:
        logger.error("ModelSelect.SummaryAndMermory is not configured")
        return {"started": False, "reason": "model_not_configured"}

    for target in (self._summary_worker_log_path, self._summary_worker_bootstrap_log_path):
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)

    cmd, worker_launcher = self._build_summary_worker_command(model_key)
    creation_flags = 0
    if os.name == "nt":
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        with open(self._summary_worker_bootstrap_log_path, "a", encoding="utf-8") as bootstrap_log:
            bootstrap_log.write(
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Launch worker | "
                f"launcher={worker_launcher} | cwd={PROJECT_ROOT} | cmd={cmd}\n"
            )
            process = subprocess.Popen(
                cmd,
                cwd=PROJECT_ROOT,
                creationflags=creation_flags,
                stdin=subprocess.DEVNULL,
                stdout=bootstrap_log,
                stderr=subprocess.STDOUT,
            )

        time.sleep(1.5)
        exit_code = process.poll()
        if exit_code is not None:
            bootstrap_tail = self._read_log_tail(self._summary_worker_bootstrap_log_path, max_lines=60)
            logger.error(
                "History summary worker exited immediately | model=%s | launcher=%s | exit_code=%s | bootstrap_log=%s\n%s",
                model_key,
                worker_launcher,
                exit_code,
                self._summary_worker_bootstrap_log_path,
                bootstrap_tail,
            )
            return {
                "started": False,
                "reason": "process_exited_early",
                "exit_code": exit_code,
                "bootstrap_log": self._summary_worker_bootstrap_log_path,
            }

        self._summary_worker_started = True
        logger.info(
            "History summary worker started | model=%s | launcher=%s | pid=%s | log=%s | bootstrap_log=%s",
            model_key,
            worker_launcher,
            process.pid,
            self._summary_worker_log_path,
            self._summary_worker_bootstrap_log_path,
        )
        return {
            "started": True,
            "model": model_key,
            "launcher": worker_launcher,
            "pid": process.pid,
            "log": self._summary_worker_log_path,
            "bootstrap_log": self._summary_worker_bootstrap_log_path,
        }
    except Exception as error:
        logger.exception("Failed to start history summary worker: %s", error)
        return {"started": False, "reason": str(error)}
