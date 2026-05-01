[简体中文](./CONFIG_REFERENCE.zh-CN.md)

# Config Reference

This document explains the main fields currently used in `config.json`.

It is intentionally written as a practical operator reference rather than a literal schema dump. Field names stay exactly as they appear in the config file, while the explanations below focus on what each group controls and what to review before deployment.

## General rules

- `config.json` often contains sensitive values such as `api_key`, `auth_token`, and local paths. Do not commit a real runtime config to a public repository.
- The frontend config editor writes this file directly.
- Changes related to the frontend listener, ports, or auto-start usually require a runtime restart to take full effect.
- Many model-related entries reuse the same shape:

```json
{
  "enabled": true,
  "model": "deepseek_flash",
  "thinking": true,
  "json_mode": false,
  "reasoning_effort": "high"
}
```

Common meanings:

- `enabled`: turn the feature chain on or off
- `model`: internal alias from `LLM_Setting.providers.*.models`
- `thinking`: request reasoning/thinking mode when supported
- `json_mode`: ask for JSON-shaped output when the caller parses the result
- `reasoning_effort`: reasoning strength hint such as `low`, `high`, or `max`

## Memory architecture

Selena's memory stack is layered:

- Static role/system prompts define persona and baseline behavior.
- `ContextMemory` keeps always-relevant core memory.
- Live topic context keeps recent conversation state for the current thread.
- Topic archives preserve older grouped conversation history.
- Long-term vector memory stores retrievable atomic memories with metadata such as TTL, temperature, and search score.

## `LLM_Setting`

### `LLM_Setting.default_model`

Fallback model alias when a task does not select one explicitly.

### `LLM_Setting.providers`

Defines all available model providers. The current project references providers such as `qwen`, `kimi`, `minimax`, `deepseek`, `mimo`, and `openrouter`.

Important nested fields:

- `api_key`: primary credential for that provider
- `free_api_key`: optional alternate key used by some compatible paths
- `base_url`: default OpenAI-compatible endpoint
- `models`: mapping from Selena's internal alias to the real provider model

Models can be mapped in two ways:

- Simple string alias: `"qwen": "qwen3.5-flash"`
- Object form: `{ "model": "qwen-plus-character", "url": "..." }`

Use the object form when a single alias needs a custom URL override.

## `Embedding_Setting`

Controls the embedding provider and endpoint used for vectorization.

Typical fields:

- `qwen_embedding_modelName`
- `qwen_embedding_url`
- `qwen_key`

If you do not rely on local embeddings, this is where your remote embedding setup matters most.

## `Rerank_Setting`

Controls the rerank model and endpoint used after the initial vector retrieval.

Typical fields:

- `qwen_rerank_modelName`
- `qwen_rerank_url`
- `qwen_rerank_key`

## `ModelSelect`

`ModelSelect` is the task-to-model-alias routing layer.

Common task entries include:

- `Agent`
- `Simple`
- `RolePlay`
- `LiteraryCreation`
- `SummaryAndMermory`
- `topic_same`
- `topic_archive_summary`
- `context_summary`
- `SilenceFollowUpPrompt`
- `core_memory_update`
- `LLMIntentRouter`
- `SkillEvolutionEval`
- `AgentTestJudge`

In practice, this section is one of the best places to tune cost, latency, and model specialization without changing code.

## `SkillEvolution`

Controls whether repeated tool patterns can be evaluated and turned into reusable skills.

Key fields:

- `enabled`
- `min_tool_calls`
- `similarity_threshold`

## `AgentRuntime`

Controls main-loop guardrails.

Important fields:

- `max_tool_calls`
- `max_consecutive_same_tool_calls`

These two limits are the first things to inspect when an agent seems too timid or too expensive.

## `IntentRouter`

Controls whether Selena decides between lightweight reply mode and agent mode automatically.

Key fields:

- `method`: usually `vector` or `llm`
- `enabled`
- `high_confidence_threshold`
- `low_confidence_threshold`
- `candidate_limit`
- `llm_fallback`

The `vector` path uses thresholds and candidate limits heavily; the `llm` path trades more cost for more direct reasoning.

## `ContextMemory`

Controls the always-injected core memory layer.

Important fields:

- `enabled`
- `update_trigger`
- `update_min_new_messages`
- `update_on_empty`
- `max_chars`
- `max_items_total`
- `max_items_per_section`
- `max_item_chars`
- `recent_message_limit`

If core memory becomes noisy or stale, this section is usually where the fix starts.

## `AgentRetrievalCache`

Controls reuse of previous retrieval results inside agent workflows.

Key fields:

- `enabled`
- `match_model`
- `match_thinking`
- `match_json_mode`
- `match_reasoning_effort`
- `max_cache_inject_chars`
- `cacheable_tools`

This feature can reduce repeated tool cost during long tasks, but too much injection can also bloat context windows.

## `Summary`

Controls summarization limits and context budgets.

Typical fields:

- `Max_context`
- `Summary_context`
- `Agent_Max`

## `SilentTime`

Controls time windows related to idle behavior.

Typical fields:

- `First`
- `Last`

## `Qdrant_Setting`

Defines where Qdrant runs and how collections are named.

Main fields:

- `host`
- `port`
- `grpc_port`
- `prefer_grpc`
- `local_data_path`
- `docker_data_path`
- `collections`

Each collection entry usually contains:

- `name`
- `vector_size`

Current collection roles typically include:

- `intention`: intent examples and routing support
- `rag`: retrieval content
- `memory`: long-term memory
- `web_embedding`: web-derived embedding content

## `Frontend`

Controls the local web UI runtime.

Fields to review:

- `enabled`
- `auto_start`
- `host`
- `port`
- `api_port`
- `package_manager`

## `MCP`

Controls Model Context Protocol integration.

Main fields:

- `enabled`
- `servers`

Per server entry:

- `name`
- `enabled`
- `url`
- `auth_token`

## `Security`

This section matters a lot before public or production use.

Important fields:

- `is_admin`
- `approval_mode`
- `allow_local_terminal`
- `enabled_toolsets`
- `approved_tools`
- `file_roots`

Review these carefully if the model can touch local files, shells, or browsers.

## `ExecutionBackends`

Controls which backend is used for terminal execution.

Main field:

- `terminal.default_backend`

## `SubAgentPolicy`

Defines limits and tool policies for delegated agents.

Global fields include:

- `max_depth`
- `allow_admin_tools`
- `max_concurrent_tasks`
- `max_queue_size`
- `default_priority`
- `result_cache_enabled`
- `result_cache_ttl_seconds`
- `result_cache_max_entries`
- `toolsets`

Per agent type you can further define:

- `toolsets`
- `max_tool_calls`
- `resource_limits.max_file_reads`
- `resource_limits.max_file_writes`
- `resource_limits.max_network_calls`

Current built-in agent roles commonly include:

- `general`
- `explore`
- `research`
- `plan`
- `review`
- `test`

## `AutonomousTaskMode`

Controls idle-time autonomous task planning and execution.

Top-level fields:

- `enabled`
- `idle_threshold_seconds`
- `max_daily_tasks`
- `max_task_attempts`
- `max_daily_interrupts`
- `cancel_wait_seconds`
- `stale_attempt_timeout_seconds`
- `summary_on_complete`

Token budget fields:

- `token_limits.max_input_tokens_per_session`
- `token_limits.max_output_tokens_per_session`
- `token_limits.max_input_tokens_per_task`
- `token_limits.max_output_tokens_per_task`

Execution fields:

- `task_planning`
- `task_execution.agent_type`
- `task_execution.max_tool_calls`
- `task_execution.timeout_seconds`

Sharing-related fields:

- `sharing_score`
- `sharing.min_score`
- `sharing.cooldown_days`
- `sharing.max_inject_count`
- `sharing.mention_detection.embedding_threshold`
- `sharing.mention_detection.keyword_min_hits`

## `VectorSetting`

Controls memory ranking, rerank behavior, duplication checks, and TTL upgrades or downgrades.

Fields to review most often:

- `Top_k`
- `Importance_Threshold`
- `Max_SearchScore`
- `Rerank_Scale`
- `Rerank_Top_k`
- `Duplicate_Vector_Threshold`
- `Upgrade_Score_Threshold`
- `Downgrade_Score_Threshold`
- `Upgrade_TTL_Multiplier`
- `Downgrade_TTL_Multiplier`
- `Default_TTL_Days`

Temperature weights such as `hot`, `warm`, and `cold` affect how strongly memory freshness changes retrieval ranking.

## `MemoryRecall`

Main field:

- `rerank_min_score`

Use this to decide how strict reranked recall should be before a memory is injected back into context.

## Recommended defaults

For a safer first setup:

- Start with one LLM provider that you trust and verify first.
- Keep `Security.is_admin = false`.
- Keep `Security.allow_local_terminal = false` unless you intentionally want local command execution.
- Keep `Frontend.enabled = true` if you want the easiest way to inspect state and config.
- Keep autonomous mode conservative until the base dialogue and tooling behavior feels stable.
