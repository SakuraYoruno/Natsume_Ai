受众：MASTER
标题：外部服务配置手册（纯 .env 架构）
版本：v2.0
日期：2026-03-04

---

## 1. 架构结论

当前仓库采用“纯 `.env` 架构”：

- 运行主配置：仓库根 `.env`（本地私有，git 忽略）
- 示例与降级：仓库根 `.env.example`（版本管理，禁止真实密钥）
- 密钥映射：`configs/secrets/*.json`（仅保存 `*_REF -> secret` 的本地解析数据）

除密钥映射外，不再依赖 `configs/runtime/*.json`、`configs/models/*.json`、`configs/model_plane/*.yaml` 等外置配置文件。

---

## 2. 加载优先级

以下脚本统一按同一优先级读取配置：

1. `.env`
2. `.env.example`（当 `.env` 不存在时）

覆盖范围：

- `scripts/runtime/start_host_side.ps1`
- `scripts/runtime/start_docker_services.ps1`
- `scripts/runtime/start_edge.ps1`
- `scripts/runtime/stop_host_side.ps1`
- `scripts/runtime/stop_docker_services.ps1`
- `scripts/runtime/check_external_env.py`
- `scripts/runtime/generate_env_readiness_report.py`

---

## 3. 配置分层

### 3.1 Host 核心

- `Natsume_ENV`
- `HOST_APP_*`
- `HOST_LLM_*`
- `HOST_ENABLE_*`
- `HOST_RATE_LIMIT_*`

### 3.2 Edge 核心

- `EDGE_RPC_*`
- `EDGE_ALLOW_INSECURE_AUTH`
- `EDGE_*_ENABLED`
- `EDGE_ASR_*`
- `EDGE_TTS_*`
- `EDGE_VTS_*`
- `EDGE_EVENT_REPORT_*`

### 3.3 数据与检索

- Neo4j：`BOND_NEO4J_*`
- Chroma：`CHROMA_*`
- Embedding：`RETR_EMBED_*`
- Reranker：`RETR_RERANK_*`
- Recall：`RETR_RECALL_*`

### 3.4 密钥与引用

- 真实密钥（本地注入）：`OPENAI_API_KEY` / `GEMINI_API_KEY` / `DEEPSEEK_API_KEY` / `EDGE_HMAC_SHARED_KEY`
- 引用键（可提交）：`*_API_KEY_REF` / `EDGE_HMAC_SHARED_KEY_REF`

---

## 4. 维护规则

- 新增或变更配置项时，必须同步更新以下三个位置：
  - `.env`（本地真实运行）
  - `.env.example`（示例与降级）
  - 本文档（分组与说明）
- `.env.example` 只能包含：
  - 默认值
  - 示例值
  - 非敏感 `*_REF`
- `.env.example` 禁止包含任何真实密钥或私有地址凭据。

---

## 5. 运行与验证

### 5.1 启动

```powershell
.\scripts\runtime\start_docker_services.ps1
.\scripts\runtime\start_host_side.ps1
.\scripts\runtime\start_edge.ps1
```

### 5.2 检查

```bash
python scripts/runtime/check_external_env.py
python scripts/runtime/generate_env_readiness_report.py
```

### 5.3 停止

```powershell
.\scripts\runtime\stop_host_side.ps1
.\scripts\runtime\stop_docker_services.ps1
.\scripts\runtime\stop_edge.ps1
```

---

## 6. 冲突判定

当文档描述与代码行为不一致时，以“脚本实际加载顺序（`.env` -> `.env.example`）”为准；随后必须回补文档。
