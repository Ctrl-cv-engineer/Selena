[简体中文](./zh-CN/intent-routing.md)

# Intent Routing

Intent routing is the layer that decides whether Selena should stay in lightweight dialogue mode or enter the full agent workflow.

## 1. Why intent routing exists

Without a routing layer, every message risks becoming an expensive agent task. Intent routing helps reserve tool-heavy reasoning for requests that actually need it.

## 2. Two working modes

### Mode A: `vector`

Uses vector matching against an intent library, plus optional LLM review for gray-area cases.

### Mode B: `llm`

Uses a pure model-based decision path. This is simpler conceptually but usually more expensive.

## 3. Important parameters for vector mode

The main controls are:

- `high_confidence_threshold`
- `low_confidence_threshold`
- `candidate_limit`
- `llm_fallback`

### Threshold logic

High-confidence matches can route directly.
Low-confidence matches can be rejected.
The middle band can trigger a model-based review.

## 4. Where the intent library comes from

Two common sources:

1. `intent_examples` bundled with a skill manifest
2. Automatically generated examples from the intent example prompt assets

## 5. Gray-zone review prompt

When vector confidence is not decisive, a smaller LLM review can decide whether the user request really needs the agent path.

## 6. Example

In vector mode:

- the query is embedded,
- candidates are retrieved,
- thresholds are applied,
- and only ambiguous cases escalate.

In pure LLM mode:

- the request goes directly to a classifier-style model decision.

## 7. Tuning suggestions

- Raise thresholds if too many casual messages trigger agent mode.
- Lower them if legitimate tool requests are being missed.
- Keep the candidate limit large enough for coverage but small enough for speed.

## 8. Boundaries with other systems

Intent routing is not the same as:

- memory retrieval,
- tool approval,
- or full agent planning.

It is the gate before those systems.

## 9. Related documents

- [Agent loop](./agent-loop.md)
- [Skill system](./skill-system.md)
- [Memory system](./memory-system.md)
