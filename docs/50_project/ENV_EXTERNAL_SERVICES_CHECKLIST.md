受众：PROJECT
标题：外部环境与服务实装清单（九节点 / 密钥 / 图编排 / Neo4j / Chroma / Embedding / Reranker）

版本：v1.0
日期：2026-03-01
定位：用于把“已完成的代码契约”落地为“可运行环境”。本清单只描述字段与验收口径，不包含任何终端操作片段。

---

## 1. 目标与完成定义

目标：
- 把九节点清单、密钥 Profile、模型路由、图编排、外部数据库配置补齐到仓库配置资产。
- 使运行面可以稳定产出 readiness 报告，并把不可用场景归类到稳定 `reason_code`。

完成定义（全部满足才算完成）：
- 配置资产齐全：节点清单、密钥配置、模型路由、图编排配置均有可解析内容。
- Host/Edge 配置可加载：无必填缺失；可用与降级场景均有稳定输出结构。
- Neo4j/Chroma 探针可解释：依赖可用时 `UP`，不可用时有稳定 `reason_code/stage`。
- 检索链可解释：Embedding/Reranker 路由可解析，异常映射稳定。
- 环境就绪报告可用：`readiness_status` 能稳定区分 READY/DEGRADED/FAILED。

---

## 2. 配置资产清单（文件级）

### 2.1 九节点编排清单
- 文件：`configs/deploy/node_inventory_9.json`
- 必填字段：`node_id`、`region`、`role`、`services[]`、`health_endpoint`、`key_profile_id`
- 一致性要求：
  - `node_id` 唯一，目标数量固定为 9。
  - `services[]` 与节点角色一致，且覆盖 Host/Edge 必需服务。
  - `key_profile_id` 必须能在密钥 Profile 中找到对应条目。

### 2.2 密钥 Profile 清单
- 文件：`configs/secrets/key_profiles.example.json`（实装时替换为运行时版本，不落盘明文）
- 必填字段：`provider`、`node_id`、`scope`、`secret_ref`、`rotation_epoch`、`status`
- 一致性要求：
  - `node_id` 必须在九节点清单中存在。
  - `scope` 与使用方匹配：`host.llm` / `edge.rpc.auth`。
  - 过期或作用域不匹配必须稳定映射为拒绝码，不得静默放行。

### 2.3 Embedding 路由清单
- 文件：`configs/models/embedding_routes.example.json`
- 必填字段：`provider`、`model_id`、`dim`、`timeout_ms`、`node_selector`
- 一致性要求：
  - `default_route` 指向有效路由。
  - `dim` 与向量库集合维度一致。
  - `node_selector` 能定位到有效节点范围。

### 2.4 Reranker 路由清单
- 文件：`configs/models/reranker_routes.example.json`
- 必填字段：`provider`、`model_id`、`top_k`、`timeout_ms`、`node_selector`
- 一致性要求：
  - `default_route` 指向有效路由。
  - `top_k` 为正整数，且在实际候选规模内可回退。
  - 失败场景可稳定归类（down/timeout/invalid score）。

### 2.5 图编排运行时配置
- 参考资产：`tests/fixtures/orchestration/good/graph_main.json`
- 一致性要求：
  - 图节点与边必须闭合（无悬空节点、无无效边）。
  - fallback 边存在并可回放。
  - 编排失败不得绕过 Governor/Token/Audit。

---

## 3. 环境变量清单（运行时）

### 3.1 Host 关键变量
- 变量组：`NATSUME_ENV`、`HOST_LLM_PROVIDER`、`HOST_LLM_TIMEOUT_MS`、`HOST_LLM_MAX_RETRIES`、`HOST_COLD_START_ALLOWED`、`HOST_ENABLE_RETRIEVAL`、`HOST_ENABLE_VISION`、`HOST_ENABLE_EDGE_RPC`
- 密钥变量：`OPENAI_API_KEY` 或 `OPENAI_API_KEY_REF`
- 验收口径：
  - `prod` 且冷启动不允许时，缺 key 必须失败。
  - 冷启动允许时，缺 key 必须进入可审计降级，不得伪执行。

### 3.2 Edge 关键变量
- 变量组：`EDGE_RPC_HOST`、`EDGE_RPC_PORT`、`EDGE_ALLOW_INSECURE_AUTH`、`EDGE_RPC_GATEWAY_ENABLED`、`EDGE_ASR_ENABLED`、`EDGE_TTS_ENABLED`、`EDGE_BARGE_IN_ENABLED`、`EDGE_FALLBACK_LLM_ENABLED`、`EDGE_RESOURCE_GATE_ENABLED`、`EDGE_MAX_VRAM_MB`、`EDGE_MAX_QUEUE_LENGTH`
- 密钥变量：`EDGE_HMAC_SHARED_KEY` 或 `EDGE_HMAC_SHARED_KEY_REF`
- 验收口径：
  - `prod` 且禁用不安全鉴权时，缺 HMAC 必须失败。
  - `EDGE_RPC_GATEWAY_ENABLED=false` 时，语音链必须整体降级并可解释。

### 3.3 Neo4j / Chroma / Retrieval 变量
- Neo4j：`BOND_NEO4J_ENABLED`、`BOND_NEO4J_URI`、`BOND_NEO4J_DATABASE`、`BOND_NEO4J_AUTH_REF`、`BOND_NEO4J_POOL_SIZE`、`BOND_NEO4J_TLS_ENABLED`
- Chroma：`CHROMA_ENABLED`、`CHROMA_ENDPOINT`、`CHROMA_TENANT`、`CHROMA_DATABASE`、`CHROMA_DEFAULT_COLLECTION`、`CHROMA_COLLECTION_PROFILES_JSON`
- Embedding：`RETR_EMBED_ENABLED`、`RETR_EMBED_DEFAULT_ROUTE`、`RETR_EMBED_ROUTES_JSON`
- Reranker：`RETR_RERANK_ENABLED`、`RETR_RERANK_DEFAULT_ROUTE`、`RETR_RERANK_ROUTES_JSON`
- 验收口径：
  - 依赖不可用时必须进入 `DEGRADED/FAILED` 且带稳定 `reason_code/stage`。
  - 路由或配置格式错误必须被显式拒绝，不得隐式吞错。

---

## 4. 联调顺序（推荐）

1. 先完成九节点清单与密钥 Profile 对齐（`node_id` + `key_profile_id`）。
2. 再完成 Host/Edge 环境变量装配（先 dev/staging，再 prod）。
3. 然后完成 Neo4j 与 Chroma 外部依赖配置与探针对齐。
4. 最后完成 Embedding/Reranker 路由与图编排配置，并生成环境就绪报告。

---

## 5. 风险与失败映射（便于定位）

- 节点清单问题：优先检查 `node_id` 唯一性、角色冲突、必需服务缺失、`key_profile_id` 缺口。
- 密钥问题：优先检查 `scope`、`status`、`rotation_epoch`、`secret_ref` 可解析性。
- Neo4j 问题：按 `leader_unavailable / auth_failed / schema_drift` 分类定位。
- Chroma 问题：按 `endpoint_down / collection_missing / vector_dim_mismatch` 分类定位。
- Embedding 问题：按 `RETR_EMBED_UNAVAILABLE / RETR_EMBED_TIMEOUT / RETR_EMBED_INPUT_INVALID` 分类定位。
- Reranker 问题：按 `RETR_RERANK_UNAVAILABLE / RETR_RERANK_TIMEOUT / RETR_RERANK_INPUT_INVALID` 分类定位。

---

## 6. 关联资产

- 节点清单：`configs/deploy/node_inventory_9.json`
- 密钥模板：`configs/secrets/key_profiles.example.json`
- Embedding 路由模板：`configs/models/embedding_routes.example.json`
- Reranker 路由模板：`configs/models/reranker_routes.example.json`
- Flow 指针：`flows/active.pointer`
- Trace：`docs/60_trace/requirements_trace.md`
