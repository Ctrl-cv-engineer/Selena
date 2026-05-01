# 🎨 Web 工作台

> Selena 内置一个 React + TypeScript 的 Web 工作台，**对话只是其中一个面板**。它还能让你实时观察记忆、调试意图、可视化向量库、追踪 LLM 调用、检视自主任务产出。

---

## 1. 启动方式

```json
{
  "Frontend": {
    "enabled": true,
    "auto_start": true,
    "host": "127.0.0.1",
    "port": 5173,
    "api_port": 8000,
    "package_manager": "pnpm"
  }
}
```

`auto_start = true` 时，Selena 主程序启动会自动拉起前端 dev server。

| 服务 | 默认地址 |
|------|---------|
| 前端页面 | http://127.0.0.1:5173 |
| 本地 API | http://127.0.0.1:8000 |

也可以手动启动：

```bash
cd DialogueSystem/frontend
pnpm install   # 首次
pnpm dev       # 开发模式
pnpm build     # 生产构建
```

---

## 2. 九个面板一览

| 路由 | 名称 | 用途 |
|------|------|------|
| `/` | 💬 Chat | 实时对话主界面 |
| `/workbench` | 🛠️ Workbench | 工作台 |
| `/debug` | 🔬 Debug | 运行时调试 |
| `/data/:collection` | 📊 DataVisualization | 向量库可视化 |
| `/IntentionSelection` | 🎯 IntentionSelection | 意图库管理 |
| `/schedule` | 📅 Schedule | 日程管理 |
| `/config` | ⚙️ ConfigEditor | 配置编辑 |
| `/llm-inspector` | 📡 LLMInspector | LLM 调用追踪 |
| `/atm-inspector` | 🌙 ATMInspector | 自主任务产物检视 |

---

## 3. 面板详解

### 💬 Chat — 主对话界面

最常驻的页面。提供：

- 消息流（含工具调用展示）
- 工具审批弹窗（`approval_mode = manual` 时）
- 话题切换提示
- ContextMemory 实时预览
- 流式输出 + 思考链折叠展示

工具调用以可视化卡片展示：

```
┌─────────────────────────────────┐
│ 🔧 webSearch                    │
│ 正在搜索：Mamba 架构对比        │
│ ────────────────────────────── │
│ ✅ 找到 8 条结果（已压缩 1200 字）│
└─────────────────────────────────┘
```

---

### 🛠️ Workbench — 工作台

集成式控制台，给"高级用户"用。常见用途：

- 手动触发自主任务规划
- 强制刷新 ContextMemory
- 一键重启后台 worker
- 导出 / 导入对话历史

---

### 🔬 Debug — 调试

开发与运行时排错。能看到：

- 当前 live context 的 token 数与压缩状态
- Agent 主循环的工具调用历史
- 意图路由的判定细节（命中分数 / 复核结果）
- 后台 worker 的状态（是否阻塞、任务队列）

---

### 📊 DataVisualization — 向量库可视化

直接对接 Qdrant，可以浏览每个 collection 的内容：

| Collection | 用途 |
|-----------|------|
| `IntentionSelection_512` | 意图库 |
| `rag_memory_512` | RAG 记忆 |
| `long_term_memory_512` | 长期原子记忆 |
| `web_embedding_1024` | 网页内容向量 |

支持：

- 列出 / 搜索 / 删除某条记忆
- 查看每条记忆的元数据（temperature、TTL、SearchScore）
- 话题归档可视化（`TopicArchiveView`）

---

### 🎯 IntentionSelection — 意图库管理

意图路由的核心数据是意图库。这个面板让你：

- 浏览所有意图示例
- 添加新示例（手写或 LLM 生成）
- 删除老旧 / 错误示例
- 实时测试某句话的路由结果

调试意图路由准确率时的主战场。

---

### 📅 Schedule — 日程管理

`schedule-manager` skill 的可视化 UI：

- 列表 / 日历视图查看任务
- 手动新建 / 编辑 / 删除
- 提醒到点的弹窗通知

---

### ⚙️ ConfigEditor — 配置编辑

直接读写 `config.json`。

- 表单化展示所有配置项
- 字段说明（关联 [`CONFIG_REFERENCE.md`](../CONFIG_REFERENCE.md)）
- 修改后立即落盘
- Frontend 相关字段提示需要重启生效

> ⚠️ 修改 LLM Key / Qdrant 等关键字段后**必须重启**主程序。

---

### 📡 LLMInspector — LLM 调用追踪

显示每一次模型调用的完整追踪：

| 字段 | 含义 |
|------|------|
| Endpoint | 请求的 LLM 供应商 + 模型 |
| Task | 哪个任务（Agent / Simple / topic_same / ...） |
| Input tokens / Output tokens | 真实消耗 |
| Latency | 延迟 |
| Status | 成功 / 失败 / 超时 |
| Prompt | 完整 prompt（可展开）|
| Response | 完整响应 |

**调试 prompt、找性能瓶颈、对比模型时的核心工具**。

---

### 🌙 ATMInspector — 自主任务产物检视

[自主任务模式](./autonomous-mode.md) 写的笔记 / 随笔 / 任务总结，全部在这里：

- 时间线视图
- 按类型筛选（writing / research / note / ...）
- 阅读单条产物的完整内容
- 看分享分与是否已被提及

```
┌─ 2026-04-30 ──────────────────────┐
│ 🌙 关于「安静」的随笔              │
│ score: 0.82  · already mentioned   │
│ ──────────────────────────────── │
│ 我喜欢清晨四点的安静。那不是...   │
└────────────────────────────────────┘
```

---

## 4. 前端技术栈

| 类别 | 技术 |
|------|------|
| 框架 | React 18 + TypeScript |
| 构建 | Vite 6 |
| 路由 | React Router 7 |
| 组件库 | Ant Design 6 |
| 状态 | Zustand |
| 编辑器 | Monaco Editor |
| 图表 | Chart.js + react-chartjs-2 |
| 图标 | Lucide |
| 样式 | Tailwind CSS |

---

## 5. 本地 API 端口

前端通过 `http://127.0.0.1:8000` 与后端通讯。API 由 `runtime/frontend_runtime.py` 提供，主要分组：

| 分组 | 路径 | 用途 |
|------|------|------|
| Chat | `/api/chat/*` | 发送消息、获取流式回复 |
| Memory | `/api/memory/*` | 浏览 ContextMemory / 长期记忆 |
| Qdrant | `/api/vector/*` | 向量库代理 |
| Schedule | `/api/schedule/*` | 日程 CRUD |
| Config | `/api/config/*` | 配置读写 |
| LLM | `/api/llm/*` | LLM 调用追踪 |
| ATM | `/api/atm/*` | 自主任务查询 |
| Skill | `/api/skill/*` | 技能管理 |

---

## 6. 自定义前端

如果你想改前端样式 / 加新页面：

1. 在 `frontend/src/pages/<NewPage>/` 创建组件。
2. 在 `frontend/src/App.tsx` 加路由。
3. 在 `frontend/src/components/Layout/` 加导航入口（如需）。
4. 后端 API 加在 `runtime/frontend_runtime.py`。

前端是**完全独立的子项目**，可以单独开发与部署。

---

## 7. 不想用前端？

```json
{ "Frontend": { "enabled": false } }
```

主程序会以纯 CLI 模式运行 —— 终端直接对话。所有功能仍可用，只是没有可视化。

---

## 8. 相关文档

- [整体架构](./architecture.md) — 前端在三层架构中的位置
- [配置参考](../CONFIG_REFERENCE.md#frontend)
- [部署指南](../DEPLOYMENT.md) — 生产环境前端部署建议
