[简体中文](./zh-CN/security-policy.md)

# Security Policy

Selena's runtime is designed around the idea that tool access should be intentionally constrained, not assumed safe.

## 1. Secure by default

The safest production posture is to keep dangerous capabilities disabled until they are explicitly needed.

## 2. Five main lines of defense

The public runtime security story is built around:

1. toolset whitelists
2. admin mode controls
3. file root restrictions
4. approval mode
5. execution backend selection

## 3. Defense 1: toolset whitelist

Only explicitly enabled tool families should be reachable by the model.

## 4. Defense 2: admin privileges

`Security.is_admin` is a powerful switch and should not be enabled casually.

## 5. Defense 3: file roots

`Security.file_roots` limits where file tools are allowed to read and write.

### Path validation logic

File operations should resolve inside approved root paths rather than trusting raw user or model input.

## 6. Defense 4: approval mode

Approval mode lets Selena ask before running sensitive actions.

### Which tools usually need approval

High-risk local operations such as terminal access, file writes, or privileged integrations are common candidates.

### Possible outcomes

The user can approve, deny, or keep those actions unavailable depending on the surrounding runtime policy.

## 7. Defense 5: execution backend

The backend used to execute local actions matters. A more isolated backend is generally safer than unrestricted local execution.

## 8. Subagent security policy

Subagents should inherit stricter limits unless there is a specific reason to expand their access. `SubAgentPolicy` exists so delegated work does not quietly bypass the main policy story.

## 9. Data safety

### Credentials

Keep credentials in local config only and never in public issue reports or commits.

### Dialogue history and memory

History and memory stores may contain sensitive user data and should be treated as private runtime artifacts.

### Browser profiles

Persistent browser profiles can contain cookies, sessions, and personal state.

## 10. Prompt-injection resistance

Live browsing and external tool outputs can feed adversarial instructions back into the model. Restrictive tool policy, explicit approvals, and narrower task scopes all help reduce that risk.

## 11. Redaction

Logs, screenshots, and config fragments shared publicly should be scrubbed for:

- keys,
- tokens,
- local paths,
- private conversation data,
- and account-specific information.

## 12. Production checklist

- keep admin mode off unless necessary
- keep local terminal access off unless necessary
- restrict file roots tightly
- use approval mode for risky tools
- place public-facing services behind a reverse proxy
- back up runtime data deliberately

## 13. Related documents

- [Deployment](../DEPLOYMENT.md)
- [MCP integration](./mcp-integration.md)
- [Browser agent](./browser-agent.md)
- [Subagent delegation](./subagent-delegation.md)
