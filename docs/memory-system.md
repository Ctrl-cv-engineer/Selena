[简体中文](./zh-CN/memory-system.md)

# Layered Memory System

Selena's memory is intentionally layered so that short-term coherence and long-term recall can coexist without dumping everything into one giant prompt.

## 1. Why use layers

Different kinds of memory have different jobs:

- some facts should almost always stay visible,
- some context only matters for the current topic,
- and some memories should be retrievable only when relevant.

## 2. `ContextMemory` (core memory)

### Design goal

Keep high-value, stable facts in a compact form that can be injected frequently.

### What it looks like

A curated set of short memory items rather than a raw conversation dump.

### Key parameters

- `enabled`
- `max_chars`
- `max_items_total`
- `max_items_per_section`
- `max_item_chars`

### Recommended approach

Keep this layer small and specific. If it grows too large, it stops being "core."

## 3. Live topic context

This is the active conversation state for the current topic.

### Topic segmentation

Selena groups messages into topic-oriented units instead of treating the whole history as one flat stream.

### Context compression

Older or less important material can be summarized so the active window remains usable.

## 4. Topic archive

Older topic groups are archived rather than discarded. This allows later retrieval or review without keeping everything in the live prompt.

## 5. Long-term vector memory

### Write path

Useful facts, experiences, or retrieval-worthy content can be embedded and stored in Qdrant.

### Retrieval path

Relevant memories are recalled by vector search and optional rerank logic when the current turn warrants it.

### Metadata per memory item

Typical metadata includes:

- TTL
- temperature
- search score
- importance-related signals

### Temperature and decay

Important sub-mechanisms include:

1. `temperature`: a rough measure of freshness or activity
2. `TTL` upgrades and downgrades
3. duplicate suppression

### Retrieval flow

The runtime retrieves candidates, optionally reranks them, filters them, and only injects the best subset back into context.

## 6. Key parameter groups

The most important knobs usually live in:

- `ContextMemory`
- `VectorSetting`
- `MemoryRecall`
- `Summary`

## 7. Tuning suggestions

- If Selena forgets stable facts, review `ContextMemory`.
- If recall is noisy, review vector thresholds and rerank settings.
- If the prompt window gets bloated, reduce memory injection size or archive more aggressively.

## 8. Related documents

- [Architecture](./architecture.md)
- [Agent loop](./agent-loop.md)
- [Intent routing](./intent-routing.md)
- [Config reference](../CONFIG_REFERENCE.md)
