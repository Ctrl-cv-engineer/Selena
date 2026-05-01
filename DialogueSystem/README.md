# DialogueSystem 目录说明

这个目录承载了对话系统的主运行时、静态资源以及运行期产物。下面按“维护时最常接触的内容”说明各部分职责，方便后续继续拆分和维护。

## Python 模块

- 根目录
  - `main.py`：顶层运行入口，负责启动 Selena、维护对话上下文、做意图路由、调用 Agent 工具规划，并拉起后台摘要 worker。
  - `Demo.py`：开发期手动测试脚本，不参与正式主循环。
  - `__init__.py`：暴露包级公共导出。
- `agent/`
  - Agent 主循环、会话状态、子代理调度、token 预算，以及工具展示/结果压缩辅助。
- `autonomous/`
  - 自主任务模式的规划、执行和 SQLite 日志持久化。
- `browser/`
  - 浏览器控制、兼容适配与增强型网页工具。
- `config/`
  - 统一路径常量、prompt / tool / skill 静态资源加载。
- `llm/`
  - LLM / Embedding / Rerank HTTP 调用封装。
- `memory/`
  - 上下文记忆、话题历史、历史摘要 worker、本地记忆存储，以及摘要 worker 启动辅助。
- `policy/`
  - 工具元数据与安全策略判定。
- `runtime/`
  - 动态工具、MCP 桥接和本地前端 API 运行时。
- `services/`
  - 用户侧服务能力，目前主要是日程提醒与任务管理。
- `skill_system/`
  - 技能加载、技能管理与技能市场相关逻辑。

## 静态资源目录

- `MdFile/`
  - 存放系统 prompt 与意图示例生成 prompt，按功能分为子目录：
    - `agent/` — Agent 与自主任务相关提示词
    - `intent/` — 意图识别与路由提示词
    - `memory/` — 记忆系统提示词
    - `topic/` — 话题与上下文提示词
    - `dialogue/` — 对话人设与基础提示词
    - `skill/` — 技能系统提示词
  - 这些文件直接影响模型行为，修改时应视为”运行逻辑的一部分”，不要随意改文案。
- `tools/`
  - 普通 function tool 的 JSON 定义，供 Agent 规划时使用。
- `skills/`
  - 技能 manifest 及技能附带的工具定义。
  - 当前 `web_access` 技能通过 builtin tool 让模型直接走联网搜索能力。

## 运行期目录

- `history/`
  - 原始对话历史落盘目录。
  - `raw_dialogue_*.jsonl` 为会话原始消息文件，按 `topicGroup` 切分。
  - `.summary_memory_state.json` 和 `.summary_memory_worker.lock` 为摘要 worker 的状态与锁文件。
- `logs/`
  - 当前使用中的日志目录。
  - `dialogue_system.log*` 记录主对话进程日志。
  - `history_summary_worker*.log` 记录后台摘要 worker 日志。
- `__pycache__/`
  - Python 运行期缓存，可忽略，不应手工维护。

## 维护建议

- 优先在对应功能子包内改动，再回到 `main.py` 调整入口拼装逻辑，风险会更低。
- 如果后续继续拆 `main.py`，优先考虑沿着“意图路由”“RAG/长期记忆检索”“工具展示/审批”这几条边界继续下沉模块，避免回到单文件累积逻辑的状态。
- `history/` 和 `logs/` 里的内容属于运行产物，排查问题时可看，但不建议把“给它们加注释”当成代码维护目标。
