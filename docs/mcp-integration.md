[简体中文](./zh-CN/mcp-integration.md)

# MCP Integration

Selena can connect to external tool servers through Model Context Protocol (MCP).

## 1. What MCP is

MCP is a protocol for exposing tools and context to language-model-driven systems in a structured, discoverable way.

## 2. Selena's MCP implementation

Selena treats MCP as an external capability layer. When enabled, configured servers are discovered and their tools become available to the runtime.

## 3. Configuring MCP servers

Add server entries under `MCP.servers` in `config.json` with fields such as:

- `name`
- `enabled`
- `url`
- `auth_token`

## 4. Tool discovery and refresh

On startup, Selena can load and refresh available MCP tools from the configured servers. If a server is unavailable, its tools will not appear until it becomes reachable again and the runtime refreshes.

## 5. Example: connecting an external service

Typical flow:

1. Start or deploy the MCP server.
2. Add it to Selena's config.
3. Restart Selena.
4. Let the runtime discover the new tools.
5. Use those tools in chat or agent workflows.

## 6. Security considerations

- Treat external MCP servers as trusted integrations only when you understand what they expose.
- Use auth tokens where appropriate.
- Keep approval and toolset policies aligned with the kinds of actions those tools can perform.

## 7. Common MCP use cases

Typical servers expose:

- project management systems,
- knowledge bases,
- code or file tooling,
- search or browsing integrations,
- task-specific enterprise tools.

## 8. MCP vs skills

Use MCP when the capability already exists as an external tool server.

Use a Selena skill when:

- you want bundled in-repo behavior,
- you need custom prompting around tool usage,
- or the capability is more naturally packaged as a local skill.

## 9. Troubleshooting

### Tools do not appear on startup

Check server reachability, `enabled` flags, auth, and runtime logs.

### Tool calls fail

Check the remote server itself, the auth token, and any schema mismatch between the tool and its expected input.

### Want to disable MCP completely

Set `MCP.enabled = false`.

## 10. Related documents

- [Skill system](./skill-system.md)
- [Security policy](./security-policy.md)
- [Config reference](../CONFIG_REFERENCE.md)
