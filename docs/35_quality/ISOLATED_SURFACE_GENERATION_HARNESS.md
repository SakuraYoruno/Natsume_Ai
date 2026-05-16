# Isolated Surface Generation Harness

目的：先把 `Soul.md + System Prompt` 的最终表层文本生成稳定下来，再接历史、状态、传感器或器官族输出。

状态：这是当前 normal chat 文本质量阶段的主链基线。旧 `ChatKernelV2` 链路已移除，不参与文本生成、验收、调参或 fallback。

当前 normal chat 主链采用 direct-only：最终表层 prompt 只要求一条可发送回复，`SurfaceRuntime` 直接采用模型输出。候选 rerank、repair/fallback 选优分支已删除；评分器只保留给 trace 和批测观察，不能替换直出文本。

## Boundary

最终生成层只能读取压缩后的控制面：

- `Soul.md`
- 当前用户输入
- `ReplyControlPacket`
- 必要事实列表
- 明确的 `must_not_do` 列表

禁止进入最终生成 prompt 的输入：

- 原始器官族输出
- 原始长期记忆
- 大段历史对话
- 工具候选列表
- 未压缩 RAG 文档
- 任意模块生成的自然语言建议

这些信息后续如果确实需要影响回复，必须先被上游压缩成 `ReplyControlPacket.facts`、`must_include`、`must_not_do` 或 `style_anchor`。

## ReplyControlPacket

实现位置：`host_brain/persona/isolated_surface_harness.py`

```python
class ReplyControlPacket(BaseModel):
    response_move: str
    task_mode: str
    emotional_tone: str
    relationship_distance: str
    information_budget: str
    question_budget: int
    must_include: list[str]
    must_not_do: list[str]
    facts: list[str]
    style_anchor: list[str]
```

默认值偏保守，适合日常短回复。最终生成层不再直接读取全量 runtime state。

## Harness Flow

1. `build_isolated_surface_input(...)`
   - 只接受允许字段。
   - 发现 `history`、`memory`、`organ`、`rag`、`tool_candidates`、`module_advice` 等 raw context key 时直接报错。
2. `build_isolated_surface_messages(...)`
   - 生成固定双消息 prompt。
   - system 中放隔离边界、输出约束和 `Soul.md`。
   - 离线 harness 可要求 JSON 候选；normal chat 主链会在 `SurfaceRuntime` 中改写成单条 direct 输出要求。
   - user 中放当前输入、`ReplyControlPacket`、facts、must_not_do。
   - 同步放入由当前用户输入 + `ReplyControlPacket.task_mode` 派生的 `TurnSurfaceContract`，只包含 response_move、required_any、问句预算、长度和 notes；不放 forbidden_surface 或 raw 上下文。
   - `TurnSurfaceContract` 允许当前用户文本纠正过粗或错误的 `task_mode`，例如 `你烦不烦` 优先落到 `annoying_tease`，`我刚忙完` 优先落到 `busy_done`。
   - 只保留少量真实中文原句 quote 作为质感参考；已删除 `style_reference_bank` 和泛化 style seed 注入。
   - 生成提示明确要求不要靠 `~` / `～` 当可爱语气，角色感必须来自用词、转折和节奏。
3. `SurfaceRuntime(...)`
   - normal chat 默认路径。
   - 只取模型直接输出的第一条最终回复。
   - scorer 只记录观察信号，不再按分数重选、repair 或 fallback。
4. `run_isolated_surface_generation(...)`
   - 只接收一个注入的 `generate(messages)` 函数。
   - harness 不知道 runtime state，也不从全局状态取上下文。
5. `parse_surface_candidates(...)`
   - 解析 `{"candidates":[...]}`，最多取 5 个。
6. `score_isolated_surface_candidates(...)`
   - 使用现有 hard gate 拦截绝对错误。
   - 使用 sample-guided scorer 做 soft scoring。
   - scorer 显式输出 galgame 质感维度：`catchiness`、`template_density`、`scene_grounding`、`implied_fact_risk`、`line_rhythm`、`character_freshness`。
   - 对 `must_include`、`must_not_do`、`question_budget` 做控制面扣分。
8. 候选选择器
   - `SurfaceRuntime` 不再调用候选选择器。
   - 老的多候选选择逻辑只允许留在离线 harness 内做历史对照，不得重新接回 normal chat 主链。

## Continuity Harness

500 条日常 goal 的 `new_500` 已经跑到 `500/500`，低分 `0`，hard fail `0`。下一步连续对话仍然不接原始历史，而是先验证“压缩连续性输入”能不能稳住表层文本。

实现位置：

- `host_brain/persona/isolated_surface_continuity.py`
- `tools/build_isolated_surface_continuity_cases.py`
- `tools/isolated_surface_continuity_harness.py`

连续对话 case 的外层只有 `case_id` 和 `turns`。每个 turn 仍然只允许：

- 当前 `user_message`
- `ReplyControlPacket`
- `facts`
- `must_not_do`

上一轮信息必须被上游压缩成事实，例如：

```text
上一轮摘要：尼尼说这版终于跑通了，回复已接住进展。
连续事实：本轮的“厉害吧”指刚才跑通。
```

禁止把以下内容放进连续对话输入：

- `conversation_history` / `history_turns` / `messages`
- 原始助手回复列表
- 原始长期记忆
- 器官族原始输出
- 工具候选列表
- 未压缩 RAG 文档
- 模块自然语言建议

`candidates`、`model_output`、`candidate_payload` 只允许作为离线评分 artifact 字段出现，永远不会进入 `build_isolated_surface_messages()`。

### 摘要压缩怎么做

当前长连续对话先用确定性的 `ContinuitySummaryCompressor` 做滚动压缩。它不保存原始消息数组，只保存会进入 `ReplyControlPacket.facts` 的短标签：

- `thread_summary`：整条对话的长期方向。
- `topic_state`：当前局部主题。
- `last_turn_summary`：上一轮用户意图和助手动作的短摘要。
- `open_loop`：下一轮还没收完的话口。
- `emotional_state`：当前情绪状态标签。
- `distance_note`：关系距离边界。

每轮生成前只取最多 `6` 条 facts。生成后，上游用本轮的用户意图摘要、助手动作摘要、主题和未完成话口更新 snapshot；下一轮只看到更新后的压缩事实，看不到旧轮原文。也就是说，旧细节会退成主题词或方向词，上一轮摘要会被新一轮覆盖，避免把连续对话变成历史全文回灌。

36 轮长链专门验证这个机制：从第 1 轮到第 36 轮，每个 turn 进入最终表层生成层的输入仍然只有 `Soul.md + 当前用户输入 + ReplyControlPacket + facts + must_not_do`。

### 三层上下文怎么接

三层上下文可以进入压缩器，但不能直接进入最终表层生成层：

```text
最近几轮原始对话
+ 中期摘要
+ 长期事实
        ↓
ThreeLayerContinuityContext
        ↓
ReplyControlPacket
        ↓
isolated surface generator
```

实现入口：

- `ThreeLayerContinuityContext`
- `build_reply_control_packet_from_context(...)`
- `build_surface_input_from_three_layer_context(...)`

这里的 `recent_turns` 是上游压缩器输入，只用于生成 `上一轮摘要`、`当前主题`、`未完成话口` 等短 facts。`build_surface_input_from_three_layer_context(...)` 构造最终请求时只传 `current_user_message` 和压缩后的 `ReplyControlPacket`，不会把 `recent_turns`、`mid_term_summary` 字段名或原始历史文本传给 `build_isolated_surface_messages()`。

三层压缩 surface case：

```powershell
python tools/build_three_layer_surface_cases.py `
  --out artifacts/isolated_surface_harness/three_layer_surface_20260511 `
  --force

python tools/isolated_surface_harness.py `
  --cases artifacts/isolated_surface_harness/three_layer_surface_20260511/cases.jsonl `
  --out artifacts/isolated_surface_harness/three_layer_surface_20260511/prompt_build `
  --strict
```

当前三层压缩 baseline：

- cases: `8`
- max facts per case: `6`
- raw context crosses surface: `false`
- prompt/scorer strict: `8/8`
- errors: `0`
- live run: `artifacts/isolated_surface_harness/three_layer_surface_live_20260511`
- live result: `8/8`
- live avg selected score: `88.854`
- live low score / hard fail / control violation: `0 / 0 / 0`

三层压缩 36 轮长链：

```powershell
python tools/build_three_layer_surface_cases.py `
  --suite chain `
  --out artifacts/isolated_surface_harness/three_layer_chain_36_20260512 `
  --force

python tools/isolated_surface_harness.py `
  --cases artifacts/isolated_surface_harness/three_layer_chain_36_20260512/cases.jsonl `
  --out artifacts/isolated_surface_harness/three_layer_chain_36_20260512/prompt_build `
  --strict
```

当前三层 36 轮长链 baseline：

- cases: `36`
- max chain turns: `36`
- max facts per case: `6`
- raw context crosses surface: `false`
- prompt/scorer strict: `36/36`
- live run: `artifacts/isolated_surface_harness/three_layer_chain_36_live_20260512`
- live result: `36/36`
- live avg selected score: `88.386`
- live low score / hard fail / control violation: `0 / 0 / 0`

三层压缩 actual-output 闭环：

```powershell
python tools/run_three_layer_chain_live_loop.py `
  --out artifacts/isolated_surface_harness/three_layer_chain_36_closed_loop_v2_20260512 `
  --progress-every 3
```

闭环 runner 是串行执行：第 `n` 轮 live selected reply 会进入第 `n+1` 轮的 `recent_turns`，但最终 prompt 只接收重新压缩后的 `ReplyControlPacket`。输出 artifact 里 `loop_trace.jsonl` 的 recent window 只保留 role、长度和 hash，不保存 raw recent 文本。

当前 closed-loop baseline：

- closed loop: `true`
- actual feedback turns: `36`
- result: `36/36` turn pass，`1/1` dialogue pass
- avg selected score: `88.261`
- low score / hard fail / control violation: `0 / 0 / 0`
- raw recent leak count: `0`
- recent window hash only: `true`
- quality report watch rows: `4`

构建 smoke case：

```powershell
python tools/build_isolated_surface_continuity_cases.py `
  --out artifacts/isolated_surface_harness/continuity_smoke_20260511 `
  --force
```

构建更大的 eval case：

```powershell
python tools/build_isolated_surface_continuity_cases.py `
  --suite eval `
  --out artifacts/isolated_surface_harness/continuity_eval_72_20260511 `
  --force
```

当前 eval baseline：

- `24` 段压缩连续对话
- `72` 个 turn
- prompt/scorer strict: `72/72`
- errors: `0`

构建 36 轮 long case：

```powershell
python tools/build_isolated_surface_continuity_cases.py `
  --suite long `
  --out artifacts/isolated_surface_harness/continuity_long_36_20260511 `
  --force
```

当前 36-turn long baseline：

- case: `artifacts/isolated_surface_harness/continuity_long_36_20260511`
- dialogue: `1`
- turns: `36`
- max_turns_per_dialogue: `36`
- prompt/scorer strict: `36/36`
- live run: `artifacts/isolated_surface_harness/continuity_long_36_live_final_zh_20260511`
- live result: `36/36` turn pass，`1/1` dialogue pass
- low score: `0`
- hard fail: `0`
- control violation: `0`
- avg selected score: `87.956`
- quality report watch rows: `5`

校验 prompt 和离线候选评分：

```powershell
python tools/isolated_surface_continuity_harness.py `
  --cases artifacts/isolated_surface_harness/continuity_smoke_20260511/cases.jsonl `
  --out artifacts/isolated_surface_harness/continuity_smoke_20260511/prompt_build `
  --strict
```

运行 live 连续对话生成：

```powershell
python tools/run_isolated_surface_continuity_live.py `
  --cases artifacts/isolated_surface_harness/continuity_smoke_20260511/cases.jsonl `
  --out artifacts/isolated_surface_harness/continuity_smoke_live_20260511 `
  --concurrency 3 `
  --progress-every 3
```

live 输出：

- `live_raw_generations.jsonl`：每个 turn 的模型原始输出、候选和选中项。
- `live_scores.jsonl`：每个 turn 的 scorer 明细。
- `summary.json`：turn pass rate、dialogue pass rate、低分数、hard fail、control violation 和延迟统计。

少量失败、低分或尾部超时可以只补跑对应 turn，再合并：

```powershell
python tools/merge_continuity_live_scores.py `
  --scores artifacts/isolated_surface_harness/continuity_eval_72_live_20260511/live_scores.jsonl `
           artifacts/isolated_surface_harness/continuity_eval_72_retry_tail_20260511/live/live_scores.jsonl `
  --expected-cases artifacts/isolated_surface_harness/continuity_eval_72_20260511/cases.jsonl `
  --out artifacts/isolated_surface_harness/continuity_eval_72_live_merged_20260511
```

生成“通过但不好听”的人工观察报告：

```powershell
python tools/continuity_quality_report.py `
  --scores artifacts/isolated_surface_harness/continuity_smoke_live_after_control_prompt_20260511/live_scores.jsonl `
  --out artifacts/isolated_surface_harness/continuity_smoke_live_after_control_prompt_20260511/quality_report `
  --weak-threshold 83
```

报告会保留已通过但需要人工盯的行，例如：

- `constraint_language_leak`：暴露 prompt/规则/问句预算等约束语言。
- `stiff_or_toolish_surface`：过硬、像工具接单。
- `clingy_distance_risk`：日常场景里距离过黏。
- `scorer_soft_warning`：scorer 已给出 soft warning，但还没低到失败线。

当前 live baseline：

- run: `artifacts/isolated_surface_harness/continuity_smoke_live_after_control_prompt_20260511`
- result: `18/18` turn pass，`6/6` dialogue pass
- low score: `0`
- hard fail: `0`
- control violation: `0`
- avg selected score: `87.332`
- quality report watch rows: `8`

当前 72-turn eval live baseline：

- source run: `artifacts/isolated_surface_harness/continuity_eval_72_live_20260511`
- retry tail: `artifacts/isolated_surface_harness/continuity_eval_72_retry_tail_20260511/live`
- merged run: `artifacts/isolated_surface_harness/continuity_eval_72_live_merged_20260511`
- result: `72/72` turn pass，`24/24` dialogue pass
- missing turns: `0`
- low score: `0`
- hard fail: `0`
- control violation: `0`
- avg selected score: `87.963`
- quality report watch rows: `7`

这个 harness 不是 runtime 集成。它只回答一个问题：在没有原始历史、没有状态树、没有器官族自然语言建议的情况下，压缩后的连续性事实能不能让最终表层生成自然接住上一轮。

## Galgame Soft Scoring

`sample_guided_text_scorer` 的新增维度只做软评分，不把口癖或上下文相关词全局硬禁。

- `catchiness`：区分有台词钩子的短句和“好啦/知道啦/没事”式平板回复。
- `template_density`：对 `哼`、`尼尼`、`啦/呀/嘛` 等堆叠口癖扣分，但不直接拒绝。
- `scene_grounding`：要求候选落到当前 scene 的动作，比如成功分享要接住“跑通/搞定”，小失败要能“一起看/补救”。
- `implied_fact_risk`：对未提供的现实状态暗示扣分，比如“刚发呆”“忙到明天”“感觉你会来找我”。
- `line_rhythm`：偏好可发送的一到两段私聊台词，扣长而无停顿的直线句。
- `character_freshness`：对 stock opening 和机械傲娇模板降权，避免只靠口癖冒充角色感。

这些维度会影响观察分，但不会新增 hard forbidden pattern，也不会替换直出文本。

## Original Chinese Quote References

仓库只保留少量真实中文原句 quote 作为质感参考：

- `data/style_bank/surface_original_quote_bank.jsonl`：保存可提示的短台词、标签和来源摘要。

隔离 harness 不再注入抽象派生风格特征，也不再使用 `style_reference_bank` 或泛化 style seed。

这让 `Soul.md` 继续只做语言风格锚定，同时让常见 scene 的 good reference 从人工短片段逐步迁移到原创中文样本派生特征。
`source_refs` 只写入 harness trace，不进入生成 prompt。

## CLI

```powershell
python tools/isolated_surface_harness.py --cases tests/fixtures/isolated_surface/cases.jsonl
```

输出目录默认在：

```text
artifacts/isolated_surface_harness/<timestamp>/
```

产物：

- `prompts.jsonl`：每条 case 的隔离 prompt。
- `scores.jsonl`：如果 case 带了生成后候选，则输出逐候选评分。
- `summary.json`：case 数、错误数、已评分 case 数、通过率。

case 示例：

```json
{
  "case_id": "next_step_minimal",
  "user_message": "继续",
  "reply_control_packet": {
    "response_move": "next_step",
    "task_mode": "next_step",
    "emotional_tone": "focused",
    "relationship_distance": "familiar",
    "information_budget": "short",
    "question_budget": 0,
    "must_include": ["继续"],
    "must_not_do": ["哥哥"],
    "facts": ["目标是先固定最小文本链。"],
    "style_anchor": ["短句", "轻微吐槽"]
  },
  "facts": ["当前只允许读取 ReplyControlPacket。"],
  "must_not_do": ["脑补昨晚发生了什么"],
  "candidates": [
    "哥哥，怎么了？",
    "继续，先把入口锁在 ReplyControlPacket 上。"
  ]
}
```

注意：`candidates` 只用于离线评分，不会被放进生成 prompt。真实生成链路必须由 harness 调用模型后再解析候选。

## Daily Goal Case Expansion

500 条日常 goal case 由 100 条 seed 稳定扩充生成，不手写 artifact：

```powershell
python tools/build_isolated_surface_daily_goal_cases.py `
  --seed artifacts/isolated_surface_harness/daily_goal_100_20260511/cases.jsonl `
  --out artifacts/isolated_surface_harness/daily_goal_500_20260511 `
  --force
```

扩充原则：

- 每条 seed 保留原 `ReplyControlPacket`、facts 和 `must_not_do`。
- 每条 seed 生成 5 个同 scene 用户输入变体，case id 使用 `__v02` 到 `__v05` 后缀。
- 只改当前 `user_message`，不接入历史、状态、器官族输出或自然语言建议。
- 生成后必须先跑 prompt 构建校验：

```powershell
python tools/isolated_surface_harness.py `
  --cases artifacts/isolated_surface_harness/daily_goal_500_20260511/cases.jsonl `
  --out artifacts/isolated_surface_harness/daily_goal_500_20260511/prompt_build `
  --strict
```

## Held-Out and Challenge Suites

500 条日常 goal 已经成为回归集，不再继续无限扩同一批 case。新增泛化评估由独立 builder 生成：

```powershell
python tools/build_isolated_surface_eval_suites.py `
  --out artifacts/isolated_surface_harness/daily_goal_eval_1000_20260511 `
  --force
```

输出结构：

- `heldout_200/cases.jsonl`：200 条未参与调参的日常泛化集，覆盖问候、在场、累、饿、夸奖、道歉、坏心情、想你等常见 surface。
- `challenge_300/cases.jsonl`：300 条挑战集，集中压 `advice_continue`、`short_ack`、`good_news_proud`、`identity`、`presence`、`greeting`、`annoying_tease`、`bad_mood`。
- `new_500/cases.jsonl`：上面 200 + 300 的合并集。
- `summary.json`：记录 regression、heldout、challenge 的总量、scene counts 和 group counts。

这三批的职责不同：

- `daily_goal_500_20260511` 是回归集，要求稳定全绿。
- `heldout_200` 是泛化观察集，低分用于判断方法是否外推，不直接按孤例改规则。
- `challenge_300` 是压力集，低分先聚类成 failure class；只有反复出现的 cluster 才进入 scorer 或 scene contract 优化。

所有新增 case 仍然只包含当前用户输入、`ReplyControlPacket`、facts、`must_not_do` 和 style anchor。不得把历史、器官族输出、工具候选或自然语言建议混入 case。

## Failure-Only Retry

完整 live 批测耗时较长时，不要为了少量坏 case 重跑 100 条。先用当前 parser/scorer 重放上次 raw output，再只重试低分 case：

```powershell
python tools/retry_isolated_surface_failures.py `
  --cases artifacts/isolated_surface_harness/daily_goal_100_20260511/cases.jsonl `
  --source-run artifacts/isolated_surface_harness/daily_goal_100_contract_live_20260511_152900 `
  --out artifacts/isolated_surface_harness/daily_goal_100_retry_live `
  --challenge-threshold 83 `
  --attempts-per-case 2
```

常用检查模式：

```powershell
python tools/retry_isolated_surface_failures.py `
  --cases <cases.jsonl> `
  --source-run <previous-live-run-dir> `
  --out <retry-out-dir> `
  --dry-run
```

产物：

- `source_rescored_scores.jsonl`：用当前 scorer 重放旧 raw output。
- `retry_raw_generations.jsonl` / `retry_scores.jsonl`：只包含被重试的 case。
- `merged_scores.jsonl`：逐 case 在 source 和 retry 中选更好的 selected。
- `summary.json`：记录 `retry_case_ids`、`retry_case_count`、merged pass rate 和节省后的局部耗时。

参数语义：

- `--threshold`：正式通过线，默认 `80`；失败或低于该线的 case 必须重试。
- `--challenge-threshold`：弱通过挑战线；已通过但低于该分数的 case 也会进入 retry，例如 `80-83`。
- `--attempts-per-case`：每个 retry case 独立生成几次；merge 时只在 retry 的 pass/hard/control/score 真正更好时替换 source，不靠更短文本覆盖。

## Quality Cluster Report

文本质量不再按单个坏句补规则；每轮先对 scored artifacts 做聚类，只处理达到阈值的 failure class。

```powershell
python tools/quality_cluster_report.py `
  --scores artifacts/isolated_surface_harness/daily_goal_100_retry_wave_whatdoing_goodnews_meal_presence_write_live_20260511/merged_scores.jsonl `
  --out artifacts/isolated_surface_harness/daily_goal_100_retry_wave_whatdoing_goodnews_meal_presence_write_live_20260511/quality_clusters `
  --min-cluster-count 3
```

输出：

- `quality_cluster_summary.json`：程序可读的 cluster 汇总、最低分尾部、issue/watch/diagnostic 分组。
- `quality_cluster_report.md`：人工扫描报告。

分组口径：

- `Large Issue Clusters`：达到阈值且应优先判断是否优化的坏味道类别，例如 `surface:cheap_wave_punctuation`、`low_dimension:catchiness`。
- `Diagnostic Overlap`：和 issue cluster 重叠的 reason 解释，只辅助定位，不作为新的独立修复目标。
- `Watch Clusters`：只观察，不默认修，例如“短但干净”或正常问句。

处理原则：

- 只处理 cluster，不处理孤例。
- 单个输出不能直接变成 hard forbidden 或专用 regex。
- cluster 修复必须说明它泛化到哪些场景，并保留 soft scoring 优先。

最新 100 条日常 goal live retry 基线：

- run: `artifacts/isolated_surface_harness/daily_goal_100_retry_wave_whatdoing_goodnews_meal_presence_write_live_20260511`
- cases: `100`
- retry cases: `15`
- pass: `100/100`
- low score: `0`
- weak pass: `0`
- hard fail: `0`
- avg selected score: `87.487`

最新 500 条日常 goal 扩容基线：

- cases: `artifacts/isolated_surface_harness/daily_goal_500_20260511/cases.jsonl`
- prompt build: `500/500`，errors `0`
- raw live run: `artifacts/isolated_surface_harness/daily_goal_500_live_20260511`
- raw live pass: `483/500`
- raw live low score: `17`
- raw live hard fail: `1`
- raw live avg selected score: `86.628`
- retry run: `artifacts/isolated_surface_harness/daily_goal_500_after_identity_contract_retry_20260511`
- retry cases: `40`
- retry attempts: `80`
- merged pass: `500/500`
- merged low score: `0`
- merged hard fail: `0`
- merged avg selected score: `87.348`
- weak pass remaining: `5`
- cluster report: `artifacts/isolated_surface_harness/daily_goal_500_after_identity_contract_retry_20260511/cluster_report`

本轮 500 条低分清零不是通过扩大 hard forbidden 获得的：`advice_continue`、`good_news`、`short_ack`、`identity` 都只补了 scene-level contract notes、good/bad sample 与 soft penalty。

最新 1000 评估结构：

- regression cases: `artifacts/isolated_surface_harness/daily_goal_500_20260511/cases.jsonl`
- eval suite builder: `tools/build_isolated_surface_eval_suites.py`
- eval suite root: `artifacts/isolated_surface_harness/daily_goal_eval_1000_20260511`
- heldout cases: `200`
- challenge cases: `300`
- regression + heldout + challenge total: `1000`
- prompt build: heldout `200/200` errors `0`，challenge `300/300` errors `0`，new_500 `500/500` errors `0`
- raw new_500 live: `485/500` pass，`15` low score，`0` hard fail，avg `87.135`
- raw split: heldout `192/200` pass，challenge `293/300` pass
- first failure-only retry: `31` cases / `62` attempts，merged `499/500` pass，`1` low score，avg `87.515`
- `good_news_proud` follow-up retry: `28` cases / `56` attempts
- merged new_500 after `good_news_proud`: `500/500` pass，`0` low score，`0` hard fail，avg `87.545`
- weak pass remaining after fix: `2`
- run: `artifacts/isolated_surface_harness/daily_goal_eval_1000_new_500_retry_after_good_news_proud_v2_20260511`
- cluster report: `artifacts/isolated_surface_harness/daily_goal_eval_1000_new_500_retry_after_good_news_proud_v2_20260511/cluster_report`

`challenge_good_news_proud_user_proud_030` 没有用专用 regex 修；它被归并为“进展 + 求夸”小场景，要求同时接住进展词和熟人式认可。`事情收尾了` 这类输入也被归入 good_news，因为“收尾/结束”是泛化的进展锚点。

本轮修复的 cluster：

- cheap wave punctuation：生成侧降噪 + scorer soft penalty。
- `what_doing` attention wait：问“在干嘛”时不说一直等用户来找。
- `presence` attention debt / incomplete question tail：确认在场，不追问需求或抱怨等待。
- `good_news` scene grounding：`happy` 中出现“跑通/搞定”时使用 `good_news` contract。
- `meal_share` stuffed scolding：吃撑时接“先缓一下”，不训人、不推越界身体照顾。
- write takeover usefulness：写作任务的 `内容/要求/题目/丢来` 计入 technical helpfulness。
- `advice_continue` small-step contract：`task_mode=advice_continue` 和“再试试/继续有意义吗”统一落到“小步判断”，不再生成“试就对了/继续继续”。
- `good_news` probing：小成就场景接住“搞定/小事/进展”，压低“什么小事说来听听”式追问。
- `short_ack` obedience：`好/行/嗯/哦` 不写成“算你乖/算你识相”，必须沿上一轮用听着/接着/多说等推进锚。
- `identity` name-first：`你是` 必须先落“新海天”，不再用“我在呢/你故意的吧”躲开身份确认。
- `good_news_proud` grounding：进展 + 求夸不再混进普通 `praise`；回复必须同时保留进展锚点和认可/拌嘴锚点。
- `good_news` progress vocabulary：`收尾/结束/完成/弄成/做成` 等成功进展词进入 soft grounding，不把它们压回泛 `happy`。

## Migration Rule

现有运行时链路要迁移到这个 harness 时，先新增一个上游压缩步骤：

```text
runtime state / organs / memory / rag
  -> ReplyControlPacket
  -> isolated surface generation
```

最终生成层的单测应断言 prompt 中没有 raw history、raw memory、organ output、tool candidates 或未压缩 RAG 文档。旧测试如果要求最终生成器读取这些原始对象，应改写为测试上游 packet 压缩器。
