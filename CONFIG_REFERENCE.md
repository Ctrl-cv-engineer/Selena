# Config Reference

本文档用于解释 `config.json` 中当前实际使用的配置项含义。`config.json` 本身不支持注释，因此说明统一维护在这里。

## General Rules

- `config.json` 中的敏感字段很多，包括 `api_key`、`auth_token`、本地路径等，不建议直接提交到公共仓库。
- 前端 `/config` 页面会直接读写这个文件；保存成功后会立刻落盘。
- 与 `Frontend` 相关的监听地址、端口、自动启动设置，通常需要重启 Selena 主程序后才能完全生效。
- 多数“模型配置”都采用同一套字段结构：

```json
{
  "enabled": true,
  "model": "deepseek_flash",
  "thinking": true,
  "json_mode": false,
  "reasoning_effort": "high"
}
```

字段含义如下：

- `enabled`：是否启用该子任务链路。
- `model`：引用 `LLM_Setting.providers.*.models` 中定义的模型别名。
- `thinking`：是否向供应商请求 reasoning / thinking 能力。
- `json_mode`：是否要求模型按 JSON object 输出，适合需要程序解析结构化结果的场景。
- `reasoning_effort`：thinking 模式下的推理强度。项目里实际可见值包括 `low`、`high`、`max`。

## Memory Architecture

当前项目的上下文与记忆分层大致如下：

- 静态角色提示词：定义角色人格、行为边界和基础风格。
- `ContextMemory`：始终可见的关键记忆层，用于保存长期稳定但又需要高频注入的状态。
- 当前话题上下文：当前 topic 的最近对话与必要摘要。
- `topic archive`：旧话题的摘要与原始分组历史。
- 原子长期记忆：带向量索引、温度、TTL、SearchScore 的长期记忆库。

## `LLM_Setting`

### `LLM_Setting.default_model`

- 含义：默认模型别名。
- 作用：当某个任务没有显式指定模型时，系统可用它兜底。

### `LLM_Setting.providers`

- 含义：所有 LLM 供应商定义。
- 当前配置包含：`qwen`、`kimi`、`minimax`、`deepseek`、`mimo`、`openrouter`。

### `LLM_Setting.providers.<provider>.api_key`

- 含义：该供应商的正式 API Key。
- 作用：大部分正常请求都会优先使用这里做鉴权。

### `LLM_Setting.providers.kimi.free_api_key`

- 含义：Kimi 兼容链路中的备用免费 Key。
- 说明：通常优先级低于 `api_key`，主要用于兼容或实验场景。

### `LLM_Setting.providers.<provider>.base_url`

- 含义：该供应商默认的 OpenAI 兼容聊天接口地址。

### `LLM_Setting.providers.<provider>.models`

- 含义：项目内部模型别名到真实模型 ID 的映射表。
- 作用：`ModelSelect.*.model` 填写的是这里的“别名”，不是供应商原始模型名。

### `LLM_Setting.providers.<provider>.models.<alias>`

- 含义：一个可以被业务代码引用的模型别名。
- 支持两种写法：
  - 直接写字符串，例如 `"qwen": "qwen3.5-flash"`。
  - 写对象，例如 `{ "model": "qwen-plus-character", "url": "..." }`。

### `LLM_Setting.providers.<provider>.models.<alias>.model`

- 含义：该别名最终请求的真实模型 ID。

### `LLM_Setting.providers.<provider>.models.<alias>.url`

- 含义：只覆盖当前模型别名使用的接口地址。
- 说明：不填时沿用 provider 级别的 `base_url`。

## `Embedding_Setting`

### `Embedding_Setting.qwen_embedding_modelName`

- 含义：Embedding 模型名称。
- 作用：所有向量化请求最终会使用它生成 embedding。

### `Embedding_Setting.qwen_embedding_url`

- 含义：Embedding 接口地址。

### `Embedding_Setting.qwen_key`

- 含义：Embedding 接口使用的鉴权密钥。

## `Rerank_Setting`

### `Rerank_Setting.qwen_rerank_modelName`

- 含义：Rerank 模型名称。
- 作用：向量召回后，使用该模型对候选结果做二次排序。

### `Rerank_Setting.qwen_rerank_url`

- 含义：Rerank 接口地址。

### `Rerank_Setting.qwen_rerank_key`

- 含义：Rerank 接口鉴权密钥。

## `ModelSelect`

`ModelSelect` 负责把“不同子任务”路由到不同的模型配置。每个任务都使用前面提到的统一字段结构。

### 当前任务项用途

#### `ModelSelect.Agent`

- 用途：Agent 主流程，包括规划、调用工具、整理结果和最终产出。

#### `ModelSelect.Simple`

- 用途：Simple 风格回复链路。

#### `ModelSelect.RolePlay`

- 用途：RolePlay 人设回复，以及部分面向最终用户的 RAG 风格答案。

#### `ModelSelect.LiteraryCreation`

- 用途：更偏创作、扩写、长文本表达的链路。

#### `ModelSelect.SummaryAndMermory`

- 用途：历史总结、记忆提炼等后台摘要型任务。

#### `ModelSelect.topic_same`

- 用途：判断当前消息是否仍属于同一话题。
- 建议：通常配 `json_mode = true`，因为程序需要解析结构化结果。

#### `ModelSelect.topic_archive_summary`

- 用途：旧话题归档前生成摘要。

#### `ModelSelect.context_summary`

- 用途：当前 live context 太长时压缩上下文。

#### `ModelSelect.SilenceFollowUpPrompt`

- 用途：用户静默较久后生成跟进文案。

#### `ModelSelect.core_memory_update`

- 用途：更新 `ContextMemory` 关键记忆 state。
- 建议：通常配 `json_mode = true`，因为代码会解析结构化记忆结果。

#### `ModelSelect.LLMIntentRouter`

- 用途：当 `IntentRouter.method = "llm"` 时，负责判断最后一条消息是否需要工具调用。

#### `ModelSelect.SkillEvolutionEval`

- 用途：评估一轮工具流程是否值得沉淀成新 skill。

#### `ModelSelect.AgentTestJudge`

- 用途：Agent 自动化测试时对结果做 LLM 判卷。

## `SkillEvolution`

### `SkillEvolution.enabled`

- 含义：是否启用自动技能演化。
- 作用：允许系统把重复、稳定的工具流程沉淀成可复用 skill。

### `SkillEvolution.min_tool_calls`

- 含义：一轮流程至少要调用多少次工具，才有资格进入“是否值得沉淀”的评估。

### `SkillEvolution.similarity_threshold`

- 含义：候选 skill 与已有 skill 的相似度阈值。
- 作用：太相似时不再重复创建，避免 skill 泛滥。

## `AgentRuntime`

### `AgentRuntime.max_tool_calls`

- 含义：主 Agent 在单轮工作流中允许执行的普通工具调用总上限。

### `AgentRuntime.max_consecutive_same_tool_calls`

- 含义：同一个工具连续调用达到该次数后，系统会强制进入整理/收尾逻辑。
- 作用：避免重复调用同一工具导致循环。

## `IntentRouter`

### `IntentRouter.method`

- 含义：意图路由模式。
- 可选值：
  - `vector`：先走向量意图库，再用灰区 LLM 复核。
  - `llm`：完全由 LLM 直接判断是否需要工具调用。

### `IntentRouter.enabled`

- 含义：是否启用混合路由开关。
- 说明：主要在 `method = vector` 时有意义。

### `IntentRouter.high_confidence_threshold`

- 含义：高置信阈值。
- 作用：向量召回最高分不低于该值时，直接进 Agent，不再复核。

### `IntentRouter.low_confidence_threshold`

- 含义：低置信阈值。
- 作用：向量召回最高分不高于该值时，直接走 RAG；中间灰区则交给 LLM 复核。

### `IntentRouter.candidate_limit`

- 含义：送给灰区复核模型的候选能力数上限。

### `IntentRouter.llm_fallback`

- 含义：灰区复核阶段所使用的模型配置。
- 字段：同通用模型配置结构。

## `ContextMemory`

### `ContextMemory.enabled`

- 含义：是否维护关键记忆层。

### `ContextMemory.update_trigger`

- 含义：关键记忆的刷新时机。
- 当前支持：
  - `assistant_turn`：每次回复后刷新。
  - `topic_switch`：只在话题切换时刷新。
  - `topic_switch_or_interval`：话题切换或累计一定新消息后刷新。

### `ContextMemory.update_min_new_messages`

- 含义：在 `topic_switch_or_interval` 模式下，累计到多少条新消息后允许刷新。

### `ContextMemory.update_on_empty`

- 含义：关键记忆为空时是否允许冷启动生成第一版。

### `ContextMemory.max_chars`

- 含义：关键记忆渲染到 prompt 时允许占用的总字符上限。

### `ContextMemory.max_items_total`

- 含义：关键记忆条目总数上限。

### `ContextMemory.max_items_per_section`

- 含义：单个 section 默认最多保留多少条关键记忆。

### `ContextMemory.max_item_chars`

- 含义：单条关键记忆的字符上限。

### `ContextMemory.recent_message_limit`

- 含义：更新关键记忆时，最多向后回看多少条最近消息。

## `AgentRetrievalCache`

该模块用于缓存“检索型工具”的结果，并在用户追问时尝试复用。

### `AgentRetrievalCache.enabled`

- 含义：是否启用 Agent 检索缓存。

### `AgentRetrievalCache.match_model`

- 含义：判断“当前问题是否可复用旧检索结果”时所使用的模型别名。

### `AgentRetrievalCache.match_thinking`

- 含义：缓存匹配判断阶段是否开启 thinking。

### `AgentRetrievalCache.match_json_mode`

- 含义：缓存匹配判断阶段是否按 JSON 模式输出。

### `AgentRetrievalCache.match_reasoning_effort`

- 含义：缓存匹配判断阶段的推理强度。

### `AgentRetrievalCache.max_cache_inject_chars`

- 含义：命中缓存后，最多把多少字符的原始检索文本直接注入当前上下文。

### `AgentRetrievalCache.cacheable_tools`

- 含义：允许被缓存的工具白名单。
- 当前配置里的典型条目：
  - `webSearch`
  - `webFetch`
  - `browserExtractPage`
  - `readAutonomousTaskArtifact`
  - `searchAutonomousTaskArtifacts`
  - `searchLongTermMemory`
  - `searchFullText`
  - `readLocalFile`

## `Summary`

### `Summary.Max_context`

- 含义：live context 超过多少条消息后触发上下文摘要。

### `Summary.Summary_context`

- 含义：摘要后从哪一段开始保留原始消息。
- 直观理解：越小表示压缩得越狠。

### `Summary.Agent_Max`

- 含义：Agent 链路中可保留的上下文消息上限。

## `SilentTime`

### `SilentTime.First`

- 含义：用户静默多久后触发第一阶段跟进。

### `SilentTime.Last`

- 含义：用户静默多久后触发最终阶段跟进。

## `Qdrant_Setting`

### `Qdrant_Setting.host`

- 含义：Qdrant 主机地址。

### `Qdrant_Setting.port`

- 含义：Qdrant HTTP API 端口。

### `Qdrant_Setting.grpc_port`

- 含义：Qdrant gRPC 端口。

### `Qdrant_Setting.prefer_grpc`

- 含义：是否优先通过 gRPC 访问 Qdrant。

### `Qdrant_Setting.local_data_path`

- 含义：本地方式运行 Qdrant 时的数据目录。

### `Qdrant_Setting.docker_data_path`

- 含义：Docker 方式运行 Qdrant 时映射的数据目录。

### `Qdrant_Setting.collections`

- 含义：各类 collection 的名称和向量维度定义。

### `Qdrant_Setting.collections.<collection>.name`

- 含义：运行时真正连接的 collection 名称。

### `Qdrant_Setting.collections.<collection>.vector_size`

- 含义：该 collection 预期使用的向量维度。
- 注意：必须和实际 embedding 输出维度一致。

### `Qdrant_Setting.collections.rag.test`

- 含义：RAG 相关的测试 / 备用集合名。
- 说明：主要用于测试或迁移阶段，不是主链路默认集合名。

### 当前 collection 角色

#### `Qdrant_Setting.collections.intention`

- 作用：意图路由所使用的意图库集合。

#### `Qdrant_Setting.collections.rag`

- 作用：RAG / 记忆检索主集合之一。

#### `Qdrant_Setting.collections.memory`

- 作用：长期原子记忆相关集合。

#### `Qdrant_Setting.collections.web_embedding`

- 作用：网页抽取内容的向量存储集合。

## `Frontend`

### `Frontend.enabled`

- 含义：是否启用内置前端。

### `Frontend.auto_start`

- 含义：启动主程序时是否自动拉起前端服务。

### `Frontend.host`

- 含义：前端页面和本地 API 绑定的监听地址。

### `Frontend.port`

- 含义：浏览器访问前端页面时使用的端口。

### `Frontend.api_port`

- 含义：本地前端 API 端口。

### `Frontend.package_manager`

- 含义：自动启动前端时所使用的包管理器命令。
- 当前常见值：`pnpm`、`npm`、`yarn`、`bun`。

## `MCP`

### `MCP.enabled`

- 含义：是否启用 MCP 动态工具发现能力。

### `MCP.servers`

- 含义：MCP 服务器列表。
- 每个元素都是一个对象，当前结构如下：

```json
{
  "name": "example",
  "enabled": false,
  "url": "http://127.0.0.1:9000/mcp",
  "auth_token": ""
}
```

### `MCP.servers[].name`

- 含义：MCP 服务器在系统内的逻辑名称。

### `MCP.servers[].enabled`

- 含义：是否启用该服务器。

### `MCP.servers[].url`

- 含义：MCP JSON-RPC HTTP 地址。

### `MCP.servers[].auth_token`

- 含义：可选 Bearer Token。
- 说明：为空时通常不会发送 `Authorization` 头。

## `Security`

`Security` 是工具级安全边界的核心配置，运行时会被 `ToolPolicyEngine` 读取。

### `Security.is_admin`

- 含义：当前会话是否默认具备管理员级工具能力。
- 影响：管理员级工具通常涉及文件写入、高权限执行等高风险能力。

### `Security.approval_mode`

- 含义：审批模式。
- 当前可见值：
  - `manual`：高风险工具先请求用户审批。
  - `off`：关闭审批。

### `Security.allow_local_terminal`

- 含义：是否允许终端类工具直接使用本机本地执行后端。
- 风险：开启后，终端执行不会被限制在隔离后端里。

### `Security.enabled_toolsets`

- 含义：启用的工具集白名单。
- 当前系统中的典型工具集包括：
  - `core`
  - `memory`
  - `schedule`
  - `browser`
  - `file_read`
  - `file_write`
  - `terminal`
  - `subagent`
  - `mcp`
  - `skill_admin`

### `Security.approved_tools`

- 含义：已经被用户永久批准的工具名列表。
- 说明：运行时审批通过后，系统可能把结果回写到这里。

### `Security.file_roots`

- 含义：文件读写类工具允许访问的根目录列表。
- 作用：任何文件路径如果不在这些根目录或其子目录中，策略层会拒绝。

## `ExecutionBackends`

### `ExecutionBackends.terminal.default_backend`

- 含义：终端类工具默认使用的执行后端。
- 可选值：
  - `isolated`：走隔离执行器。
  - `local`：直接在当前 workspace 上执行。

## `SubAgentPolicy`

`SubAgentPolicy` 用于控制委派任务（SubAgent）的深度、并发、缓存和不同类型 agent 的资源配额。

### 全局字段

#### `SubAgentPolicy.max_depth`

- 含义：SubAgent 递归创建 SubAgent 的最大深度。
- 作用：防止无限递归委派。

#### `SubAgentPolicy.allow_admin_tools`

- 含义：是否允许 SubAgent 使用管理员级工具。

#### `SubAgentPolicy.max_concurrent_tasks`

- 含义：同时运行的委派任务最大数量。

#### `SubAgentPolicy.max_queue_size`

- 含义：等待队列上限。
- 说明：超过后新任务会被拒绝或不能继续入队。

#### `SubAgentPolicy.default_priority`

- 含义：委派任务的默认优先级。
- 说明：数值越大通常越优先。

#### `SubAgentPolicy.result_cache_enabled`

- 含义：是否启用委派结果缓存。

#### `SubAgentPolicy.result_cache_ttl_seconds`

- 含义：委派结果缓存的 TTL 秒数。

#### `SubAgentPolicy.result_cache_max_entries`

- 含义：委派结果缓存最多保留多少条。

#### `SubAgentPolicy.toolsets`

- 含义：全局工具集覆盖。
- 说明：`null` 表示沿用每个 agent 类型自己的默认工具集。

### `SubAgentPolicy.agent_type_configs`

- 含义：不同 agent 类型的默认策略。
- 当前包含：`general`、`explore`、`research`、`plan`、`review`、`test`。

### `SubAgentPolicy.agent_type_configs.<type>.toolsets`

- 含义：该类型默认允许使用的工具集白名单。

### `SubAgentPolicy.agent_type_configs.<type>.max_tool_calls`

- 含义：该类型单任务允许的默认工具调用次数。

### `SubAgentPolicy.agent_type_configs.<type>.resource_limits.max_file_reads`

- 含义：该类型单任务的最大文件读取次数。

### `SubAgentPolicy.agent_type_configs.<type>.resource_limits.max_file_writes`

- 含义：该类型单任务的最大文件写入次数。

### `SubAgentPolicy.agent_type_configs.<type>.resource_limits.max_network_calls`

- 含义：该类型单任务的最大网络请求次数。

### 当前类型角色

#### `general`

- 角色：通用型委派。
- 特点：兼顾记忆、浏览器、日程和文件读取。

#### `explore`

- 角色：探索型委派。
- 特点：偏重快速阅读和文件定位，通常不给写权限，也不给网络权限。

#### `research`

- 角色：研究型委派。
- 特点：高读取、高网络配额，适合综合调研。

#### `plan`

- 角色：规划型委派。
- 特点：偏方案设计和结构化规划。

#### `review`

- 角色：审查型委派。
- 特点：高读取、低写入、无网络，适合代码审查。

#### `test`

- 角色：测试型委派。
- 特点：允许少量写入，适合跑测试和生成验证产物。

## `AutonomousTaskMode`

该模块控制“用户空闲时自动规划并执行后台任务”的能力。

### 顶层开关与节奏

#### `AutonomousTaskMode.enabled`

- 含义：是否启用自主任务模式。

#### `AutonomousTaskMode.idle_threshold_seconds`

- 含义：距离上次用户交互超过多少秒后，允许进入自主模式。

#### `AutonomousTaskMode.max_daily_tasks`

- 含义：单日最多执行多少个自主任务，包含结转任务。

#### `AutonomousTaskMode.max_task_attempts`

- 含义：同一任务最多允许创建多少次 attempt。

#### `AutonomousTaskMode.max_daily_interrupts`

- 含义：一天内最多允许被用户打断多少次。
- 说明：超过后当天通常不再重新进入自主模式。

#### `AutonomousTaskMode.cancel_wait_seconds`

- 含义：请求取消后台任务后，最多等待多久确认其进入终态。

#### `AutonomousTaskMode.stale_attempt_timeout_seconds`

- 含义：attempt 多久没有心跳或终态更新后，会被视为 stale。

#### `AutonomousTaskMode.summary_on_complete`

- 含义：当天任务全部完成或预算耗尽后，是否自动生成自主经历总结。

### `AutonomousTaskMode.token_limits`

- 含义：真实 token 预算。
- 说明：预算控制区分“单日会话”和“单任务”两个粒度。

#### `AutonomousTaskMode.token_limits.max_input_tokens_per_session`

- 含义：单日自主会话输入 token 总上限。

#### `AutonomousTaskMode.token_limits.max_output_tokens_per_session`

- 含义：单日自主会话输出 token 总上限。

#### `AutonomousTaskMode.token_limits.max_input_tokens_per_task`

- 含义：单个自主任务的输入 token 上限。

#### `AutonomousTaskMode.token_limits.max_output_tokens_per_task`

- 含义：单个自主任务的输出 token 上限。

### `AutonomousTaskMode.task_planning`

- 含义：生成“今日计划”阶段使用的模型配置。
- 字段：同通用模型配置结构。

### `AutonomousTaskMode.task_execution`

- 含义：后台执行自主任务时的配置。

#### `AutonomousTaskMode.task_execution.agent_type`

- 含义：自主任务执行阶段使用的 agent 类型。
- 当前示例：`autonomous`。
- 作用：让后台任务走专门的委派策略，而不是复用普通前台 agent。

#### `AutonomousTaskMode.task_execution.max_tool_calls`

- 含义：单个自主任务执行期允许调用的工具次数上限。

#### `AutonomousTaskMode.task_execution.timeout_seconds`

- 含义：单个自主任务从发起到等待结束的总超时时长。

### `AutonomousTaskMode.sharing_score`

- 含义：给自主经历打“是否值得分享给用户”的分数时使用的模型配置。
- 字段：同通用模型配置结构。

### `AutonomousTaskMode.sharing`

- 含义：控制自主经历最终是否注入到正常对话上下文。

#### `AutonomousTaskMode.sharing.min_score`

- 含义：最低分享分阈值。
- 说明：低于此分数的经历不会进入候选注入池。

#### `AutonomousTaskMode.sharing.cooldown_days`

- 含义：某条经历被提到后，需要冷却多少天才允许再次注入。

#### `AutonomousTaskMode.sharing.max_inject_count`

- 含义：单轮对话最多允许注入多少条自主经历。

#### `AutonomousTaskMode.sharing.mention_detection.embedding_threshold`

- 含义：用向量相似度判断“回复已经提及该经历”时的阈值。

#### `AutonomousTaskMode.sharing.mention_detection.keyword_min_hits`

- 含义：用关键词重叠判断“已经提及”时的最少命中数。

## `VectorSetting`

`VectorSetting` 决定长期记忆检索、去重、升降级、TTL 和 rerank 保留策略，是向量记忆系统的核心参数区。

### `VectorSetting.Top_k`

- 含义：向量检索的初始召回条数。

### `VectorSetting.Importance_Threshold`

- 含义：高重要度保护阈值。
- 作用：重要度超过该值的记忆，不会在衰减流程里被轻易删除。

### `VectorSetting.Max_SearchScore`

- 含义：SearchScore 上限。
- 作用：避免某条记忆因为反复命中而无限膨胀。

### `VectorSetting.Temperature_Weight.hot`

- 含义：hot 记忆的温度权重。

### `VectorSetting.Temperature_Weight.warm`

- 含义：warm 记忆的温度权重。

### `VectorSetting.Temperature_Weight.cold`

- 含义：cold 记忆的温度权重。

### `VectorSetting.Rerank_Scale`

- 含义：检索重排公式的统一缩放系数。

### `VectorSetting.Cold_SearchScore_Multiplier`

- 含义：处于 cold 状态且仍有一定价值的记忆，其 SearchScore 增益倍数。

### `VectorSetting.Other_SearchScore_Multiplier`

- 含义：非 cold 记忆在中间区间的 SearchScore 增益倍数。

### `VectorSetting.Duplicate_Vector_Threshold`

- 含义：向量去重阈值。
- 说明：相似度达到该值时，系统会把它视为“几乎同一条向量内容”。

### `VectorSetting.Upgrade_Score_Threshold`

- 含义：TTL 到期后，记忆被“升级续命”的 SearchScore 阈值。

### `VectorSetting.Downgrade_Score_Threshold`

- 含义：TTL 到期后，记忆被“降级”的 SearchScore 阈值。

### `VectorSetting.Upgrade_TTL_Multiplier`

- 含义：记忆升级后，新 TTL 相对于默认 TTL 的倍数。

### `VectorSetting.Downgrade_TTL_Multiplier`

- 含义：记忆降级后，新 TTL 相对于默认 TTL 的倍数。

### `VectorSetting.Default_TTL_Days`

- 含义：新增记忆默认的 TTL 天数。

### `VectorSetting.Rank_Score`

- 含义：rerank 后最终保留候选结果的基础阈值。

### `VectorSetting.Rerank_Top_k`

- 含义：初筛之后送入 rerank 的候选条数。

## `MemoryRecall`

### `MemoryRecall.rerank_min_score`

- 含义：原子记忆进入最终上下文前的最低 rerank 分数。
- 作用：低于该值的记忆，即使被召回，也不会被注入上下文。

## Recommended Defaults

- `ContextMemory.update_trigger` 通常建议优先使用 `topic_switch`，这样不会在每轮回复里频繁改写 always-visible prompt。
- `Security.allow_local_terminal` 建议默认保持 `false`，除非你明确需要本地直接执行。
- `SubAgentPolicy.toolsets = null` 通常比强行全局覆盖更灵活，因为它允许各 agent 类型保留自己的默认白名单。
- `VectorSetting` 和 `MemoryRecall` 建议小步调整；这两组参数会直接影响长期记忆召回质量与保留策略。
