# 🔌 MCP 协议集成

> **Model Context Protocol (MCP)** 是一套让 LLM 可以"插拔式"调用外部工具的开放协议。Selena 实现了 MCP 客户端，可以**动态接入任何符合 MCP 规范的服务器**，零代码扩展能力边界。

---

## 1. 什么是 MCP？

简单理解：**给 LLM 工具的统一插槽**。

| 概念 | 类比 |
|------|------|
| MCP Server | USB 设备 |
| MCP Client (Selena) | USB 接口 |
| 工具 | 设备能做的事 |

只要某个工具服务实现了 MCP 协议（JSON-RPC over HTTP / stdio），Selena 就能自动发现并调用它的工具，**不需要为它写一行 Python**。

官方文档：https://modelcontextprotocol.io/

---

## 2. Selena 的 MCP 实现

| 模块 | 路径 | 职责 |
|------|------|------|
| MCP 运行时 | `runtime/mcp_runtime.py` | 维护 MCP 客户端、工具发现、调用桥接 |
| 工具 | `tools/listMcpTools.json`, `refreshMcpTools.json` | Agent 用来列出 / 刷新 MCP 工具 |

启动时如果 `MCP.enabled = true`，Selena 会：

1. 读取 `MCP.servers` 列表。
2. 对每个 enabled 的服务器发起 `tools/list` JSON-RPC 调用。
3. 把返回的工具注册到 Agent 的工具池中（带 `mcp:<server>:<tool>` 命名空间）。
4. 后续模型调用这些工具时，Selena 帮忙转发到对应 MCP 服务器。

---

## 3. 配置 MCP 服务器

```json
{
  "MCP": {
    "enabled": true,
    "servers": [
      {
        "name": "linear",
        "enabled": true,
        "url": "http://127.0.0.1:9000/mcp",
        "auth_token": "lin_oauth_xxxx"
      },
      {
        "name": "github",
        "enabled": true,
        "url": "http://127.0.0.1:9001/mcp",
        "auth_token": ""
      }
    ]
  }
}
```

| 字段 | 含义 |
|------|------|
| `name` | 服务器在 Selena 内的逻辑名称（用于工具命名空间） |
| `enabled` | 是否启用 |
| `url` | MCP JSON-RPC HTTP 端点 |
| `auth_token` | 可选 Bearer Token；空时不发 `Authorization` 头 |

---

## 4. 工具发现与刷新

启动时自动发现一次，运行中也可以让 Agent 主动刷新：

```
[模型] 我需要看一下当前可用的 MCP 工具
  → listMcpTools()
  → 返回所有已发现的 MCP 工具及其描述

[模型] MCP 服务器更新了新工具，刷新一下
  → refreshMcpTools()
  → 重新对每个服务器发起 tools/list
```

---

## 5. 一个完整例子：接入 Linear

假设你有一个 Linear MCP 服务器（提供 issue / project 查询）跑在本地 9000 端口。

### 1) 启动它（示意）
```bash
linear-mcp-server --port 9000 --auth-token $LINEAR_TOKEN
```

### 2) 配置 Selena
```json
{
  "MCP": {
    "enabled": true,
    "servers": [
      {
        "name": "linear",
        "enabled": true,
        "url": "http://127.0.0.1:9000/mcp",
        "auth_token": "lin_oauth_xxxx"
      }
    ]
  }
}
```

### 3) 重启 Selena，自动发现工具

启动日志：
```
[MCP] Connected to linear at http://127.0.0.1:9000/mcp
[MCP] Discovered 6 tools: searchIssues, getIssue, createIssue, ...
[MCP] Registered as: mcp:linear:searchIssues, mcp:linear:getIssue, ...
```

### 4) 在对话中使用

```
你：帮我查一下 INGEST 项目里 In Progress 的 issues。

[Agent 决策]
  → mcp:linear:searchIssues({"project": "INGEST", "state": "In Progress"})

[结果]
  - INGEST-123: Fix pipeline backpressure
  - INGEST-145: Refactor queue consumer
```

---

## 6. 安全考量

MCP 工具与本地工具走相同的策略层：

| 检查 | 行为 |
|------|------|
| `Security.enabled_toolsets` 含 `mcp` | 否则所有 MCP 工具拒绝 |
| `approval_mode = manual` | 高敏感 MCP 工具调用前需用户审批 |
| `auth_token` 加密存储 | 仅在请求头中使用 |

> ⚠️ **不要把高权限 MCP 服务器暴露到公网**。Selena 默认用 `127.0.0.1`，你的 MCP 服务器也应该绑在本地或内网。

---

## 7. 常见 MCP 服务器

社区有大量现成的 MCP 服务器可以直接用：

| 类别 | 例子 |
|------|------|
| 开发工具 | GitHub, GitLab, Linear, Jira, Slack |
| 数据库 | PostgreSQL, SQLite, Redis |
| 文件系统 | Filesystem, Google Drive, Dropbox |
| 浏览器 | Puppeteer, Playwright（与 Selena 内置 chrome-browser-agent 互补） |
| 搜索 | Brave Search, Tavily |

完整列表：https://github.com/modelcontextprotocol/servers

---

## 8. MCP vs Skill：什么时候用哪个？

| 场景 | 用 MCP | 用 Skill |
|------|--------|---------|
| 已经存在 MCP 服务器 | ✅ | — |
| 需要本地数据/状态 | — | ✅ |
| 跨语言（非 Python） | ✅ | — |
| 需要深度集成记忆系统 | — | ✅ |
| 临时实验 | ✅（不需要写代码） | — |
| 长期生产能力 | 视情况 | ✅（性能更好） |

简言之：**优先 MCP（生态大、零开发），核心能力做成 Skill（性能好、深度集成）**。

---

## 9. 排错

### 启动时 MCP 工具没出现
- 检查 MCP 服务器是否正常响应 `POST /mcp` with `{"method":"tools/list"}`。
- 检查 `auth_token` 是否正确。
- 看 `DialogueSystem/logs/dialogue_system.log` 里 `[MCP]` 标签的日志。

### 工具调用报错
- 让 Agent 调用 `refreshMcpTools()` 重新拉取最新 schema。
- 检查参数是否匹配 MCP 服务器的工具签名。

### 想完全关闭
```json
{ "MCP": { "enabled": false } }
```

---

## 10. 相关文档

- [技能系统](./skill-system.md) — Skill 与 MCP 的对比
- [Agent 主循环](./agent-loop.md) — MCP 工具如何参与规划
- [安全策略](./security-policy.md) — `mcp` toolset 的权限边界
