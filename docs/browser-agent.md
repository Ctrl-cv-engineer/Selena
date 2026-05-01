[简体中文](./zh-CN/browser-agent.md)

# Browser Agent

Selena includes a browser runtime for tasks that genuinely require live UI interaction.

## 1. Design philosophy

The browser agent is meant for situations where:

- page state matters,
- clicks and navigation are unavoidable,
- screenshots or snapshots are useful,
- and normal HTTP retrieval is not enough.

## 2. Supported browsers

The runtime is designed around Chromium-family and other supported local browsers through CDP-style control paths and compatible wrappers.

## 3. Snapshot-based operating model

Instead of reasoning over raw pixels alone, Selena can work from structured browser snapshots.

### What a snapshot gives the model

A snapshot can expose:

- page structure,
- candidate elements,
- text content,
- references for later clicks or typing.

This is usually more stable than asking the model to infer everything from screenshots alone.

## 4. `chrome-browser-agent` toolset

### Navigation

Open pages, change URLs, move between tabs, and reload state.

### Observation

Capture snapshots, inspect text, and gather structured page state.

### Interaction

Click, type, press keys, and submit forms.

### Async waiting

Wait for page changes, loading states, or async content.

### Multi-tab work

Open, switch, and coordinate across tabs when the workflow requires it.

## 5. `browser-enhancements`

These helpers make browser tasks less brittle by adding convenience behaviors around common navigation and page-state flows.

## 6. Example workflow

A typical search flow looks like:

1. Navigate to a site.
2. Snapshot the page.
3. Type into the search field.
4. Submit.
5. Snapshot results.
6. Click the best candidate.

## 7. Browser profile persistence

Persistent browser profiles can preserve sessions and local state, but they also raise privacy and security considerations.

## 8. Security considerations

- Browser access should be gated by toolset policy.
- Persistent profiles may contain cookies and private state.
- Live browsing increases prompt-injection exposure from untrusted pages.

## 9. Performance and stability notes

- Prefer targeted navigation over broad browsing.
- Snapshot-driven flows are often more reliable than free-form visual clicking.
- Long multi-tab sessions should be bounded by task budgets and approvals.

## 10. When not to use the browser agent

Avoid it when:

- a normal web search or API call is enough,
- the task is purely text retrieval,
- or policy does not justify interactive browsing.

## 11. Related documents

- [Skill system](./skill-system.md)
- [Security policy](./security-policy.md)
- [MCP integration](./mcp-integration.md)
