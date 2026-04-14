# QGBT240413H 首判模型升级与回归记录

日期：2026-04-13  
范围：Gateway 入站 LLM 首判（`executive_router`）

## 1) 代码变更

- 默认首判模型从 `gemini-2.5-flash` 升级为 `gemini-3.1-pro-preview`  
  文件：`host_brain/app/model_router.py`  
  位置：`_GEMINI_EXECUTIVE_ROUTER_DEFAULT_MODEL_ID`
- `.env.example` 新增独立覆盖项 `HOST_EXECUTIVE_ROUTER_MODEL_ID`，默认 `gemini-3.1-pro-preview`  
  文件：`.env.example`

## 2) 当前模型优先级（首判）

1. `HOST_EXECUTIVE_ROUTER_MODEL_ID`
2. `HOST_DISPATCH_AGENT_MODEL_ID`（兜底）
3. 代码默认：`gemini-3.1-pro-preview`

## 3) 测试执行

执行命令：

```powershell
pytest tests/unit/test_model_router_runtime.py -q
pytest tests/unit/test_input_routes_worker.py -q
```

结果：

- `tests/unit/test_model_router_runtime.py`: `13 passed`
- `tests/unit/test_input_routes_worker.py`: `23 passed`

结论：本次首判模型升级未引入上述关键链路回归。
