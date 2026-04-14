# QGBT240413G Gateway 意图路由复测摘要（2026-04-13）

## 本轮修复项

- 修复“翻译句子包含 bug 关键词时被错误覆盖到 `code_agent`”：
  - 现在翻译意图优先级高于代码关键词，命中翻译后直接返回，不再被后续代码规则覆盖。
- 修复“最近一轮追问被判 chat”：
  - 增加 `recent_turn` 强制纠偏，`你刚刚说了什么/上一句` 类请求固定进入最近轮次路径。
- 修复规则兜底记忆识别：
  - `保存这条记忆...` 现在可命中记忆写入兜底规则（`memory_store`）。

## 相关代码

- `host_brain/app/input_routes.py`
  - `_apply_gateway_force_override`
  - `_build_gateway_rule_route_decision`
  - `_build_gateway_intent_draft`
- `tests/unit/test_input_routes_worker.py`
  - 新增翻译优先、recent_turn纠偏、记忆兜底相关测试

## 批测留痕

- 结果明细（query -> route -> target）：
  - `runtime/test_reports/QGBT240413F_gateway_intent_route_batch.json`
  - `docs/test_reports/QGBT240413F_gateway_intent_route_batch.md`

## 回归测试结果

命令：
```powershell
pytest -q D:\Natsume_Ai\tests\unit\test_input_routes_worker.py D:\Natsume_Ai\tests\unit\test_input_routes_dispatch.py D:\Natsume_Ai\tests\unit\test_input_routes_smoke.py D:\Natsume_Ai\tests\unit\test_executive_runtime_entry.py D:\Natsume_Ai\tests\unit\test_executive_runtime_helpers.py D:\Natsume_Ai\tests\unit\test_skill_family_router.py
```

结果：`45 passed in 5.09s`

## 备注

- LLM首判在复测中仍出现上游高负载（503）与超时，系统已按设计降级到规则兜底，链路不中断。
