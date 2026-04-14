# QGBT240414I - Gemini 兜底模型切换为 Flash Lite

## 目标
- 将 Gemini 主模型失败后的默认兜底模型固定为 `gemini-2.5-flash-lite`。
- 保留已实现的 Key Pool 轮换能力，避免因高配模型日限额导致连续失败。

## 代码变更
- `host_brain/app/provider_factory.py`
  - `_GEMINI_PRIMARY_FALLBACK_MODEL_ID` 从 `gemini-3.1-pro-preview` 改为 `gemini-2.5-flash-lite`。
- `host_brain/app/model_capability_registry.py`
  - 新增 `gemini-2.5-flash-lite` 能力画像，避免路由治理落入 unknown profile。
- `tests/unit/test_provider_factory_gemini_failover.py`
  - 新增 `test_gemini_primary_fallback_default_model_is_flash_lite`，锁定默认兜底模型。
- `.env.example`
  - 示例补充：`HOST_GEMINI_PRIMARY_FALLBACK_MODEL_ID=gemini-2.5-flash-lite`。
- `.env`
  - 本地运行配置新增：`HOST_GEMINI_PRIMARY_FALLBACK_MODEL_ID=gemini-2.5-flash-lite`。

## 验证命令与结果
1. `pytest tests/unit/test_provider_factory_gemini_failover.py -q`
   - 结果：`4 passed`
2. `pytest tests/unit/test_model_router_runtime.py tests/unit/test_provider_factory_templates.py -q`
   - 结果：`16 passed`

## 结论
- Gemini 主链路失败时，默认将降级到 `gemini-2.5-flash-lite`，不再默认落到 `gemini-3.1-pro-preview`。
- 该改动与既有 Key Failover 机制兼容，已通过相关单测验证。
