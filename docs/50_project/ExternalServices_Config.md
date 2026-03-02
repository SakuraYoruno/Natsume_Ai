受众：MASTER
标题：外置服务清单与配置手册（基于 external_services.json）

版本：v1.1
日期：2026-03-02

---

## 1. 清单文件

- 机器可读清单：`configs/runtime/external_services.json`
- 目标：统一维护本项目运行所需的服务依赖、模型名、路径、端点、环境变量与健康检查方式。
- 约束：清单文件不包含任何密钥明文。

---

## 2. 服务总览

| service_id | 类型 | 必需 | 主要配置入口 |
|---|---|---|---|
| `host.api` | 运行时服务（Host） | 是 | `.env` (`HOST_APP_HOST/PORT`), `scripts/runtime/start_host_side.ps1` |
| `edge.rpc.gateway` | 运行时服务（Edge） | 是 | `.env` (`EDGE_RPC_HOST/PORT`), `scripts/runtime/start_edge_side.ps1` |
| `neo4j` | 图数据库 | 是 | `.env` (`BOND_NEO4J_*`), `configs/runtime/local_services.env` |
| `chroma` | 向量数据库 | 是 | `.env` (`CHROMA_*`), `configs/runtime/local_services.env` |
| `llm.providers` | 外部 LLM API | 是 | `.env` (`HOST_LLM_PROVIDER`, `*_API_KEY(_REF)`) |
| `retrieval.embedding` | Embedding 路由 | 是 | `.env` (`RETR_EMBED_*`), `configs/models/embedding_routes.example.json` |
| `retrieval.reranker` | Reranker 路由 | 是 | `.env` (`RETR_RERANK_*`), `configs/models/reranker_routes.example.json` |
| `retrieval.recall` | Recall 路由 | 是 | `.env` (`RETR_RECALL_*`), `configs/models/recall_routes.example.json` |
| `edge.rpc.auth_key` | Edge HMAC 鉴权密钥 | 是 | `.env` (`EDGE_HMAC_SHARED_KEY(_REF)`) |
| `edge.asr.faster_whisper` | Edge ASR 运行时 | 是 | `edge_client/senses/asr.py` + `.env` (`EDGE_ASR_ENABLED`) |
| `edge.tts.gpt_sovits` | Edge TTS 运行时 | 是 | `edge_client/actuators/tts_engine.py` + `.env` (`EDGE_TTS_ENABLED`) |
| `edge.avatar.vtube_studio` | VTube Studio 实时驱动 | 否 | `edge_client/actuators/vts_driver.py` |
| `edge.fallback_llm.local` | Edge 兜底 LLM（草案输出） | 否 | `.env` (`EDGE_FALLBACK_LLM_ENABLED`), `edge_client/rpc/fallback_llm.py` |
| `host.rate_limit.redis` | Host 限流 Redis 后端 | 否 | `.env` (`HOST_RATE_LIMIT_BACKEND=redis`), `host_brain/governor/rate_limit_store.py` |
| `sandbox.docker_runtime` | 沙箱 Docker 隔离后端 | 否 | `SANDBOX_*` 变量 + `host_brain/airlock/docker_sandbox.py` |
| `secret.profile_store` | Key Profile/Secret Store | 是 | `configs/secrets/*.json` |

---

## 3. 配置方式

### 3.1 Host 端（含依赖）

1. 准备环境变量文件（推荐从 `.env.example` 复制）。
2. 配置 `HOST_APP_HOST`、`HOST_APP_PORT`。
3. 配置 Neo4j/Chroma 连接（`BOND_NEO4J_*`、`CHROMA_*`）。
4. 配置 LLM provider 与密钥（`HOST_LLM_PROVIDER` + `*_API_KEY` 或 `*_API_KEY_REF`）。
5. 可选：配置限流后端（`HOST_RATE_LIMIT_BACKEND=memory/sqlite/redis`）。
6. 执行：

```powershell
.\scripts\runtime\start_host_side.ps1
```

若 Neo4j 不是放在 `runtime/neo4j/neo4j-community-*`，使用：

```powershell
.\scripts\runtime\start_host_side.ps1 -Neo4jHome "C:\path\to\neo4j-home"
```

若本机无 `JAVA_HOME`，脚本会尝试 `C:\Program Files\Microsoft\jdk-21.0.10.7-hotspot`。

### 3.2 Edge 端

1. 配置 `EDGE_RPC_HOST`、`EDGE_RPC_PORT`。
2. 配置鉴权密钥策略：
`dev/test` 可用 `EDGE_ALLOW_INSECURE_AUTH=true`；`prod` 需配置 `EDGE_HMAC_SHARED_KEY` 或 `EDGE_HMAC_SHARED_KEY_REF`，并关闭 insecure auth。
3. 配置能力开关：`EDGE_ASR_ENABLED`、`EDGE_TTS_ENABLED`、`EDGE_BARGE_IN_ENABLED`。
4. 可选：开启 Edge 兜底 LLM（草案模式）`EDGE_FALLBACK_LLM_ENABLED=true`。
5. 执行：

```powershell
.\scripts\runtime\start_edge_side.ps1
```

### 3.3 模型与检索路由

1. Embedding 路由：`RETR_EMBED_*`，示例见 `configs/models/embedding_routes.example.json`。
2. Reranker 路由：`RETR_RERANK_*`，示例见 `configs/models/reranker_routes.example.json`。
3. Recall 路由：`RETR_RECALL_*`，示例见 `configs/models/recall_routes.example.json`。
4. 模型平面节点与模型名见：`configs/model_plane/node_registry.yaml`。

### 3.4 可选沙箱隔离后端（Docker）

1. 配置 `SANDBOX_ISOLATION_BACKEND=docker`。
2. 配置 `SANDBOX_TIMEOUT_S`、`SANDBOX_MAX_CODE_BYTES`、`SANDBOX_DOCKER_IMAGE`。
3. 保障本机 Docker Engine 可用；不可用时应降级到 subprocess 并保留稳定错误码。

---

## 4. 关键服务信息

### 4.1 Neo4j

- 默认端点：`bolt://127.0.0.1:7687`
- 核心变量：`BOND_NEO4J_URI`、`BOND_NEO4J_DATABASE`、`BOND_NEO4J_AUTH_REF`
- 健康检查：TCP 7687 监听

### 4.2 Chroma

- 默认端点：`http://127.0.0.1:8000`
- 核心变量：`CHROMA_ENDPOINT`、`CHROMA_TENANT`、`CHROMA_DATABASE`、`CHROMA_DEFAULT_COLLECTION`
- 健康检查：`/api/v2/heartbeat`

### 4.3 LLM Provider

- 选择器：`HOST_LLM_PROVIDER`（`openai/gemini/deepseek/mock/disabled`）
- 密钥：`OPENAI_API_KEY(_REF)`、`GEMINI_API_KEY(_REF)`、`DEEPSEEK_API_KEY(_REF)`
- 模型示例：
`gpt-5.2`、`gpt-5-mini`、`gpt-5.2-pro`、`gemini-2.5-flash`、`gemini-2.5-pro`、`deepseek-chat`

### 4.4 Edge ASR / TTS / VTS

- ASR：`faster-whisper`，默认模型 `large-v3`，默认设备 `cuda`
- TTS：`GPT-SoVITS`，默认模型路径 `models/gpt_sovits`，默认设备 `cuda`
- VTS：默认 `localhost:8001`，token 文件 `.vts_token`

### 4.5 Edge Fallback LLM（可选）

- 开关：`EDGE_FALLBACK_LLM_ENABLED`
- 默认模型标识：`ollama:3b-q4`
- 约束：仅允许 draft-only 输出，不可绕过 Host 治理链。

### 4.6 Host Rate Limit Redis（可选）

- 启用方式：`HOST_RATE_LIMIT_BACKEND=redis`
- 默认地址：`redis://localhost:6379/0`
- 降级策略：Redis 不可用时应降级到内存后端并保持稳定原因码。

### 4.7 沙箱 Docker 后端（可选）

- 启用方式：`SANDBOX_ISOLATION_BACKEND=docker`
- 依赖：Docker Engine 20+（建议）
- 失败策略：容器不可用时降级到 subprocess，不中断主流程。

---

## 5. 验证方式

1. Host 健康检查：

```powershell
Invoke-WebRequest http://127.0.0.1:18080/health
```

2. Edge 健康检查：

```powershell
Invoke-WebRequest http://127.0.0.1:17800/v1/edge/health
```

3. 可选 Redis 探针：

```powershell
Test-NetConnection -ComputerName 127.0.0.1 -Port 6379
```

4. 本地依赖停止：

```powershell
.\scripts\runtime\stop_local_services.ps1
.\scripts\runtime\stop_edge.ps1
```

---

## 6. 维护约定

- 新增外部依赖时，必须同步更新：
`configs/runtime/external_services.json` 与本文件。
- 若涉及模型名、路径、端点、环境变量变更，需同步更新 `.env.example` 与对应启动脚本。
- 文档与清单出现冲突时，以 `configs/runtime/external_services.json` 为准，再回补文档描述。
