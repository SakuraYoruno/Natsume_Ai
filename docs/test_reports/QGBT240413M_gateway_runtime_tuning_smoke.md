# QGBT240413M Gateway 运行时调优冒烟（LLM首判 + 回包失败不降级）

日期：2026-04-13

## 本轮调优

运行时 `.env`：

- `HOST_GEMINI_PRIMARY_RETRY_ATTEMPTS=3`
- `HOST_GEMINI_PRIMARY_DISABLE_MODEL_FALLBACK=false`
- `HOST_EXECUTIVE_ROUTER_MODEL_ID=gemini-2.5-flash`
- `HOST_EXECUTIVE_ROUTER_TIMEOUT_MS=12000`
- `HOST_GATEWAY_INTENT_LLM_TIMEOUT_MS=12000`
- `HOST_GATEWAY_REPLY_FAILURE_DEGRADE_INPUT=false`

## 冒烟请求

- request_id：`req_ce2971096d8e438fbbeccae66d1fab08`
- query：`明天的计划帮我整理一个两步执行方案。`
- source/platform：`GATEWAY / qq`

## 关键观测

1. `INPUT_ROUTE_DECIDED` 已产生，detail：
   - `route=delegate target=intent_agent source=override llm_used=true`
2. 最终 `INPUT_COMPLETED`：
   - `status=OK`
   - `reason_code=None`
   - `gateway_status=failed`
3. 说明：
   - 首判 LLM 在本次请求未超时（日志中无该 request 的 `gateway_intent_llm_route_failed`）。
   - Gateway 回包失败已被隔离，不再污染 Host 主流程状态（`OK` 保留）。

## 结论

- 调优后，Host 侧“首判→技能执行→完成态”链路可稳定留痕。
- 在 Edge 不稳定场景下，主流程验收可继续进行（不再被回包失败统一打成 `DEGRADED`）。
