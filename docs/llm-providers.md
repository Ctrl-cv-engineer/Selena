[简体中文](./zh-CN/llm-providers.md)

# LLM Providers

Selena supports multiple OpenAI-compatible providers through a model-alias layer in `config.json`.

## 1. Default providers

The current config structure is built around providers such as:

- `qwen`
- `kimi`
- `minimax`
- `deepseek`
- `mimo`
- `openrouter`

The exact list can change over time because the runtime is provider-driven rather than hardcoded to a single vendor.

## 2. Three-layer model mapping

### Layer 1: task -> alias

`ModelSelect` decides which alias a given task should use.

### Layer 2: alias -> real model

`LLM_Setting.providers.<provider>.models` maps Selena's internal alias to the provider's real model ID.

### Layer 3: optional alias-specific URL override

Some aliases can also override the URL if a single model path needs a custom endpoint.

## 3. Task types and model recommendations

Typical separation looks like this:

| Task type | What to optimize for |
| --- | --- |
| `Simple` | speed and low cost |
| `Agent` | stronger reasoning and tool planning |
| `SummaryAndMermory` | compact, stable summarization |
| `LLMIntentRouter` | cheap classification-style decisions |
| `SkillEvolutionEval` | structured evaluation quality |

## 4. Adding a new provider

1. Add the provider block under `LLM_Setting.providers`.
2. Add model aliases under its `models`.
3. Point `ModelSelect` entries to those aliases.
4. Restart Selena.

## 5. Embedding and rerank

Provider setup is not only about chat models. Embedding and rerank models are configured separately through:

- `Embedding_Setting`
- `Rerank_Setting`

### Local embeddings

If you choose local embedding, make sure the required model files are available and the runtime path is configured intentionally.

## 6. Thinking / reasoning mode

If the provider supports it, Selena can ask for reasoning-heavy behavior through fields such as:

- `thinking`
- `reasoning_effort`

## 7. JSON mode

`json_mode` is useful when the runtime expects structured output that downstream code will parse.

## 8. One key for many models

It is normal for several aliases to share the same provider key. Selena's alias system is about routing and specialization, not one-key-per-alias isolation.

## 9. Switching models without code changes

Most model swaps can be done entirely in config by updating aliases and `ModelSelect` assignments, which makes experimentation much easier than hardcoding model names in the runtime.

## 10. Related documents

- [Config reference](../CONFIG_REFERENCE.md)
- [Intent routing](./intent-routing.md)
- [Agent loop](./agent-loop.md)
