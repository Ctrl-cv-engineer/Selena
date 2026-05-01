# 部署指南

本文档说明如何在本地或服务器上部署 Selena DialogueSystem。

---

## 1. 环境要求

| 组件 | 最低版本 | 说明 |
|------|---------|------|
| Python | 3.9+ | 主程序运行环境 |
| Docker | 20.10+ | 用于运行 Qdrant 向量数据库（可选，也可本地运行 Qdrant） |
| Node.js | 18+ | 前端构建（可选，仅在使用 Web 前端时需要） |
| pnpm / npm | 最新 | 前端包管理器 |

---

## 2. 配置文件准备

### 2.1 复制配置模板

```bash
cp config.example.json config.json
```

### 2.2 填入 API Key

编辑 `config.json`，将所有 `sk-YOUR_*_API_KEY` 占位符替换为实际的 API Key。至少需要配置一个 LLM 供应商（推荐 `qwen` 或 `deepseek`）以及 `Embedding_Setting`、`Rerank_Setting`。

详细字段说明参见 [`CONFIG_REFERENCE.md`](./CONFIG_REFERENCE.md)。

---

## 3. 安装 Python 依赖

**强烈建议为本项目创建独立的 Python 环境**，避免与系统/全局环境的其他包冲突。下面提供两种隔离方式，任选其一。

### 方式 A：venv（标准库，推荐）

```bash
# 在项目根目录创建独立虚拟环境
python -m venv .venv

# 激活虚拟环境
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Windows (CMD)
.venv\Scripts\activate.bat
# Linux/macOS
source .venv/bin/activate

# 安装核心依赖
pip install --upgrade pip
pip install -r requirements.txt
```

### 方式 B：conda（适合已经使用 Anaconda 的用户）

```bash
# 创建专用环境（不要复用通用的 workspace 环境）
conda create -n selena python=3.11 -y
conda activate selena

# 安装核心依赖
pip install -r requirements.txt
```

### 3.1 关于"为什么要新建环境"

`requirements.txt` 经过精确扫描，仅声明了项目实际使用的第三方库。常见的混合环境（例如多项目共用的 Anaconda 环境）通常包含数百个无关包，会带来三个问题：
1. **版本冲突**：项目依赖的 `qdrant-client` / `tiktoken` 版本可能被其他项目锁定到不兼容的版本。
2. **启动开销**：包过多会拖慢解释器导入和环境初始化。
3. **难以调试**：报错时难以判断问题来自项目本身还是无关包的副作用。

如果当前在用如 `D:\ANACONDA_data\envs\workspace\python.exe` 这样的通用环境，建议为本项目新建一个干净环境再运行。

### 3.2 验证安装

```bash
python -c "import requests, qdrant_client, numpy, tiktoken, yaml; print('OK')"
```

打印 `OK` 即表示核心依赖已就绪。

### 3.3 可选依赖

`requirements.txt` 中的可选依赖被注释掉，按需启用：

| 依赖 | 启用条件 |
|------|---------|
| `sentence-transformers` | 使用本地 Embedding（不依赖云端 Embedding 服务），同时需要下载 `BAAI/bge-small-zh-v1.5` 等模型 |
| `python-docx` | 使用 `document-generation` skill 生成 Word 文档 |
| `python-pptx` | 使用 `document-generation` skill 生成 PowerPoint |

---

## 4. 部署 Qdrant 向量数据库

Qdrant 是必需组件，用于意图识别、长期记忆、RAG 检索等功能。

### 方式 A：Docker Compose（推荐）

```bash
docker compose up -d
```

这会启动 `selena-qdrant` 容器，监听 6333（HTTP）和 6334（gRPC）端口，数据持久化到 `./qdrant_data_docker/`。

### 方式 B：PowerShell 脚本（Windows）

```powershell
.\deploy_qdrant_docker.ps1
```

脚本会读取 `config.json` 中的 `Qdrant_Setting`，自动启动或复用容器。

### 方式 C：本地直接运行 Qdrant

```bash
docker run -d --name selena-qdrant \
  -p 6333:6333 -p 6334:6334 \
  -v $(pwd)/qdrant_data_docker:/qdrant/storage \
  qdrant/qdrant:latest
```

### 验证 Qdrant 启动

```bash
curl http://127.0.0.1:6333/healthz
```

返回 `healthz check passed` 即正常。

### 从本地数据迁移到 Docker（可选）

如果之前有 `./qdrant_data/` 本地数据需要迁移：

```bash
python migrate_qdrant_to_docker.py
```

支持环境变量覆盖：
- `QDRANT_SOURCE_PATH`：源数据路径
- `QDRANT_TARGET_HOST`：目标 Qdrant 主机
- `QDRANT_TARGET_PORT`：目标 Qdrant 端口

---

## 5. 启动主程序

```bash
python -m DialogueSystem.main
```

或：

```bash
python DialogueSystem/main.py
```

首次启动会自动创建 `DialogueSystem/data/`、`DialogueSystem/history/`、`DialogueSystem/logs/` 等运行时目录，并初始化各 SQLite 数据库。

---

## 6. 启动前端（可选）

如果在 `config.json` 中设置了 `Frontend.auto_start = true`，主程序会自动拉起前端开发服务器。

手动启动：

```bash
cd DialogueSystem/frontend
pnpm install        # 首次需要
pnpm dev            # 开发模式
# 或
pnpm build          # 构建生产版本
pnpm preview        # 预览生产版本
```

默认地址：
- 前端页面：http://127.0.0.1:5173
- 本地 API：http://127.0.0.1:8000

---

## 7. 常见问题

### Q1：Qdrant 端口被占用？

修改 `config.json` 的 `Qdrant_Setting.port` 和 `grpc_port`，并同步修改 `docker-compose.yml` 中的端口映射。

### Q2：sentence-transformers 模型下载失败？

`MemorySystem/Embedding.py`（如使用本地 Embedding）默认使用 `BAAI/bge-small-zh-v1.5`，并设置了 `local_files_only=True`。需提前手动下载模型到本地缓存：

```python
from sentence_transformers import SentenceTransformer
SentenceTransformer("BAAI/bge-small-zh-v1.5")
```

或将 `local_files_only=False`，让首次运行时自动下载。

### Q3：浏览器工具无法启动？

`DialogueSystem/browser/` 模块依赖本地浏览器（Chrome / Edge / Firefox）和对应驱动。确保系统已安装浏览器，并在 `config.json` 中开启 `Security.enabled_toolsets` 中的 `browser`。

### Q4：MCP 服务器集成？

在 `config.json` 的 `MCP.servers` 中配置外部 MCP 服务器：

```json
{
  "name": "my-mcp",
  "enabled": true,
  "url": "http://127.0.0.1:9000/mcp",
  "auth_token": "optional-bearer-token"
}
```

### Q5：如何关闭某些功能降低资源占用？

通过 `config.json` 中的开关字段：
- `AutonomousTaskMode.enabled = false` — 关闭自主任务
- `IntentRouter.enabled = false` — 关闭意图路由
- `SkillEvolution.enabled = false` — 关闭技能演化
- `Frontend.enabled = false` — 关闭前端
- `MCP.enabled = false` — 关闭 MCP 集成

---

## 8. 生产环境建议

- **不要使用 `Security.is_admin = true` 和 `Security.allow_local_terminal = true`**：这会允许 LLM 直接执行本地终端命令，存在严重安全风险。
- **限制 `Security.file_roots`**：仅放开必要的目录，避免 LLM 访问敏感文件。
- **使用反向代理**：前端和 API 端口不要直接暴露到公网，推荐使用 Nginx / Caddy 做反向代理 + HTTPS。
- **定期备份**：
  - `config.json`
  - `DialogueSystem/data/` 中的 SQLite 数据库
  - `DialogueSystem/history/` 中的对话历史
  - `qdrant_data_docker/` 中的向量数据
- **日志轮转**：`DialogueSystem/logs/` 下的日志文件会自动按日期切分，建议配置 logrotate 或定期清理。
