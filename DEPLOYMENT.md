[简体中文](./DEPLOYMENT.zh-CN.md)

# Deployment Guide

This document explains how to run Selena locally or on a server.

## 1. Requirements

| Component | Minimum | Notes |
| --- | --- | --- |
| Python | 3.9+ | Runtime for the main application |
| Docker | 20.10+ | Recommended for Qdrant, though local Qdrant is also possible |
| Node.js | 18+ | Only needed if you use the web frontend |
| pnpm / npm | recent | Frontend package manager |

## 2. Prepare the config file

### 2.1 Copy the template

```bash
cp config.example.json config.json
```

### 2.2 Fill in real API keys

Edit `config.json` and replace the placeholder `sk-YOUR_*_API_KEY` values with real credentials.

At least one LLM provider should be configured. In practice, `Embedding_Setting` and `Rerank_Setting` should also be reviewed before first launch.

For field-by-field explanations, see [CONFIG_REFERENCE.md](./CONFIG_REFERENCE.md).

## 3. Install Python dependencies

Using a dedicated Python environment is strongly recommended so the project does not inherit unrelated package conflicts from a shared workspace.

### Option A: `venv` (recommended)

```bash
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1
# Windows CMD
.venv\Scripts\activate.bat
# Linux/macOS
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

### Option B: `conda`

```bash
conda create -n selena python=3.11 -y
conda activate selena
pip install -r requirements.txt
```

### Why a separate environment helps

The project keeps `requirements.txt` intentionally small and close to the packages actually used at runtime. Reusing a large shared environment often creates three problems:

1. Version conflicts with packages pinned by other projects.
2. Slower startup and debugging due to unrelated imports.
3. Harder diagnosis when something breaks.

### Validate the install

```bash
python -c "import requests, qdrant_client, numpy, tiktoken, yaml; print('OK')"
```

If it prints `OK`, the core dependencies are in place.

### Optional dependencies

Some packages are left commented out in `requirements.txt` and only need to be installed if you use the related features.

| Package | When to enable it |
| --- | --- |
| `sentence-transformers` | Local embeddings instead of a cloud embedding service |
| `python-docx` | Word export through the document-generation skill |
| `python-pptx` | PowerPoint export through the document-generation skill |

## 4. Start Qdrant

Qdrant is a required dependency for intent routing, long-term memory, and vector-based retrieval.

### Option A: Docker Compose

```bash
docker compose up -d
```

This starts the `selena-qdrant` container and persists data to `./qdrant_data_docker/`.

### Option B: PowerShell helper

```powershell
.\deploy_qdrant_docker.ps1
```

This script reads `Qdrant_Setting` from `config.json` and starts or reuses the container.

### Option C: run Qdrant directly

```bash
docker run -d --name selena-qdrant \
  -p 6333:6333 -p 6334:6334 \
  -v $(pwd)/qdrant_data_docker:/qdrant/storage \
  qdrant/qdrant:latest
```

### Health check

```bash
curl http://127.0.0.1:6333/healthz
```

`healthz check passed` means the service is ready.

### Migrate older local data

If you already have data in `./qdrant_data/`, you can migrate it:

```bash
python migrate_qdrant_to_docker.py
```

Supported environment variable overrides:

- `QDRANT_SOURCE_PATH`
- `QDRANT_TARGET_HOST`
- `QDRANT_TARGET_PORT`

## 5. Start the main runtime

```bash
python -m DialogueSystem.main
```

or

```bash
python DialogueSystem/main.py
```

On first start the runtime creates directories such as `DialogueSystem/data/`, `DialogueSystem/history/`, and `DialogueSystem/logs/`, then initializes the required SQLite databases.

## 6. Start the frontend (optional)

If `Frontend.auto_start = true` in `config.json`, the backend can try to launch the frontend dev server automatically.

To run it manually:

```bash
cd DialogueSystem/frontend
pnpm install
pnpm dev
# or
pnpm build
pnpm preview
```

Default addresses:

- Frontend: <http://127.0.0.1:5173>
- Local API: <http://127.0.0.1:8000>

## 7. Common issues

### Q1: Qdrant port already in use

Change `Qdrant_Setting.port` and `grpc_port` in `config.json`, then keep `docker-compose.yml` in sync.

### Q2: `sentence-transformers` model download failed

If you use local embeddings, `MemorySystem/Embedding.py` defaults to `BAAI/bge-small-zh-v1.5` with `local_files_only=True`.

You can pre-download it manually:

```python
from sentence_transformers import SentenceTransformer
SentenceTransformer("BAAI/bge-small-zh-v1.5")
```

Or change `local_files_only` to `False` for first-run download behavior.

### Q3: Browser tools do not start

The browser runtime expects a locally installed browser and the matching driver/runtime support. Check `DialogueSystem/browser/` and make sure the `browser` toolset is enabled in `Security.enabled_toolsets`.

### Q4: How do I configure MCP servers?

Add them under `MCP.servers` in `config.json`, for example:

```json
{
  "name": "my-mcp",
  "enabled": true,
  "url": "http://127.0.0.1:9000/mcp",
  "auth_token": "optional-bearer-token"
}
```

### Q5: How can I reduce resource usage?

Disable features through `config.json`, for example:

- `AutonomousTaskMode.enabled = false`
- `IntentRouter.enabled = false`
- `SkillEvolution.enabled = false`
- `Frontend.enabled = false`
- `MCP.enabled = false`

## 8. Production suggestions

- Do not run with `Security.is_admin = true` and `Security.allow_local_terminal = true` unless you fully understand the risk.
- Keep `Security.file_roots` as small as possible.
- Put the frontend and API behind a reverse proxy such as Nginx or Caddy instead of exposing them directly.
- Back up these paths regularly:
  - `config.json`
  - `DialogueSystem/data/`
  - `DialogueSystem/history/`
  - `qdrant_data_docker/`
- Log files under `DialogueSystem/logs/` are date-split automatically, but long-running deployments should still set up rotation or cleanup.
