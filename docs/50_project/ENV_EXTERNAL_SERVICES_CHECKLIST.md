受众：PROJECT
标题：环境与外部服务落地检查清单（纯 .env）
版本：v2.0
日期：2026-03-04

---

## 1. 目标

确认仓库在“纯 `.env` 架构”下可稳定启动、可探测、可降级。

完成标准：

- `.env` 可直接驱动 Host/Edge/Neo4j/Chroma 启动
- `.env` 缺失时，可由 `.env.example` 提供最小可运行降级配置
- 密钥不落入版本库

---

## 2. 文件级检查

必须存在：

- `.env`（本地私有）
- `.env.example`（示例模板）
- `configs/secrets/key_profiles.example.json`
- `configs/secrets/secret_store.runtime.example.json`

必须不存在（已迁移）：

- `configs/runtime/external_services.json`
- `configs/runtime/local_services.env`
- `configs/deploy/node_inventory_9.json`
- `configs/models/*.json`
- `configs/model_plane/*.yaml`

---

## 3. 变量完整性检查

### 3.1 Host

- `Natsume_ENV`
- `HOST_APP_HOST` / `HOST_APP_PORT`
- `HOST_LLM_PROVIDER` / `HOST_LLM_TIMEOUT_MS` / `HOST_LLM_MAX_RETRIES`
- `HOST_ENABLE_RETRIEVAL` / `HOST_ENABLE_VISION` / `HOST_ENABLE_EDGE_RPC`
- `HOST_RATE_LIMIT_BACKEND` / `HOST_RATE_LIMIT_WINDOW_MS` / `HOST_RATE_LIMIT_MAX_REQUESTS`

### 3.2 Edge

- `EDGE_RPC_HOST` / `EDGE_RPC_PORT`
- `EDGE_RPC_GATEWAY_ENABLED`
- `EDGE_ASR_ENABLED` / `EDGE_TTS_ENABLED` / `EDGE_BARGE_IN_ENABLED`
- `EDGE_RESOURCE_GATE_ENABLED` / `EDGE_MAX_VRAM_MB` / `EDGE_MAX_QUEUE_LENGTH`
- `EDGE_VTS_HOST` / `EDGE_VTS_PORT`

### 3.3 Data & Retrieval

- `BOND_NEO4J_*`
- `CHROMA_*`
- `RETR_EMBED_*`
- `RETR_RERANK_*`
- `RETR_RECALL_*`

### 3.4 Secrets

- 真实密钥默认留空：
  - `OPENAI_API_KEY`
  - `GEMINI_API_KEY`
  - `DEEPSEEK_API_KEY`
  - `EDGE_HMAC_SHARED_KEY`
- 使用 `*_REF` 指向本地 secret store。

---

## 4. 行为验证

### 4.1 启动链路

```powershell
.\scripts\runtime\start_local_services.ps1
.\scripts\runtime\start_host_side.ps1
.\scripts\runtime\start_edge.ps1
```

验收：

- Host `/health` 返回 2xx
- Edge `/v1/edge/health` 返回 2xx
- Neo4j/Chroma 端口可达

### 4.2 配置降级链路

步骤：

1. 临时移走 `.env`
2. 执行 `python scripts/runtime/check_external_env.py`
3. 执行 `python scripts/runtime/generate_env_readiness_report.py`

验收：

- 脚本可从 `.env.example` 启动检查
- 结果中降级原因清晰，不出现空配置崩溃

### 4.3 安全检查

- `git status` 不应出现 `.env`
- `.env.example` 不得包含真实密钥值

---

## 5. 变更留痕

每次配置架构调整都要更新：

- 本检查清单
- `docs/50_project/ExternalServices_Config.md`
- 对应 trace 文档（`docs/60_trace/*`）
