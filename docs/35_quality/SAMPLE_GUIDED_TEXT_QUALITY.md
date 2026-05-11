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

### Bland Pass / Charm Floor

`BLAND_PASS_LINE` 是 soft penalty，不是 hard gate。它用于拉低“可以发但没有角色反应”的候选，例如短确认场景里的 `嗯嗯，你说了算。`、`听你的。`、`那就这样呗。`

处理原则：

- 不全局禁用 `嗯嗯`、`你说了算`、`听你的` 这类词。
- 只在日常/短确认等容易滑平的场景里扣 `catchiness`、`character_freshness`、`context_fit` 和 `line_rhythm`。
- `接着说`、`听着呢`、`多说几个字`、`说点好听的`、`就这一个...` 这类有推进或轻吐槽的短句获得 `short_ack_charm` 加成。
- rerank 应让 bland pass 输给有推进、有反应、有一点角色味的候选。

### Cluster Soft Penalties

近期 100 条日常 goal live retry 暴露的坏味道按 cluster 收束为 soft penalty：

- `CHEAP_WAVE_PUNCTUATION`：`~` / `～` 不再算“有纹理”，会压低 `catchiness`、`character_freshness`、`line_rhythm` 和 `naturalness`；生成提示也要求角色感不要靠波浪号。
- `WHAT_DOING_ATTENTION_WAIT`：只在 `what_doing` 场景扣“没干嘛，就等你找我聊天”这类注意力债务，不把 `找我`、`等你` 全局 hard ban。
- `PRESENCE_ATTENTION_DEBT`：只在 `presence` 场景扣“等你半天/终于来找我”，让 `在吗` 回到“我在/看见了”。
- `MEAL_SHARE_SCOLDING_OR_PUSHY`：只在 `meal_share` 场景扣“谁让你吃太急/活该/揉肚子”这类训人或越界照顾。
- `QUALITY_SOFT_TRAILING_INCOMPLETE_QUESTION`：尾巴像问题但没收句的表层完整性问题会从 weak pass 压到 retry。
- 写作/技术场景的 usefulness 锚点包含 `内容`、`要求`、`题目`、`丢来`，避免把“我来写，内容给我”误判为低技术帮助。

## Sample Bank

`PositiveStyleSample` 新增样本字段：

- `good_examples`
- `bad_examples`
- `quality_priorities`

未显式填写时，评分器复用既有 `safe_fragments` 作为 good reference，复用 `avoid` 作为 bad reference。样本用于 rerank 参考，不作为最终回复模板。

## Runtime Chain

当前主链是隔离表层文本链，不再以 `ChatKernelV2` 作为文本质量验收对象：

1. `build_isolated_surface_messages()` 只接收 `Soul.md`、当前用户输入、`ReplyControlPacket`、必要事实和 `must_not_do`。
2. `build_style_reference_query()` 以 `TurnSurfaceContract.scene_id` 为准，而不是只看上游 `task_mode`，例如 `task_mode=happy` 但用户说“终于跑通了”时切到 `good_news`。
3. 模型一次输出 3 到 5 个候选，真实生成层不能读取原始历史、原始记忆、器官族原文、工具候选或未压缩 RAG。
4. `parse_surface_candidates()` 最多解析 5 个候选。
5. 每个候选进入 `score_isolated_surface_candidates()`，内部执行 minimal hard gate 与样本引导 soft scorer。
6. `select_best_isolated_candidate()` 按 passed、hard gate、control violations、总分和长度 rerank。
7. failure-only retry 只重跑失败或弱通过 case；merge 时必须证明 retry 候选在通过状态、hard gate、control violations 或分数上更好。
8. 最终只做 minimal repair，不用整段重写或 canned fallback 伪装质量提升。

## Current Batch Baseline

日常 goal 已从 100 条 seed 扩充到 500 条稳定 case：

- builder: `tools/build_isolated_surface_daily_goal_cases.py`
- generated cases: `artifacts/isolated_surface_harness/daily_goal_500_20260511/cases.jsonl`
- prompt build: `500/500`，errors `0`
- raw live: `483/500` pass，`17` low score，`1` hard fail，avg `86.628`
- failure-only retry: `40` cases / `80` attempts
- merged result: `500/500` pass，`0` low score，`0` hard fail，avg `87.348`
- weak pass remaining: `5`

本轮继续按 cluster 优化，没有把单句变成 hard regex，也没有扩大 scene-specific hard forbidden surface。新增/强化的是 soft cluster：`ADVICE_VAGUE_PUSH`、`GOOD_NEWS_PROBING`、`SHORT_ACK_OBEDIENCE`，以及 `identity` 的 name-first contract。

## Eval Suite Expansion

500 条 daily goal 现在作为 regression suite 保存，新增泛化评估不再继续从同一批 seed 无限复制：

- builder: `tools/build_isolated_surface_eval_suites.py`
- held-out suite: `artifacts/isolated_surface_harness/daily_goal_eval_1000_20260511/heldout_200/cases.jsonl`
- challenge suite: `artifacts/isolated_surface_harness/daily_goal_eval_1000_20260511/challenge_300/cases.jsonl`
- combined new suite: `artifacts/isolated_surface_harness/daily_goal_eval_1000_20260511/new_500/cases.jsonl`
- total eval if combined with regression: `1000`

用途边界：

- `regression_500`：防止已修好的文本质量退化，要求稳定全绿。
- `heldout_200`：观察最简文本链是否能外推到未调过的日常输入。
- `challenge_300`：专门压易漂移场景，用于发现 cluster，不用于按单个 case 补 regex。

最新 live 结果：

- raw new_500: `485/500` pass，`15` low score，`0` hard fail，avg `87.135`
- raw heldout: `192/200` pass，avg `86.665`
- raw challenge: `293/300` pass，avg `87.449`
- first retry merged: `499/500` pass，`1` low score，`0` hard fail，avg `87.515`
- `good_news_proud` follow-up retry: `500/500` pass，`0` low score，`0` hard fail，avg `87.545`

`good_news_proud` 现在是独立小场景：用户既表达进展，又在等一句夸时，contract 要求候选同时落到“进展词”和“认可/拌嘴词”。这次没有把坏句写成专用 regex，而是新增 sample bank、scene grounding 维度和 style query 映射；`收尾/结束/完成/弄成/做成` 也作为泛化进展词进入 good_news。

## Continuity Step

文本质量通过后，连续对话的第一步仍然走隔离表层链，而不是回到旧 runtime 主链或 `ChatKernelV2`。新增 continuity harness 的原则：

- 多轮 case 只是一组 turn；每个 turn 进入最终生成层时仍被降成 `Soul.md + 当前用户输入 + ReplyControlPacket + facts + must_not_do`。
- 上一轮只能以压缩事实进入，例如“上一轮摘要：尼尼说这版终于跑通了，回复已接住进展。”
- 原始历史、原始助手回复、长期记忆、器官族输出、工具候选、RAG 原文和自然语言建议仍然禁止进入 prompt。
- continuity case 里的 `candidates` 只用于离线 scorer/rerank，不属于生成输入。

当前 smoke 覆盖：好消息后追夸、建议后要下一步、坏心情承接、在场到忙完回来、嫌烦后的收声修复、身份问答后的追问。

## Test Migration

保留并改写的测试：

- `test_sample_guided_text_scorer.py` 覆盖维度、样本场景、上下文词软扣分、技术场景权重。
- `test_chat_ab_gold_score.py` 覆盖跨 case 重复输出 hard fail。
- `test_galgame_text_quality.py` 保留 contract/taste/inference 可观测性，但断言迁到 soft penalty。
- `test_isolated_surface_harness.py` 覆盖输入边界、多候选解析、候选评分和最终选择。
- `test_retry_isolated_surface_failures.py` 覆盖失败/弱通过重试和 source/retry merge 规则。
- `test_chat_kernel_v2.py` 仅保留旧链路兼容性测试，不作为当前主链文本质量验收入口。

应删除或重写的旧测试：

- `test_text_quality_gate_rejects_daily_interaction_bad_patterns` 目前 `xfail`，因为它固化了“坏句表 hard reject”的旧方法论。后续应拆成样本银行 bad reference 与维度扣分测试。
- 旧的 “fallback replaces low-score candidate” 类断言不再成立，应改成“候选 rerank + 分数解释”。
- 旧的 “semantic reranker blocks daily scene into no_reply” 类断言不再成立，轻日常语义问题应优先在候选池内 rerank，而不是阻断发送。
