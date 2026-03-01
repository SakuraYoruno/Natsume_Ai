受众：CODEX
标题：E2E Tests 占位

最小链路目标：
1. 输入请求进入 Host。
2. 产生 governor 决策。
3. （可选）触发 skill 执行。
4. 审计链路可回放。

当前已落地：
1. 主链 e2e（`test_main_chain_e2e.py`）：APPROVE 执行闭环、重放拒绝、过期拒绝、缺票据拒绝、门禁拒绝。
2. 降级 e2e：LLM gateway 冷启动不可用时保持单 JSON + draft_only + reason_code/stage 可解释。
3. 演练 e2e（`test_production_drill_e2e.py`）：`cold_start_no_key/provider_down/rollback_conflict` 三场景统一报告、故障分类与 replay_ref 回放链路稳定。

说明：后续按 TASK 继续补多轮对话与跨通道场景 e2e。
