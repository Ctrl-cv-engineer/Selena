# 🔄 LLM 供应商

> Selena 通过 OpenAI 兼容协议接入 LLM 供应商，**任何提供 OpenAI 风格 `/chat/completions` 接口的模型都能用**。本篇说明默认支持的供应商、如何切换、以及如何接入新模型。

---

## 1. 默认支持的供应商

`config.example.json` 中预设了 6 家：

| 供应商 | 别名 | 适合 |
|--------|------|------|
| **阿里云通义千问** | `qwen` | 国内速度快、有 character 模型 |
| **Moonshot Kimi** | `kimi` | 长上下文、内置 web search |
| **MiniMax** | `minimax` | 角色扮演 / 高速版 |
| **DeepSeek** | `deepseek` | 综合表现强、有 reasoning |
| **小米 Mimo** | `mimo` | 国产新选择 |
| **OpenRouter** | `openrouter` | 一个 Key 通用所有海外模型（GPT / Claude / Gemini / Grok） |

> ⚠️ 配置文件中的具体模型 ID 与 base_url 是示意，**请以各家官方最新文档为准**。

---

## 2. 三层模型映射

Selena 用三层映射来灵活切换模型：

```
任务 (ModelSelect.Agent)
  ↓ 按 model 字段查找
模型别名 (qwen_flash, deepseek_pro, ...)
  ↓ 按 provider.models.<alias> 查找
真实模型 ID + URL (qwen-flash, deepseek-v4-pro, ...)
```

### 第一层：任务 → 别名（ModelSelect）

```json
{
  "ModelSelect": {
    "Agent": {
      "enabled": true,
      "model": "deepseek_flash",
      "thinking": true,
      "json_mode": false,
      "reasoning_effort": "max"
    },
    "RolePlay": {
      "enabled": true,
      "model": "deepseek_pro"
    }
  }
}
```

每个任务可以独立选模型 + 独立配 thinking / json_mode / reasoning_effort。

### 第二层：别名 → 真实模型

```json
{
  "providers": {
    "deepseek": {
      "api_key": "sk-...",
      "base_url": "https://api.deepseek.com/v1/chat/completions",
      "models": {
        "deepseek_flash": "deepseek-v4-flash",
        "deepseek_pro": "deepseek-v4-pro"
      }
    }
  }
}
```

### 第三层（可选）：单别名覆盖 URL

```json
{
  "qwen": {
    "models": {
      "qwen_chara": {
        "model": "qwen-plus-character",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
      }
    }
  }
}
```

特定模型需要不同 endpoint 时这样写。

---

## 3. 任务类型与推荐模型

| 任务 | 类型 | 推荐特性 |
|------|------|---------|
| `Agent` | Agent 主循环工具规划 | reasoning 强，thinking 必开 |
| `Simple` | 普通回复 | 平衡速度与质量 |
| `RolePlay` | 角色扮演 | 创作能力强、温度可调 |
| `LiteraryCreation` | 长文创作 | 同上 + thinking |
| `SummaryAndMermory` | 摘要生成 | json_mode 必开 |
| `topic_same` | 二分判定 | **快**，json_mode 必开 |
| `topic_archive_summary` | 话题归档摘要 | 中等推理，长上下文 |
| `context_summary` | 上下文压缩 | thinking + 中等 effort |
| `SilenceFollowUpPrompt` | 静默跟进文案 | 同 RolePlay |
| `core_memory_update` | ContextMemory 重写 | json_mode 必开 |
| `LLMIntentRouter` | 意图灰区复核 | **快**，json_mode 必开 |
| `SkillEvolutionEval` | 技能演化评估 | json_mode 必开 |
| `AgentTestJudge` | 自动化测试判卷 | thinking + json_mode |

### 一些推荐组合

**🚀 性价比配置**
```json
{
  "Agent": { "model": "deepseek_flash", "thinking": true },
  "Simple": { "model": "qwen_flash" },
  "topic_same": { "model": "qwen_flash" },
  "core_memory_update": { "model": "deepseek_flash" }
}
```

**💎 极致质量配置**
```json
{
  "Agent": { "model": "deepseek_pro", "thinking": true, "reasoning_effort": "max" },
  "Simple": { "model": "deepseek_pro" },
  "RolePlay": { "model": "minimax_chara" },
  "LiteraryCreation": { "model": "deepseek_pro", "thinking": true }
}
```

**🌍 海外模型（通过 OpenRouter）**
```json
{
  "providers": {
    "openrouter": {
      "models": {
        "claude": "anthropic/claude-3.5-sonnet",
        "gpt5": "openai/gpt-5",
        "gemini": "google/gemini-2.0-pro"
      }
    }
  },
  "ModelSelect": {
    "Agent": { "model": "claude", "thinking": true }
  }
}
```

---

## 4. 接入新供应商

只要供应商提供 OpenAI 兼容的 `/chat/completions`，三步搞定：

### 1) 加 provider
```json
{
  "providers": {
    "my_provider": {
      "api_key": "sk-xxx",
      "base_url": "https://api.example.com/v1/chat/completions",
      "models": {
        "my_model": "actual-model-id"
      }
    }
  }
}
```

### 2) 在 ModelSelect 引用
```json
{
  "ModelSelect": {
    "Agent": { "model": "my_model" }
  }
}
```

### 3) 重启 Selena

---

## 5. Embedding 与 Rerank

记忆系统的向量化和精排是**独立服务**：

```json
{
  "Embedding_Setting": {
    "qwen_embedding_modelName": "text-embedding-v4",
    "qwen_embedding_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
    "qwen_key": "sk-..."
  },
  "Rerank_Setting": {
    "qwen_rerank_modelName": "qwen3-rerank",
    "qwen_rerank_url": "https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
    "qwen_rerank_key": "sk-..."
  }
}
```

> ⚠️ Embedding 维度必须和 Qdrant collections 中的 `vector_size` 匹配。当前默认 `text-embedding-v4` 输出 512 维，对应 collections 大多是 `_512` 后缀。

### 本地 Embedding（可选）
如果不想用云端 Embedding，启用 `MemorySystem/Embedding.py`（gitignored，需要自己写）+ `sentence-transformers`：

```bash
pip install sentence-transformers
```

模型推荐：`BAAI/bge-small-zh-v1.5`（512 维，对中文友好）。

---

## 6. 思考模式（Thinking / Reasoning）

许多新一代模型支持"思考模式"（DeepSeek-R1、Kimi K2-thinking、Claude 3.7 thinking、GPT-5 reasoning…）。Selena 通过两个字段控制：

| 字段 | 含义 |
|------|------|
| `thinking` | 是否请求思考能力（开 = 模型先推理再回答） |
| `reasoning_effort` | 推理强度，`low` / `high` / `max` |

**使用建议**：

- **要开 thinking 的任务**：Agent、LiteraryCreation、SummaryAndMermory、core_memory_update。
- **不要开 thinking 的任务**：Simple、RolePlay（情感回复，思考反而僵硬）、LLMIntentRouter（要快）。

不同供应商的 thinking 字段实现略有差异，Selena 在 `llm/` 模块中做了适配。

---

## 7. JSON Mode

`json_mode = true` 让模型只输出严格的 JSON object，适合需要程序解析的场景：

- `topic_same`：判定结果
- `core_memory_update`：结构化关键记忆
- `LLMIntentRouter`：路由决策
- `SkillEvolutionEval`：演化评估
- `SummaryAndMermory`：结构化摘要

> ⚠️ 不是所有供应商都支持 json_mode。不支持的，Selena 会回退到普通模式 + 手动 JSON 解析。

---

## 8. 一份 Key 用所有模型

OpenRouter 是个特殊推荐：

```json
{
  "openrouter": {
    "api_key": "sk-or-...",
    "base_url": "https://openrouter.ai/api/v1/chat/completions",
    "models": {
      "claude":  "anthropic/claude-3.5-sonnet",
      "gpt5":    "openai/gpt-5",
      "gemini":  "google/gemini-2.0-pro",
      "grok":    "x-ai/grok-4",
      "llama":   "meta-llama/llama-3.3-70b-instruct"
    }
  }
}
```

一个 Key、一个 URL，可以用任何主流模型，对比测试时极方便。

---

## 9. 切换模型不重启

通过 [Web 工作台 / ConfigEditor](./frontend-workbench.md#-configeditor--配置编辑) 修改 `ModelSelect` 后会立即落盘，**新的对话立刻生效**（已经在跑的请求不受影响）。

但修改 `providers.<x>.api_key` / `base_url` 这类字段需要重启主程序。

---

## 10. 相关文档

- [Agent 主循环](./agent-loop.md) — ModelSelect 的具体含义
- [分层记忆系统](./memory-system.md) — Embedding / Rerank 在记忆中的作用
- [配置参考](../CONFIG_REFERENCE.md#llm_setting)
