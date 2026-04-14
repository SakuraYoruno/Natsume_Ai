# QGBT240413P llm_route_used 留痕补齐

日期：2026-04-13

## 问题

- `INPUT_ROUTE_DECIDED` 的 `detail` 中已有 `llm_used=true/false`。
- 但 payload 中缺少 `llm_route_used` 字段，导致报表只能靠 detail 文本解析，统计不稳定。

## 修复

修改文件：`host_brain/app/input_routes.py`

1. `ROUTE_DECIDED` 事件写入时，`input_payload` 增加：
   - `llm_route_used`
2. `COMPLETED` 事件写入时，`completed_payload` 增加：
   - `llm_route_used`
3. `_log_input_event()` 路由字段白名单 `route_fields` 增加：
   - `llm_route_used`

> 说明：第 3 点是关键，否则上游传了字段也会在入库前被裁剪。

## 测试

- 单测：`pytest tests/unit/test_input_routes_worker.py -q`  
  结果：`24 passed`
- 真实冒烟 request_id：`req_dab13cef03264c00bcd768b053f64023`
  - `INPUT_ROUTE_DECIDED.detail`: `route=delegate target=translate_agent source=llm llm_used=true`
  - payload: `route_source=llm`, `llm_route_used=True`

## 批测验证

- 参考批次：`QGBT240413O`
  - `route_decided_present=10/10`
  - `llm_route_used_true=7/10`
  - 路由来源分布：`llm=6, override=1, rule=3`

结论：`llm_route_used` 已可稳定用于结构化统计与看板展示。
