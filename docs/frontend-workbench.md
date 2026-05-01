[简体中文](./zh-CN/frontend-workbench.md)

# Web Workbench

Selena ships with a local React-based frontend for chat, inspection, debugging, and config editing.

## 1. How to start it

You can either:

- let the backend auto-start it if `Frontend.auto_start = true`, or
- run it manually from `DialogueSystem/frontend`.

## 2. The nine-panel overview

The current workbench surfaces these main areas:

- Chat
- Workbench
- Debug
- DataVisualization
- IntentionSelection
- Schedule
- ConfigEditor
- LLMInspector
- ATMInspector

## 3. Panel details

### Chat

Primary conversation surface.

### Workbench

General task and runtime interaction area.

### Debug

Internal state inspection and debugging support.

### DataVisualization

Vector-store or memory-related visualization.

### IntentionSelection

Intent example and routing-related management.

### Schedule

Reminder and schedule-oriented user services.

### ConfigEditor

Direct editing surface for `config.json`.

### LLMInspector

Tracing and reviewing model calls.

### ATMInspector

Inspecting artifacts produced by autonomous task mode.

## 4. Frontend stack

The frontend currently uses React, TypeScript, Vite, and related tooling from `DialogueSystem/frontend`.

## 5. Local API port

The frontend talks to the local Selena API, typically on `127.0.0.1:8000`.

## 6. Customizing the frontend

You can change panels, layout, styles, and observability surfaces like any normal React application, as long as the backend expectations stay aligned.

## 7. Do I have to use the frontend?

No. The backend runtime can still operate without the frontend if you disable it.

## 8. Related documents

- [Deployment](../DEPLOYMENT.md)
- [Architecture](./architecture.md)
- [Security policy](./security-policy.md)
