"""集中管理 `DialogueSystem` 包内常用路径。"""

import os


# `SCRIPT_DIR` 指向 `DialogueSystem` 包在磁盘上的根目录。
_CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.dirname(_CONFIG_DIR)

# `PROJECT_ROOT` 指向仓库根目录。运行时会把它加入 `sys.path`，从而导入
# `MemorySystem` 之类的兄弟包。
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# `DialogueSystem` 运行时会用到的子目录。
PROMPTS_DIR = os.path.join(SCRIPT_DIR, "MdFile")
TOOLS_DIR = os.path.join(SCRIPT_DIR, "tools")
SKILLS_DIR = os.path.join(SCRIPT_DIR, "skills")
HISTORY_DIR = os.path.join(SCRIPT_DIR, "history")
LOGS_DIR = os.path.join(SCRIPT_DIR, "logs")
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
SCHEDULE_DB_PATH = os.path.join(DATA_DIR, "schedule_system.db")
TOPIC_ARCHIVE_DB_PATH = os.path.join(DATA_DIR, "topic_archive.db")
RETRIEVAL_CACHE_DB_PATH = os.path.join(DATA_DIR, "agent_retrieval_cache.db")
PERSISTENT_CORE_MEMORY_PATH = os.path.join(DATA_DIR, "persistent_core_memory.json")
AUTONOMOUS_TASK_DB_PATH = os.path.join(DATA_DIR, "autonomous_task_mode.db")

# 旧版本直接把日志写在 `DialogueSystem/` 根目录下。这里保留这些路径，是为了让
# 清理和兼容逻辑仍然能找到它们。
LEGACY_LOG_PATHS = {
    "dialogue_system": os.path.join(SCRIPT_DIR, "dialogue_system.log"),
    "history_summary_worker": os.path.join(SCRIPT_DIR, "history_summary_worker.log"),
    "history_summary_worker_bootstrap": os.path.join(SCRIPT_DIR, "history_summary_worker_bootstrap.log"),
}
