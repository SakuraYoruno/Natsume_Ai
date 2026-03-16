受众：PROJECT
标题：本地模型与云模型变更记录（2026-03-05）

## 1. 记录范围
- 记录时间：2026-03-05 23:16:47 +08:00
- 目标：汇总本轮“本地模型链路 + 云模型链路”改动，以及本轮新增文档与验证证据。
- 范围：Host 端检索/重排/生成链路，不包含 Edge 本地 ASR/TTS 模型变更。

## 2. 本地模型链路变更（Embedding / Reranker / Canon）

### 2.1 本地模型配置现状（保持 Qwen3）
- `.env` 当前检索配置仍为本地模型：
  - `RETR_EMBED_PROVIDER=sentence_transformers`
  - `RETR_EMBED_MODEL_ID=models/Qwen3-Embedding-0.6B`
  - `RETR_EMBED_DIM=1024`
  - `RETR_RERANK_PROVIDER=qwen3_reranker`
  - `RETR_RERANK_MODEL_ID=models/Qwen3-Reranker-0.6B`

### 2.2 Canon 向量同步链路改造
- [host_brain/memory/canon_vector_projection.py](/D:/Nastume_Ai/host_brain/memory/canon_vector_projection.py)
  - 从直接 `embed_text(...)` 改为 `EmbeddingServiceContract` 路由调用。
  - 增加节点感知路由：支持 `node_id` 参与 embedding route 选择。
  - 增加 key profile / secret store 解析逻辑（用于需要云 embedding provider 的路由场景）。
  - 新增维度强校验：`route_dim` 与 Chroma `collection_dim` 不一致时直接报错，避免脏索引。
  - 同步写入 metadata：`embedding_provider`、`embedding_model_id`、`embedding_route_id`。

- [scripts/runtime/sync_canon_vector_index.py](/D:/Nastume_Ai/scripts/runtime/sync_canon_vector_index.py)
  - 新增 `--node-id` 参数，向 Canon 同步过程透传节点标识。

## 3. 云模型链路变更（Gemini 节点 + Host 生成）

### 3.1 云端密钥与节点配置
- `configs/secrets/key_profiles.runtime.json` 已切到 Gemini 节点方案：
  - `host.llm` 覆盖 `natsu-node-01` 到 `natsu-node-09`。
  - `secret_ref` 统一使用 `sec://gemini/natsu-node-xx`。
- `configs/secrets/secret_store.runtime.local.json` 已按上述 `secret_ref` 填入实际云端 key（文档不记录明文）。

### 3.2 Host 配置加载与密钥注入
- [host_brain/app/config.py](/D:/Nastume_Ai/host_brain/app/config.py)
  - 新增运行时自动解析：`key_profiles`、`secret_store`、`current_epoch`、`node_id`。
  - 新增 `_inject_resolved_llm_key(...)`：当通过 `*_REF` 解析到 key 后，自动注入进程环境变量，供下游 SDK 使用。
  - 支持从 `HOST_RETR_NODE_ID` / `Natsume_HOST_NODE_ID` / `HOST_NODE_ID` 与 profile 文件推导节点。

### 3.3 生成链路编排（LangGraph + LangChain）
- [host_brain/app/fastapi_app.py](/D:/Nastume_Ai/host_brain/app/fastapi_app.py)
  - `/v1/chat` APPROVE 分支改为走 `GraphRuntimeContract.run_graph(...)`。
  - 模型节点 provider 通过 `_build_host_provider_fn(...)` 构建：
    - 有 key：走 `langchain_openai.ChatOpenAI`（Gemini 使用 OpenAI-compatible base URL）。
    - 无 key：走 host mock provider（降级）。
  - 新增 `audit_refs.llm` 字段，记录 `status/stage/provider/model_id`。

## 4. 关于“FastAPI 为何还会看到 HTTP”
- 当前实现不是在路由里手写 provider HTTP 请求。
- 结构是：
  - FastAPI 负责入口与组装 payload；
  - LangGraph（`GraphRuntimeContract`）负责 agent/model 节点编排；
  - LangChain `ChatOpenAI` 负责模型 SDK 调用；
  - SDK 内部必然通过 HTTP 与云模型通信。
- 结论：HTTP 仍存在，但已下沉到 LangChain provider 层，不是业务路由直连 HTTP。

## 5. 本轮验证证据

### 5.1 单元测试
- 命令：
```bash
python -m pytest tests/unit/test_chat_graph_runtime_path.py tests/unit/test_host_config_runtime_secrets.py -q
```
- 结果：`3 passed in 0.66s`

### 5.2 真实生成链路（Gemini）
- 执行方式：FastAPI TestClient + 真实 Gemini key（来自 `secret_store.runtime.local.json`）。
- 结果摘要：
  - `http_status=200`
  - `decision=APPROVE`
  - `quality_stage=S_COMPLETE`
  - `llm_status=OK`
  - `llm_stage=S_ORCH_RUN`
  - `llm_provider=gemini`
  - `llm_model_id=gemini-2.5-flash`

## 6. 本轮新增文档
- [MODEL_LOCAL_CLOUD_CHANGELOG_2026-03-05.md](/D:/Nastume_Ai/docs/50_release/changelog/MODEL_LOCAL_CLOUD_CHANGELOG_2026-03-05.md)

## 7. 关联参考
- [test_report_2026-03-05.md](/D:/Nastume_Ai/docs/45_testing/test_reports/test_report_2026-03-05.md)
- [HOST_REAL_RUN_TEST_2026-03-04.md](/D:/Nastume_Ai/docs/45_testing/test_reports/host_chat/HOST_REAL_RUN_TEST_2026-03-04.md)
