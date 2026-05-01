<div align="center">

# 📚 Selena 文档中心

> 完整的项目文档与技术细节。如果你刚到这里，先看根目录的 [**README**](../README.md)。

</div>

---

## 🎓 上手指南

| 文档 | 说明 |
|------|------|
| [**60 秒快速启动**](../README.md#-60-秒快速启动) | 最短启动路径 |
| [**完整部署指南**](../DEPLOYMENT.md) | 含环境隔离、Docker、生产建议 |
| [**配置项参考**](../CONFIG_REFERENCE.md) | `config.json` 每个字段的含义 |
| [**LLM 供应商接入**](./llm-providers.md) | 接入哪一家、模型怎么挑 |

---

## 🏛️ 架构与核心机制

| 文档 | 说明 |
|------|------|
| [**整体架构**](./architecture.md) | 模块边界、数据流、扩展点 |
| [**Agent 主循环**](./agent-loop.md) | 工具规划、token 预算、连续调用控制 |
| [**意图路由**](./intent-routing.md) | 向量召回 + LLM 复核的混合判定 |

---

## 🧩 子系统

| 文档 | 说明 |
|------|------|
| [**分层记忆系统**](./memory-system.md) | 关键记忆 / 话题档案 / 向量记忆 + TTL/温度/SearchScore |
| [**技能系统**](./skill-system.md) | 9 个内置技能 + 技能演化 |
| [**浏览器代理**](./browser-agent.md) | Chrome / Edge / Firefox via CDP |
| [**子代理委派**](./subagent-delegation.md) | 6 种类型 + 并行 fan-out + wait-all |
| [**自主任务模式**](./autonomous-mode.md) | 闲时规划 / 执行 / 分享分 / 冷却 |
| [**MCP 协议集成**](./mcp-integration.md) | 动态接入外部工具服务器 |
| [**Web 工作台**](./frontend-workbench.md) | 9 个面板介绍 |

---

## 🛡️ 进阶与运维

| 文档 | 说明 |
|------|------|
| [**安全策略**](./security-policy.md) | 工具集白名单、文件 root、审批模式 |

---

## 🗂️ 文档协作

- 所有公开文档放在仓库根目录 `docs/` 下；`DialogueSystem/docs/` 是内部开发文档（gitignore）。
- 写新文档时请保持一致风格：开头一句话定位、核心概念表格化、关键流程用 mermaid。
- 跨文档引用使用相对路径，方便在 GitHub / 本地 IDE 都能正确跳转。

如果你觉得某个机制还差一篇文档，欢迎[**提 Issue**](https://github.com/your-org/selena/issues) 或直接 PR。
