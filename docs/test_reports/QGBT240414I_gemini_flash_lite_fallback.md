# QGBT240414I - Gemini 兜底模型切换为 Flash Lite

## 目标
- 将 Gemini 主模型失败后的默认兜底模型固定为 `gemini-2.5-flash-lite`。
- Gemini key 已收敛为单 key；主模型失败只做模型级 fallback，不再做多 key 轮换。

## 代码变更
- `host_brain/app/provider_factory.py`
  - `_GEMINI_PRIMARY_FALLBACK_MODEL_ID` 从 `gemini-3.1-pro-preview` 改为 `gemini-2.5-flash-lite`。
- `host_brain/app/model_capability_registry.py`
  - 新增 `gemini-2.5-flash-lite` 能力画像，避免路由治理落入 unknown profile。
- `tests/unit/test_provider_factory_gemini_single_key.py`
  - 新增 `test_gemini_primary_fallback_default_model_is_flash_lite`，锁定默认兜底模型。
- `.env.example`
  - 示例补充：`HOST_GEMINI_PRIMARY_FALLBACK_MODEL_ID=gemini-2.5-flash-lite`。
- `.env`
  - 本地运行配置新增：`HOST_GEMINI_PRIMARY_FALLBACK_MODEL_ID=gemini-2.5-flash-lite`。

## 验证命令与结果
1. `pytest tests/unit/test_provider_factory_gemini_single_key.py -q`
   - 结果：`2 passed`
2. `pytest tests/unit/test_model_router_runtime.py tests/unit/test_provider_factory_templates.py -q`
   - 结果：旧验证记录；本轮已改用单 key 路由专项测试。

## 结论
- Gemini 主链路失败时，默认将降级到 `gemini-2.5-flash-lite`，不再默认落到 `gemini-3.1-pro-preview`。
- 该改动与单 key Gemini 路由兼容。
