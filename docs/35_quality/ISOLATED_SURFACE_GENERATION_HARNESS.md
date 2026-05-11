# Isolated Surface Generation Harness

目的：先把 `Soul.md + System Prompt` 的最终表层文本生成稳定下来，再接历史、状态、传感器或器官族输出。

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
4. `run_isolated_surface_generation(...)`
   - 只接收一个注入的 `generate(messages)` 函数。
   - harness 不知道 runtime state，也不从全局状态取上下文。
5. `parse_surface_candidates(...)`
   - 解析 `{"candidates":[...]}`，最多取 5 个。
6. `score_isolated_surface_candidates(...)`
   - 使用现有 hard gate 拦截绝对错误。
   - 使用 sample-guided scorer 做 soft scoring。
   - 对 `must_include`、`must_not_do`、`question_budget` 做控制面扣分。
7. `select_best_isolated_candidate(...)`
   - 在 hard gate 通过后，优先选无控制面违约且分数最高的候选。

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

## Migration Rule

现有运行时链路要迁移到这个 harness 时，先新增一个上游压缩步骤：

```text
runtime state / organs / memory / rag
  -> ReplyControlPacket
  -> isolated surface generation
```

最终生成层的单测应断言 prompt 中没有 raw history、raw memory、organ output、tool candidates 或未压缩 RAG 文档。旧测试如果要求最终生成器读取这些原始对象，应改写为测试上游 packet 压缩器。
