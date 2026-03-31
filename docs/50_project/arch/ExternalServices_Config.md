受众：MASTER
标题：外部服务配置手册（纯 .env 架构）
版本：v3.0
日期：2026-03-27

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

### 3.3 统一消息网关（Gateway）

- `EDGE_GATEWAY_ENABLED` / `EDGE_GATEWAY_PORT` / `EDGE_GATEWAY_PLATFORMS`
- 微信：`EDGE_WECHAT_BRIDGE_*` / `WECHAT_SELF_WXID` / `WECHAT_DB_STORAGE_DIR`
- QQ：`QQ_ONEBOT_API_BASE` / `QQ_SELF_ID` / `EDGE_QQ_ONEBOT_*`
- Telegram：`TELEGRAM_API_ID` / `TELEGRAM_API_HASH` / `TELEGRAM_PHONE` / `TELEGRAM_SESSION_NAME`
- 飞书：`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_ENCRYPT_KEY`
- 企微：`WECOM_CORP_ID` / `WECOM_CORP_SECRET` / `WECOM_AGENT_ID`
- LINE：`LINE_CHANNEL_ACCESS_TOKEN` / `LINE_CHANNEL_SECRET`
- 拟人化发送：`EDGE_HUMANIZE_*`（TYPING_SPEED_CPS / BASE_DELAY_S / MAX_DELAY_S / JITTER）

### 3.4 Host 本地模型与 Skills Hub

- Host 14B 本地模型：`HOST_CHAT_HOST_14B_*`（ENABLED / MODEL_ID / BASE_URL / TIMEOUT_MS / HEALTHCHECK_*）
- Skills Hub：`HOST_SKILL_HUB_*`（ENABLED / DIR / REGISTRY_URL）
- 外部 Skill 扫描：`HOST_SKILL_SCAN_EXTERNAL` / `HOST_SKILL_EXTERNAL_DIRS`

### 3.5 数据与检索

- Neo4j：`BOND_NEO4J_*`
- Chroma：`CHROMA_*`
- Embedding：`RETR_EMBED_*`
- Reranker：`RETR_RERANK_*`
- Recall：`RETR_RECALL_*`

### 3.6 密钥与引用

- LLM 密钥（本地注入）：`OPENAI_API_KEY` / `CODEX_API_KEY` / `GEMINI_API_KEY` / `DEEPSEEK_API_KEY`
- Edge 鉴权密钥：`EDGE_HMAC_SHARED_KEY`
- 平台凭据（按需配置）：
  - Telegram：`TELEGRAM_API_ID` / `TELEGRAM_API_HASH`
  - 飞书：`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_ENCRYPT_KEY` / `FEISHU_VERIFICATION_TOKEN`
  - 企微：`WECOM_CORP_ID` / `WECOM_CORP_SECRET`
  - LINE：`LINE_CHANNEL_ACCESS_TOKEN` / `LINE_CHANNEL_SECRET`
- 引用键（可提交）：`OPENAI_API_KEY_REF` / `CODEX_API_KEY_REF` / `GEMINI_API_KEY_REF` / `DEEPSEEK_API_KEY_REF` / `EDGE_HMAC_SHARED_KEY_REF`

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

---

## 2026-03-31 LLM 回退链补充

- Host 云端主链新增 Codex relay，远端优先顺序为 `Gemini -> Codex relay -> DeepSeek`
- 需要补齐的环境变量：`HOST_GEMINI_BASE_URL`、`HOST_GEMINI_MODEL_ID`、`GEMINI_API_KEY`、`HOST_CODEX_BASE_URL`、`HOST_CODEX_MODEL_ID`、`CODEX_API_KEY`、`HOST_DEEPSEEK_BASE_URL`、`HOST_DEEPSEEK_MODEL_ID`、`DEEPSEEK_API_KEY`
- 本地模型继续使用 `HOST_CHAT_HOST_14B_*`，但仅在全部远端失败时兜底
