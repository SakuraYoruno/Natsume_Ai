受众：CODEX
标题：Refusal Templates（QA 降级口径）

版本：v1.0
日期：2026-02-28

用途：统一 QA 降级时的叙事范式（非执行事实、可解析 JSON、可统计 reason_code）。

## 默认降级模板（S_QA_DOWNGRADE）
- narrative.text：当前无法确认执行结果，我会先保持草案回复并等待后续治理。
- intent_draft：空对象（不得暗示已执行）。
- meta：
  - draft_only=true
  - degraded=true
  - reason_code=<QA reason>
  - stage=S_QA_DOWNGRADE

## 超时降级模板（S_QA_TIMEOUT）
- narrative.text：当前生成链路超时，我会先返回安全草案并等待后续处理。
- intent_draft：空对象
- meta：
  - draft_only=true
  - degraded=true
  - reason_code=LLM_QA_TIMEOUT
  - stage=S_QA_TIMEOUT

## 约束
- 模板输出必须保持 Strict JSON 单对象。
- 模板不得出现“已执行/已保存/已确认”叙事。
