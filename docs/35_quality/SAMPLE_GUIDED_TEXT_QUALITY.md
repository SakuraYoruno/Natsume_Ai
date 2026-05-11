# Sample-Guided Text Quality

本次重构把中文 galgame 私聊质量从“继续补 forbidden pattern”迁到“多候选 + hard gate + 样本引导 soft scoring + rerank”。

## Hard Gate

Hard gate 只处理绝对不可发送的问题：

- 内部字段泄露
- JSON / 结构化输出泄露
- 乱码
- 明显重复破损
- 语言错误
- 明确身份错误
- 明确现实动作脑补
- 明显越界亲密
- 明显客服模板

`哥哥`、`我在这`、`找我`、`等你` 这类词不再作为全局 hard forbidden。它们只会在 `distance_control`、`role_fit` 或 `context_fit` 中按上下文扣分。

## Soft Scoring

`host_brain/persona/sample_guided_text_scorer.py` 输出以下维度：

- `naturalness`
- `role_fit`
- `context_fit`
- `usefulness`
- `non_customer_service`
- `non_oily`
- `distance_control`
- `technical_helpfulness`
- `emotional_containment`

场景权重：

- 技术/写作/下一步场景：优先 `usefulness` 与 `technical_helpfulness`，角色味降权。
- 日常短触碰场景：优先 `naturalness`、`distance_control`、`context_fit`。
- 高情绪场景：优先 `context_fit` 与 `emotional_containment`。

## Sample Bank

`PositiveStyleSample` 新增样本字段：

- `good_examples`
- `bad_examples`
- `quality_priorities`

未显式填写时，评分器复用既有 `safe_fragments` 作为 good reference，复用 `avoid` 作为 bad reference。样本用于 rerank 参考，不作为最终回复模板。

## Runtime Chain

`ChatKernelV2.generate()` 当前链路：

1. 生成 3 个候选，环境变量可用 `HOST_CHAT_KERNEL_V2_CANDIDATES` 调到 1-5。
2. `TextQualityGate` 只做最小 hard gate，legacy forbidden 默认软标记。
3. 每个候选进入 `score_galgame_candidate()`，内部调用样本引导 scorer。
4. `_select_galgame_soft_candidate()` 按 hard gate、总分、维度结果 rerank。
5. 默认不做 LLM repair，不因低分用 canned fallback 替换候选。
6. 只有没有可发送候选且不是绝对 hard gate 泄露时，才允许 contract fallback。
7. 最终只做最小后处理，例如重复称呼去重。

## Test Migration

保留并改写的测试：

- `test_sample_guided_text_scorer.py` 覆盖维度、样本场景、上下文词软扣分、技术场景权重。
- `test_chat_ab_gold_score.py` 覆盖跨 case 重复输出 hard fail。
- `test_galgame_text_quality.py` 保留 contract/taste/inference 可观测性，但断言迁到 soft penalty。
- `test_chat_kernel_v2.py` 更新为多候选、soft select、非 fallback-first 的运行链路。

应删除或重写的旧测试：

- `test_text_quality_gate_rejects_daily_interaction_bad_patterns` 目前 `xfail`，因为它固化了“坏句表 hard reject”的旧方法论。后续应拆成样本银行 bad reference 与维度扣分测试。
- 旧的 “fallback replaces low-score candidate” 类断言不再成立，应改成“候选 rerank + 分数解释”。
- 旧的 “semantic reranker blocks daily scene into no_reply” 类断言不再成立，轻日常语义问题应优先在候选池内 rerank，而不是阻断发送。
