# QGBT240414H Gemini Key Failover 修复留痕

## 结论
- 之前确实存在“限流/失败后不自动切换”的缺口。
- 现在已改为：Gemini 节点调用默认支持 key pool 自动切换（不是只靠单 key 重试）。

## 根因复盘
- 旧逻辑只在 `core_reasoner_primary + gemini-2.5-pro` 上做重试和模型 fallback。
- 其他节点失败时直接降级，不会换 key。
- 即使触发模型 fallback，也沿用同一 key，遇到 `429/quota` 仍会卡死。

## 代码变更
- [`provider_factory.py`](/D:/Natsume_Ai/host_brain/app/provider_factory.py)
  - 新增全节点 Gemini key failover。
  - 新增失败分类：`429/quota/timeout/5xx` 等触发换 key；`400 invalid argument` 等不盲切。
  - 统一 provider 调用为 sync/async 兼容。
  - failover 全部失败时抛出带 `node/model/tried` 的错误上下文。
- [`test_provider_factory_gemini_failover.py`](/D:/Natsume_Ai/tests/unit/test_provider_factory_gemini_failover.py)
  - 新增 3 条单测：429 切 key、400 不切 key、开关关闭。
- 配置
  - [`.env`](/D:/Natsume_Ai/.env): `HOST_GEMINI_KEY_FAILOVER_ENABLED=true`、`HOST_GEMINI_KEY_FAILOVER_MAX_ATTEMPTS=9`
  - [`.env.example`](/D:/Natsume_Ai/.env.example): 同步新增示例项

## 验证结果
- `pytest tests/unit/test_provider_factory_gemini_failover.py -q` -> `3 passed`
- `pytest tests/unit/test_provider_factory_templates.py tests/unit/test_model_router_runtime.py -q` -> `16 passed`
- `pytest tests/unit/test_input_routes_worker.py -q` -> `27 passed`
