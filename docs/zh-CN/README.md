[English](../README.md)

<div align="center">

# Selena 中文文档中心

> 这里是 Selena 的中文镜像文档入口。初次进入仓库时，也可以先看根目录的 [README.zh-CN.md](../../README.zh-CN.md)。

</div>

---

## 上手

| 文档 | 说明 |
| --- | --- |
| [根目录快速开始](../../README.zh-CN.md#快速开始) | 最短启动路径 |
| [部署指南](../../DEPLOYMENT.zh-CN.md) | 环境隔离、Qdrant、运行和生产建议 |
| [配置参考](../../CONFIG_REFERENCE.zh-CN.md) | `config.json` 的主要字段说明 |
| [LLM 供应商](./llm-providers.md) | 模型别名、供应商接入和思考模式 |

## 架构与核心机制

| 文档 | 说明 |
| --- | --- |
| [整体架构](./architecture.md) | 模块边界、数据流、持久化和扩展点 |
| [Agent 主循环](./agent-loop.md) | 规划、预算、审批、压缩和子代理 |
| [意图路由](./intent-routing.md) | 何时进入 Agent 模式 |
| [分层记忆系统](./memory-system.md) | 关键记忆、话题上下文、长期向量记忆 |

## 子系统

| 文档 | 说明 |
| --- | --- |
| [技能系统](./skill-system.md) | 技能、工具、manifest 和技能演化 |
| [浏览器代理](./browser-agent.md) | 快照式浏览器操作和 CDP 工作流 |
| [子代理委派](./subagent-delegation.md) | 并行 fan-out、配额、结果处理 |
| [自主任务模式](./autonomous-mode.md) | 闲时规划、执行、分享分和冷却 |
| [MCP 协议集成](./mcp-integration.md) | 接入外部 MCP 工具服务器 |
| [Web 工作台](./frontend-workbench.md) | 前端面板、本地 API 和可观测性 |

## 运维与安全

| 文档 | 说明 |
| --- | --- |
| [安全策略](./security-policy.md) | 工具白名单、文件根目录、审批模式、执行后端 |

## 文档约定

- 英文文档位于仓库根目录和 `docs/` 下，是默认公开入口。
- 中文镜像文档位于根目录的 `*.zh-CN.md` 与 `docs/zh-CN/`。
- `DialogueSystem/docs/` 仍然视为内部开发文档，不纳入公开文档树。

如果你发现中文文档缺页或落后于英文版本，欢迎直接提 issue 或 PR。
