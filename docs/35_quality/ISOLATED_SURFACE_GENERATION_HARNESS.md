# Isolated Surface Generation Harness

目的：先把 `Soul.md + System Prompt` 的最终表层文本生成稳定下来，再接历史、状态、传感器或器官族输出。

状态：这是当前 normal chat 文本质量阶段的主链基线。旧 `ChatKernelV2` 链路只作为历史参考，不参与本阶段文本质量验收和调参。

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
2. `derive_style_reference_anchor(...)`
   - 从 `data/style_bank/sora_style_bank.jsonl` 选择原作台词派生风格特征。
   - 只注入 `intent_tags`、`emotion_tags`、`style_moves`、`rhythm_tags`、`lexical_cues`、`source_refs` 等派生特征。
   - 不把原作 raw 台词、解包文本或自然语言建议放入 prompt。
3. `build_isolated_surface_messages(...)`
   - 生成固定双消息 prompt。
   - system 中放隔离边界、输出 JSON 要求和 `Soul.md`。
   - user 中放当前输入、增强后的 `ReplyControlPacket`、facts、must_not_do。
   - `ReplyControlPacket.style_anchor` 会自动合并原作派生风格锚点。
   - 同步放入由当前用户输入 + `ReplyControlPacket.task_mode` 派生的 `TurnSurfaceContract`，只包含 response_move、required_any、问句预算、长度和 notes；不放 forbidden_surface 或 raw 上下文。
   - `TurnSurfaceContract` 允许当前用户文本纠正过粗或错误的 `task_mode`，例如 `你烦不烦` 优先落到 `annoying_tease`，`我刚忙完` 优先落到 `busy_done`。
   - style reference query 以 `TurnSurfaceContract.scene_id` 为准，避免上游粗粒度 `task_mode` 把 `good_news`、`write_takeover` 等场景压回泛 daily/happy。
   - 生成提示明确要求不要靠 `~` / `～` 当可爱语气，角色感必须来自用词、转折和节奏。
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
7. `select_best_isolated_candidate(...)`
   - 在 hard gate 通过后，优先选无控制面违约且分数最高的候选。

## Galgame Soft Scoring

`sample_guided_text_scorer` 的新增维度只做软评分，不把口癖或上下文相关词全局硬禁。

- `catchiness`：区分有台词钩子的短句和“好啦/知道啦/没事”式平板回复。
- `template_density`：对 `哼`、`尼尼`、`啦/呀/嘛` 等堆叠口癖扣分，但不直接拒绝。
- `scene_grounding`：要求候选落到当前 scene 的动作，比如成功分享要接住“跑通/搞定”，小失败要能“一起看/补救”。
- `implied_fact_risk`：对未提供的现实状态暗示扣分，比如“刚发呆”“忙到明天”“感觉你会来找我”。
- `line_rhythm`：偏好可发送的一到两段私聊台词，扣长而无停顿的直线句。
- `character_freshness`：对 stock opening 和机械傲娇模板降权，避免只靠口癖冒充角色感。

这些维度会影响 rerank 和通过分，但不会新增 hard forbidden pattern。

## Original-Derived Style Anchors

仓库已有解包台词候选与派生风格库：

- 中文优先：`C:\Natsume_Work\vn_text\skycity_chs_strings_20260502_230811\sora_chinese_daily_candidates.jsonl`、`C:\Natsume_Work\vn_text\newep_chs_strings_20260502_230811\sora_chinese_daily_candidates.jsonl`。
- 日文兜底：`artifacts/vn_sora_refresh/sora_daily_candidates.jsonl`。
- `data/style_bank/sora_style_bank.jsonl`：不含 raw 台词的派生风格特征。

隔离 harness 使用后者，而不是把原文塞给模型。生成层看到的是类似：

```text
来源：原作台词派生风格特征；禁止照抄或复述原台词。
可用动作：clingy_presence / pull_attention / question_chain
情绪倾向：neutral
节奏线索：rapid_followup / question / 反问/追问
```

这让 `Soul.md` 继续只做语言风格锚定，同时让常见 scene 的 good reference 从人工短片段逐步迁移到原作派生特征。
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

本轮修复的 cluster：

- cheap wave punctuation：生成侧降噪 + scorer soft penalty。
- `what_doing` attention wait：问“在干嘛”时不说一直等用户来找。
- `presence` attention debt / incomplete question tail：确认在场，不追问需求或抱怨等待。
- `good_news` scene grounding：`happy` 中出现“跑通/搞定”时使用 `good_news` contract。
- `meal_share` stuffed scolding：吃撑时接“先缓一下”，不训人、不推越界身体照顾。
- write takeover usefulness：写作任务的 `内容/要求/题目/丢来` 计入 technical helpfulness。

## Migration Rule

现有运行时链路要迁移到这个 harness 时，先新增一个上游压缩步骤：

```text
runtime state / organs / memory / rag
  -> ReplyControlPacket
  -> isolated surface generation
```

最终生成层的单测应断言 prompt 中没有 raw history、raw memory、organ output、tool candidates 或未压缩 RAG 文档。旧测试如果要求最终生成器读取这些原始对象，应改写为测试上游 packet 压缩器。
