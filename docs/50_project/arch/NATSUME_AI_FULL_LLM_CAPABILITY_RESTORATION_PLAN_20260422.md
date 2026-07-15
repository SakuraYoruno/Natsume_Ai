受众：CODEX
标题：Natsume_Ai 全认知能力 LLM 恢复方案（2026-04-22）

版本：v1.2
日期：2026-04-22
状态：🟢 IMPLEMENTED BASELINE（已按多批次落地）
落地节奏：**依赖有序的多批次实现，非单个大 PR**
关联分支：codex
关联 commit（文档）：将在本次 push 后回填

---

## 0.0 当前代码校准说明（2026-04-22）

本文档最初按“一次 PR 全改”撰写，但当前仓库已按多批次把主恢复链路落到代码里。阅读口径更新如下：

- `host_brain/integrations/llm_helpers.py` / `llm_gateway.py` 已提供共享模型池、tier A/B/C/D、MiniMax provider、冷却、缓存、`LLM_POOL_EXHAUSTED`、`llm_pool_call` 审计。
- 主聊天与主感知链已切池：`chat_agent.py`、`thalamus_agent.py`、`multimodal_observer_backends.py`、`plan_graph.py`；独立 `emotion_llm_inference.py` 当时未接入生产主链，现已删除。
- 认知模块已接池：`reflection_chain.py`、`self_reflection.py`、`evolution_desire_engine.py`、`bond_narrative.py`、`style_distiller.py`、`prefrontal_agent.py`、`mirror_neuron_agent.py`、`initiative_governor_agent.py`。
- 内心世界已拆成 `inner_world_numeric_tick` + `inner_world_monologue_tick`；后台独白生成走共享池，调度器已对后台 LLM 任务增加 `focus / gaming / sleep` 模式门控。
- 记忆层当前已补 `hippocampus_agent` 检索后联想摘要，以及 `MemoryDistillationEngine / LongTermMemoryDistiller` 的共享池摘要接线。

因此，本文档现在应按“蓝图 + 已落地执行记录”阅读；若正文某处仍写“待实现 / 一次 PR 全改”，以当前代码和本节校准说明为准。

---

## 0. 文档定位（读者必读）

本文档是 Natsume_Ai 项目的**架构级恢复方案**，目标是把当前"架构壳完整、认知 LLM 能力不均衡"的局面收敛到统一共享池、统一观测、统一调度门控，恢复 Natsume_Ai 设计之初承诺的**"意识主权 AI 伴侣"**行为。

本文档属于 `docs/50_project/arch/` 的**蓝图与实施记录**类型文档，当前主要用于对照“原计划”与“实际代码落点”，并需在落地时同步：
- 更新 `docs/00_index/00_DOCMAP.md` 加入本文档索引
- 更新 `.env.example` 补齐所有新增环境变量（含默认值与用途说明）
- 落地后在 `artifacts/acceptance_reports/` 产出本地 DEV 实测报告；仅将长期结构化证据提升到文档区
- 运行节点按 `docs/50_project/guides/RUNTIME_FEEDBACK_REPORT_TEMPLATE.md` 产出反馈报告

本方案的前置事实基础：
- `host_brain/app/agent_runtime_shared.py`（主聊天链）
- `host_brain/app/chat_agent.py`（ChatAgent 流式生成）
- `host_brain/tribe/thalamus_agent.py`（意图分类 · 当前已切共享池，保留极少数快速通道）
- `host_brain/reasoning/reflection_chain.py`（反思链 · 当前已接共享池 reflect / revise）
- `host_brain/skills/impl/inner_world_skill.py`（内心世界 tick · 当前已拆双 tick）
- `host_brain/persona/evolution_orchestrator.py`（进化周期 · 当前 desire 路径走共享池）
- `host_brain/persona/emergent_growth_engine.py` + `interaction_pattern_analyzer.py`（涌现成长分析与成长应用）
- `host_brain/integrations/llm_gateway.py`（LLM 网关 · 当前已提供 `complete_with_pool`）
- `configs/model_plane/node_registry.yaml` + `configs/model_plane/model_profiles.yaml`（模型平面配置资产）
- `host_brain/scheduler/scheduler.py`（任务调度器）

---

## 1. 背景与动机

### 1.1 核心问题

本文档初稿时，Natsume_Ai 存在严重的**"架构降级"**；当前这一问题已完成主链级修复，但该历史背景仍保留为方案动机：

| 层面 | 状态 |
|------|------|
| 命名层 | 完整（Limbic / Prefrontal / InnerWorld / Reflection / ...）|
| 架构层 | 完整（22+ Tribe Agents、事件总线、多层门禁、定时任务） |
| **实现层（初稿时）** | **大量能力被降级为规则 / 数值 / 模板池随机选，未调用 LLM** |

结果：项目**看起来**是有生命的 AI 伴侣，**运行起来**更像"数值状态机 + 单次对话生成"。这也是本恢复方案最初立项的直接原因。

### 1.2 代码层真实情况（初稿审计基线 vs 当前代码）

| 模块 | 初稿审计基线 | 当前代码状态 | 当前 LLM 状态 |
|------|---------|---------|----------|
| `reflection_chain` | 5 维度规则打分 + 规则 revise | `reflect / revise` 已接共享池，保留少量清洗/安全收口 | ✅ |
| `inner_world_tick` | 只更新 `loneliness / anticipation` 数值 | 已拆 `inner_world_numeric_tick` + `inner_world_monologue_tick` | ✅ |
| `emergent_growth_weekly` | 曾被文档审计为“未真正调 LLM” | 现通过共享池驱动 `InteractionPatternAnalyzer`，无 LLM 时直接跳过 | ✅ |
| `evolution_desire_engine` | `llm_fn` 可选，失败降级模板池 | 当前运行时由 orchestrator 以 strict LLM 方式调用 | ✅ |
| `limbic_agent` | 主要是数值调节 | 已补情绪叙事 / reactivity hint 接池 | ✅ |
| `hippocampus_agent` | 事件订阅 + Chroma 查询 | 已补 recall 后联想摘要 | ✅ |
| `thalamus_agent` | 规则优先，规则命中直接 return | 当前为 LLM-primary，保留 `phatic_fastpath / task_progress / recent_turn` 快速通道 | ✅ |
| 记忆蒸馏 | 缺统一 LLM 摘要层 | `MemoryDistillationEngine / LongTermMemoryDistiller` 已接共享池摘要客户端 | ✅ |

### 1.3 为什么必须修

本项目的核心产品承诺是"**意识主权 AI 伴侣**"——妹妹独处时在真实生活、被问时在真实思考、成长时在真实演化。当前实现层把这些都降级为确定性规则，产品承诺无法兑现。

### 1.4 本方案的核心原则

1. **禁止规则降级原则**：认知类任务一律走 LLM，LLM 失败必须跨模型池切换，禁止静默降级到规则 / 模板池。
2. **全能力恢复**：9 层认知架构全部打开 LLM，不做"先三点再扩展"的分步。
3. **按批次落地，但保持统一底座**：实现上按基础设施、主链、认知模块、调度与文档分批推进；不再采用单个大 PR。
4. **多模型池容错**：MiniMax 全系 + Gemini 2.5 Pro / Flash-Lite 互为主备（Claude 后期再说）。
5. **物理层保留规则**：纯时间 / 数值 / 格式解析层（Circadian / Endocrine 数值 / Airlock 解析 / phatic_fastpath）**明确允许**保持规则，不属于降级。

---

## 2. 核心原则（架构契约）

### 2.1 禁止规则降级原则（P0）

**规则降级**的定义：

> 当某模块本应通过 LLM 完成"认知 / 生成 / 语义判断"任务时，因成本 / 延迟 / 可用性考虑，改为使用规则匹配、模板池随机选、关键词打分等确定性逻辑替代。

本方案明确禁止以下模式：

```python
# ❌ 禁止：LLM 失败降级到模板
try:
    result = llm_fn(prompt)
except Exception:
    result = random.choice(TEMPLATE_POOL)

# ❌ 禁止：llm_fn 可选、失败默认模板的兜底函数签名
def generate_desire(..., llm_fn: Any | None = None):
    if llm_fn is None:
        return _fallback_template()

# ❌ 禁止：硬编码关闭 LLM
{"args": {"use_llm": False}}

# ❌ 禁止：规则结果命中即 return，不给 LLM 路径机会
rule_result = classify_task_intent_by_rules(text)
if rule_result is not None:
    return rule_result  # 直接跳过 LLM
```

正确模式：

```python
# ✅ 正确：失败切池、耗尽抛错
result = await gateway.complete_with_pool(
    messages=messages,
    cognitive_tier="B",
    request_id=request_id,
    scene_type="inner_world_tick",
)
if result.pool_exhausted:
    raise LlmPoolExhausted("全模型池耗尽")
```

### 2.2 认知 vs 物理的边界（P0）

**认知类任务**（必须 LLM、禁止规则降级）：

- 主对话 / 反思 / 改写 / 意图分析（非短句快速通道）
- 内心独白 / 白日梦 / 情感体验文本生成
- 人格成长 / 意愿生成 / 反思摘要
- 情境化记忆联想 / 语义蒸馏 / 风格抽象
- Bond narrative / 社交解读 / 情感镜像

**物理类 / 解析类任务**（允许保留规则）：

- Circadian 按时间判定昼夜
- Endocrine / Somatic 数值模拟（cortisol / 疲劳值 / 糖原储备）
- Airlock PII / 有害内容正则过滤
- 格式解析（JSON 提取、render_target_lang 判定）
- phatic_fastpath（"你好 / 嗯 / 哦" 条件反射直出）
- `silence_tick / body_sense_tick` 恢复意愿与疲劳数值
- 审计写入、任务调度、门禁计算

### 2.3 LLM 失败处理原则（P0）

LLM 调用失败时的唯一合法行为是**切换到下一个模型**，禁止降级到规则。全池耗尽时的处理策略：

| 场景 | 耗尽处理 |
|------|---------|
| 主对话（ChatAgent）| SSE 返回 `phase: COMMIT, decision: REJECT, reason_code: LLM_POOL_EXHAUSTED`；不返回规则生成的伪对话 |
| 内心独白 tick | 本次 tick 静默跳过（`status: SKIPPED, reason_code: LLM_POOL_EXHAUSTED`），等下次周期再试 |
| Reflection | 跳过反思，保留原始流式输出（原输出已经是 LLM 生成，不属于降级）|
| 情感文本生成 | 本次事件不生成文本，但保留数值更新（数值属物理层）|
| 人格成长 | 本周不成长，等 LLM 恢复后补跑（SchedulerTaskConfig 支持 retry）|
| 意图路由（C 等级）| 回退到默认 `chat` 路由（这是无法决策时的默认值，不是规则判断）|
| 记忆蒸馏 | 本次不蒸馏，等下一周期 |

### 2.4 缓存策略（可选 P1）

允许短期缓存以降低重复成本：

- 粒度：按 prompt hash + cognitive_tier
- TTL：5 分钟
- 范围：仅限同一 session 内
- 禁止：跨 session 复用（否则妹妹会"记错人"）
- 禁止：主对话 / 反思 / 意图分类使用缓存（结果必须实时）
- 允许：内心独白 / 白日梦 / bond narrative 等非实时任务短期复用

### 2.5 sys_mode 分级限频（P0）

| sys_mode | 策略 |
|----------|------|
| `normal` | 全开 |
| `focus` | 保留主对话；关闭 inner_world / default_mode / evolution / distillation 等后台 LLM |
| `gaming` | 保留主对话 + gaming_companion_tick；关闭其他后台 LLM |
| `sleep` | 全部后台 LLM 关闭；主对话保留（用户半夜找妹妹仍能对话）|

该分级在 `host_brain/scheduler/scheduler.py::_trigger_task` 的门禁链路中落地。

---

## 3. 多模型池设计

### 3.1 模型池总表

| Provider | Model | 认知等级 | 角色 | 备注 |
|----------|-------|---------|------|------|
| Gemini | gemini-2.5-pro | A 主 / B 备 / C 极端兜底 | 主对话、反思关键决策 | 当前 `HOST_CHAT_AGENT_MODEL_ID` 已指向 |
| Gemini | gemini-2.5-flash-lite | C 主 / D 备 | 路由 / 意图分类 / Route Decider | 快 + 准 + 省；当前 `HOST_DISPATCH_AGENT_MODEL_ID` 已指向 |
| MiniMax | MiniMax-M2.7-Pro | A 备1 / B 备2 | 主对话备选 | 新接入 |
| MiniMax | MiniMax-M2.7 | A 备2 / D 主 | 标准生成 / 蒸馏 / narrative | 新接入 |
| MiniMax | MiniMax-M2.6 | A 备3 | 主对话第三备 | 新接入 |
| MiniMax | MiniMax-M2-her | B 主 / C 备3 / D 备3 | 内心独白 / 情感文本 / 联想 | **伴侣专训**，场景最匹配 |
| MiniMax | MiniMax-abab6.5s | C 备1 / D 备1 | 轻量路由 / 蒸馏备选 | 高性价比 |

### 3.2 认知等级 × 模型池映射

```
┌────────────────────────────────────────────────────────────┐
│  等级 A（主对话 / 反思 / 关键决策）                         │
│  主：   gemini-2.5-pro                                     │
│  备1：  MiniMax-M2.7-Pro                                   │
│  备2：  MiniMax-M2.7                                       │
│  备3：  MiniMax-M2.6                                       │
├────────────────────────────────────────────────────────────┤
│  等级 B（内心独白 / 情感生成 / 联想 / 人格成长）            │
│  主：   MiniMax-M2-her（伴侣专训）                         │
│  备1：  gemini-2.5-pro                                     │
│  备2：  MiniMax-M2.7-Pro                                   │
│  备3：  MiniMax-M2.7                                       │
├────────────────────────────────────────────────────────────┤
│  等级 C（路由 / 意图分类 / Route Decider）                  │
│  主：   gemini-2.5-flash-lite                              │
│  备1：  MiniMax-abab6.5s                                   │
│  备2：  MiniMax-M2.7                                       │
│  备3：  gemini-2.5-pro（极端兜底）                         │
├────────────────────────────────────────────────────────────┤
│  等级 D（蒸馏 / narrative / 风格 / 打分）                   │
│  主：   MiniMax-M2.7                                       │
│  备1：  MiniMax-abab6.5s                                   │
│  备2：  gemini-2.5-flash-lite                              │
│  备3：  gemini-2.5-pro                                     │
└────────────────────────────────────────────────────────────┘
```

### 3.3 切换触发条件

| 失败条件 | 行动 |
|---------|------|
| HTTP 5xx / 连接超时 | 立即切下一家 |
| HTTP 429（限流）| 立即切下一家；当前模型 **冷却 60s** |
| HTTP 401 / 403 | 告警；切下一家；当前模型标记"不可用"直到下次 key 轮转 |
| 返回空内容 | 重试 1 次，仍失败切下一家 |
| 返回格式异常（JSON 解析失败）| 重试 1 次，仍失败切下一家 |
| 安全拦截返回违规 | 记录审计；直接切下一家；**不得修改 prompt 重试** |
| 所有模型都失败 | 抛出 `LlmPoolExhausted`，由上层按 2.3 处理 |

### 3.4 现有 model plane 扩展

当前 `configs/model_plane/node_registry.yaml` 仅包含 Gemini 节点。本方案新增以下节点：

```yaml
# ---- 新增：MiniMax 系列节点 ----
minimax_m27_pro:
  description: "MiniMax-M2.7-Pro（A 等级备选）"
  provider: minimax
  model_id: MiniMax-M2.7-Pro
  temperature: 0.70
  max_output_tokens: 1500
  node_allowlist_envs: [dev, staging, prod]

minimax_m27_standard:
  description: "MiniMax-M2.7（D 等级主节点）"
  provider: minimax
  model_id: MiniMax-M2.7
  temperature: 0.50
  max_output_tokens: 1200

minimax_m26_backup:
  description: "MiniMax-M2.6（A 等级第三备）"
  provider: minimax
  model_id: MiniMax-M2.6
  temperature: 0.70
  max_output_tokens: 1200

minimax_m2_her:
  description: "MiniMax-M2-her 伴侣专训（B 等级主节点）"
  provider: minimax
  model_id: MiniMax-M2-her
  temperature: 0.90
  max_output_tokens: 800

minimax_abab_light:
  description: "MiniMax-abab6.5s 轻量（C/D 备选）"
  provider: minimax
  model_id: MiniMax-abab6.5s
  temperature: 0.40
  max_output_tokens: 500
```

新增 profile：`multi_provider_pool_v1`，定义 4 个认知等级对应的节点链。

---

## 4. LLM Gateway 改造方案

### 4.1 新增 API

在 `host_brain/integrations/llm_gateway.py` 的 `LlmGatewayContract` 上新增：

```python
class LlmGatewayContract:
    async def complete_with_pool(
        self,
        *,
        messages: list[dict[str, Any]],
        cognitive_tier: Literal["A", "B", "C", "D"],
        request_id: str,
        scene_type: str,
        session_id: str | None = None,
        max_pool_attempts: int | None = None,
        cache_key: str | None = None,   # 2.4 缓存策略
        cache_ttl_s: int = 300,
        response_schema: type | None = None,
    ) -> PoolCompleteResult:
        """
        按 cognitive_tier 对应的模型池顺序尝试，禁止降级到规则。
        耗尽时 PoolCompleteResult.pool_exhausted = True。
        
        Raises:
            LlmPoolExhausted: 全池耗尽且调用方要求抛错时
        """

@dataclass
class PoolCompleteResult:
    content: str
    final_model: str
    final_provider: str
    attempts: list[ModelAttempt]  # 每次尝试的模型 / 状态 / 延迟
    pool_exhausted: bool
    total_latency_ms: int
    cached: bool  # 是否来自缓存
    cognitive_tier: str
```

### 4.2 provider_factory 扩展

`host_brain/app/provider_factory.py` 新增 MiniMax 适配（**openai-compatible 协议**，MiniMax 开放平台已对外提供 OpenAI 兼容接口）：

```python
if provider == "minimax":
    base_url = os.environ.get("HOST_MINIMAX_BASE_URL", "https://api.minimax.chat/v1")
    api_key = _resolve_key("minimax")
    return _build_openai_compatible_client(
        base_url=base_url,
        api_key=api_key,
        model_id=model_id,
        timeout_ms=timeout_ms,
    )
```

### 4.3 CI 禁止清单（Grep 检查）

在 CI 流水线新增 `scripts/ci/check_no_rule_degrade.py`，检测以下违规模式：

```
# 违规正则（命中即 CI fail）
REGEX_BANNED:
  - r"llm_fn\s*=\s*None"                       # llm_fn 可选兜底
  - r"use_llm\s*[:=]\s*False"                  # 硬编码关闭 LLM
  - r"if\s+llm_fn\s+is\s+None:\s*\n.*?return\s+\w*template"  # 模板兜底
  - r"_fallback_template\(\)"                  # 模板池兜底函数
  - r"random\.choice\(\w*TEMPLATE"             # 模板池随机选
  
# 例外白名单（允许路径）
WHITELIST_PATHS:
  - "host_brain/airlock/**"                    # 安全过滤层
  - "host_brain/core/endocrine.py"             # 数值模拟层
  - "host_brain/core/metabolism.py"            # 代谢数值
  - "host_brain/persona/body_sense.py"         # 身体感知数值
  - "tests/**"                                 # 测试文件
```

---

## 5. 9 层认知架构的 LLM 恢复方案

### 5.1 感知层

| 模块 | 文件 | 改造 | 认知等级 | 调用频次 |
|------|------|------|---------|---------|
| `thalamus.classify_task_intent` | `host_brain/tribe/thalamus_agent.py` | **默认走 LLM**；规则仅保留 phatic / task_progress / recent_turn 三条物理快速通道 | C | 每次对话 1 次 |
| `cortex_intent_analyzer` | `host_brain/tribe/cortex_intent_analyzer.py` | 新增 LLM 做上下文隐含意图推断（Tribe 族上下文聚合 → LLM 解读）| B | 每次对话 1 次 |
| `multimodal_observer` | `host_brain/app/multimodal_observer_backends.py` | 确认现有 LLM 接入；增加 vision LLM 切池 | A | 附件触发 |

**改动点**：
- `thalamus_agent.classify_task_intent` 中 `rule_result is not None: return` 仅允许对下面三类短路：
  - `phatic_fastpath`（"你好"等短句）
  - `task_progress`（进度查询）
  - `recent_turn`（"刚刚说什么"）
- 其他情况必须走 `classify_task_intent_by_llm`

### 5.2 记忆层

| 模块 | 文件 | 改造 | 认知等级 |
|------|------|------|---------|
| `hippocampus_agent` | `host_brain/tribe/hippocampus_agent.py` | 新增 `associate_context_with_llm()`：取召回结果后用 LLM 做情境化联想 | B |
| `distillation_engine` | `host_brain/app/memory/distillation_engine.py` | 蒸馏阶段加 LLM 语义摘要（原实现仅 embedding 聚类）| D |
| `long_term_distiller` | `host_brain/memory/long_term_distiller.py` | 跨会话整合走 LLM | D |
| `episodic_memory` 写入 | `host_brain/memory/episodic.py` | 存入前用 LLM 生成情景摘要 | D |

### 5.3 情感层

| 模块 | 文件 | 改造 | 认知等级 |
|------|------|------|---------|
| `limbic_agent` | `host_brain/tribe/limbic_agent.py` | 新增 `generate_emotion_narrative()`：根据数值状态 + 事件生成"妹妹此刻的感受文本"（1-3 句），存入 `emotion_timeline` | B |
| `emotion_reactivity` | `host_brain/persona/emotion_reactivity.py` | 情感共鸣判断从规则映射换 LLM | B |
| `emotional_silence` | `host_brain/persona/emotional_silence.py` | 沉默 hint 生成走 LLM（当前是模板字典）| B |
| `emotion_llm_inference` | 已删除的历史候选实现 | 未接入生产；若恢复必须通过 Model Runtime 与正式 composition 接线 | B |

### 5.4 人格层

| 模块 | 文件 | 改造 | 认知等级 |
|------|------|------|---------|
| `self_reflection` | `host_brain/persona/self_reflection.py` | 运行时已切 `strict_llm=True`；共享池失效时跳过，不再静默模板降级 | B |
| `emergent_growth_weekly` | `host_brain/skills/impl/emergent_growth_skill.py` + `host_brain/tribe/prefrontal_agent.py` | 当前运行时无 LLM 直接 `SKIPPED`，`InteractionPatternAnalyzer` 走 strict LLM | B |
| `evolution_desire_engine` | `host_brain/persona/evolution_desire_engine.py` | orchestrator 当前以 `strict_llm=True` 调用，不再把运行时失败静默落回模板 | B |
| `drift_detector` | `host_brain/persona/drift_detector.py` | 漂移判断从规则评分换 LLM 语义判断 | A |
| `interaction_growth_analyzer` | `host_brain/persona/interaction_growth_analyzer.py` | 交互模式理解走 LLM | B |
| `persona_growth` | `host_brain/persona/persona_growth.py` | 保留数值层 + 新增"成长叙事"LLM 生成 | B |

### 5.5 内心世界层

| 模块 | 文件 | 改造 | 认知等级 | 调用频次 |
|------|------|------|---------|---------|
| `inner_world_skill.execute_inner_world_tick` | `host_brain/skills/impl/inner_world_skill.py` | **改造为双阶段**：第一阶段（纯规则）继续更新 `loneliness / anticipation` 数值；第二阶段调用 `LimbicAgent.generate_inner_monologue_with_llm()` 生成 1-3 句内心独白，存入 `inner_stream` | B | 每 5 分钟（降频）|
| `inner_activity_agent` | `host_brain/persona/inner_activity_agent.py` | 新增 `generate_current_activity_with_llm()`：生成"妹妹正在做什么" | B | 每 10 分钟 |
| `default_mode_agent` | `host_brain/tribe/default_mode_agent.py` | 新增 `trigger_daydream_with_llm()`：默认模式激活时生成白日梦 | B | 事件触发 |
| `silence_tick / body_sense_tick` | `host_brain/skills/impl/silence_skill.py`、`body_sense_skill.py` | **不变**（纯数值恢复，物理层）| — | 每 60 秒 |

**重要调整**：`inner_world_tick` 从 60 秒一次降到 **5 分钟一次**，避免 LLM 调用爆量。数值更新保持 60 秒（拆分为 `inner_world_numeric_tick` 与 `inner_world_monologue_tick`）。

### 5.6 社交层

| 模块 | 文件 | 改造 | 认知等级 |
|------|------|------|---------|
| `social_brain_agent` | `host_brain/tribe/social_brain_agent.py` | 新增 `interpret_relational_dynamics_with_llm()`：关系动态解读 | B |
| `bond_dynamics` | `host_brain/bond/bond_dynamics.py` | 保留数值；新增 LLM narrative 生成挂钩 | B |
| `bond_narrative` | `host_brain/bond/bond_narrative.py` | **LLM 主路径**，删模板兜底 | B |
| `mirror_neuron_agent` | `host_brain/tribe/mirror_neuron_agent.py` | 情感镜像从数值映射换 LLM | B |

### 5.7 执行层

| 模块 | 文件 | 改造 | 认知等级 |
|------|------|------|---------|
| `prefrontal_agent` | `host_brain/tribe/prefrontal_agent.py` | 多步规划走 LLM（当前事件调度无 LLM）| A |
| `planning_agent` | `host_brain/agents/planning_agent.py` | 已有 LLM，接入新池 | A |
| `initiative_governor_agent` | `host_brain/tribe/initiative_governor_agent.py` | 主动性判断走 LLM | B |
| `motor_cortex_agent` | `host_brain/tribe/motor_cortex_agent.py` | **不变**（执行层物理）| — |

### 5.8 反思与质量层

| 模块 | 文件 | 改造 | 认知等级 |
|------|------|------|---------|
| `reflection_chain.reflect` | `host_brain/reasoning/reflection_chain.py` | **完全替换**为 LLM 语义评估（5 维度规则全删）| A |
| `reflection_chain.revise` | 同上 | **完全替换**为 LLM 语义重写 | A |
| `reflection_chain.should_reflect` | 同上 | 保留规则触发条件（判断"要不要反思"不是认知任务）| — |
| `response_policy` | `host_brain/persona/response_policy.py` | 保留规则骨架；在关键节点（如 `avoid_verbatim_repeat`）加 LLM 兜底判断 | C |
| `style_distiller` | `host_brain/retrieval/style_distiller.py` | 风格抽象走 LLM | D |

### 5.9 物理/生理节律层（保留规则）

**明确允许规则**：

| 模块 | 文件 | 理由 |
|------|------|------|
| `circadian_agent` | `host_brain/tribe/circadian_agent.py` | 生物钟依赖 time.now()，LLM 无增益 |
| `endocrine_agent` | `host_brain/tribe/endocrine_agent.py` | 内分泌是数值模拟，规则正确 |
| `somatic_agent` | `host_brain/tribe/somatic_agent.py` | 躯干感觉数值 |
| `immune_agent` | `host_brain/tribe/immune_agent.py` | 免疫门禁属于安全规则层 |
| `glymphatic_agent` | `host_brain/tribe/glymphatic_agent.py` | 类淋巴清理规则 |
| `ans_agent` | `host_brain/tribe/ans_agent.py` | 自主神经节律 |
| `airlock/*` | `host_brain/airlock/*.py` | PII / 违规内容过滤 |
| `phatic_fastpath` | `host_brain/app/agent_runtime_shared.py::_maybe_build_phatic_fastpath_reply` | 条件反射，保留 |
| `task_progress_fastpath` | 同上 | 进度查询规则明确 |
| `recent_turn_answer` | 同上 | 元对话规则明确 |

---

## 6. 调用量预估与成本控制

### 6.1 预期 5h 窗口量级

| 场景 | 逻辑 LLM 次数 | 含 12% 切池重试的实际 API 请求 |
|------|------------|-----------------------------|
| 休眠期（无对话）| 130-170 | 150-190 |
| 轻度对话（20 条）| 280-350 | 315-395 |
| 中度对话（60 条）| 455-545 | **510-610** |
| 重度对话（200 条）| 800-1000 | 900-1120 |
| 联调期 | 1500+ | 1700+ |

### 6.2 套餐建议（ClawBuddy / MiniMax 接入方）

- 起步：**Plus ¥49/月**（1500 次/5h，覆盖中度对话场景）
- 升级触发：连续 3 天 >10% 的 5h 窗口触达 1200+ → 升 Max ¥119
- 联调期：临时升 Max ¥119 或 Max-极速 ¥199

Gemini API 使用单 key；多模型降级由模型池和 MiniMax 备选承担。

### 6.3 成本分摊（按等级估算）

| 等级 | 占比 | 典型模型 | 相对成本权重 |
|------|-----|---------|------------|
| A | 20% | gemini-2.5-pro / MiniMax-M2.7-Pro | 10× |
| B | 40% | MiniMax-M2-her / gemini-2.5-pro | 5× |
| C | 25% | gemini-2.5-flash-lite | 1× |
| D | 15% | MiniMax-M2.7 / abab6.5s | 2× |

综合成本比"全用 2.5-pro"降低约 50%。

---

## 7. sys_mode 分级限频实施

`host_brain/scheduler/scheduler.py::_trigger_task` 新增基于 sys_mode 的 LLM 任务分级：

```python
_LLM_TASK_BY_SYSMODE = {
    "normal":  {"allow_all": True},
    "focus":   {"allow": ["agent_health_check"]},  # 只保留心跳
    "gaming":  {"allow": ["agent_health_check", "gaming_companion_tick"]},
    "sleep":   {"allow": ["agent_health_check"]},  # 只保留心跳
}
```

主对话链（`cortex_orchestrator`）**不受 sys_mode 限频**（用户随时可以叫妹妹说话）。

---

## 8. Observability 新增字段

在 `host_brain/observability/metrics.py` 与审计链路新增：

```json
{
  "llm_pool_call": {
    "request_id": "...",
    "scene_type": "inner_world_monologue",
    "cognitive_tier": "B",
    "attempts": [
      {"model": "MiniMax-M2-her", "provider": "minimax", "status": "OK", "latency_ms": 1200}
    ],
    "final_model": "MiniMax-M2-her",
    "final_provider": "minimax",
    "pool_exhausted": false,
    "cached": false,
    "total_latency_ms": 1200,
    "degraded_to_rule": false
  }
}
```

**不变量**：`degraded_to_rule` 必须永远为 `false`。出现 `true` 视为架构违规，CI 报错。

---

## 9. 单 PR 实施路径

### 9.1 改动清单

#### 9.1.1 核心基础设施（P0）

| # | 文件 | 改动 |
|---|------|------|
| 1 | `host_brain/integrations/llm_gateway.py` | 新增 `complete_with_pool` + `PoolCompleteResult` + `LlmPoolExhausted` |
| 2 | `host_brain/integrations/llm_helpers.py` | 新增 `astream_llm_chunks_with_pool`（主对话流式版本）|
| 3 | `host_brain/app/provider_factory.py` | 新增 MiniMax provider（openai-compatible）|
| 4 | `host_brain/app/model_router.py` | 新增 cognitive_tier → 模型池映射 |
| 5 | `configs/model_plane/node_registry.yaml` | 新增 5 个 MiniMax 节点 |
| 6 | `configs/model_plane/model_profiles.yaml` | 新增 `multi_provider_pool_v1` profile |
| 7 | `.env.example` | 新增 MiniMax 所有环境变量（含默认值与用途说明）|

#### 9.1.2 感知层（P0）

| # | 文件 | 改动 |
|---|------|------|
| 8 | `host_brain/tribe/thalamus_agent.py` | `classify_task_intent` 改为 LLM 主路径 |
| 9 | `host_brain/tribe/cortex_intent_analyzer.py` | 新增 LLM 隐含意图推断 |

#### 9.1.3 情感层（P0）

| # | 文件 | 改动 |
|---|------|------|
| 10 | `host_brain/tribe/limbic_agent.py` | 新增 `generate_emotion_narrative` + `generate_inner_monologue` |
| 11 | `host_brain/persona/emotion_reactivity.py` | LLM 化情感共鸣 |
| 12 | `host_brain/persona/emotional_silence.py` | LLM 化沉默 hint 生成 |

#### 9.1.4 人格层（P0）

| # | 文件 | 改动 |
|---|------|------|
| 13 | `host_brain/persona/self_reflection.py` | 强制 LLM 主路径、删模板兜底 |
| 14 | `host_brain/skills/impl/emergent_growth_skill.py` + `host_brain/tribe/prefrontal_agent.py` | 无 LLM 直接跳过，禁止 keyword fallback 静默落地 |
| 15 | `host_brain/persona/evolution_desire_engine.py` | 运行时 strict LLM，关闭静默模板降级 |
| 16 | `host_brain/persona/drift_detector.py` | 规则评分 → LLM |
| 17 | `host_brain/persona/persona_growth.py` | 新增成长叙事 |
| 18 | `host_brain/persona/interaction_growth_analyzer.py` | LLM 化 |

#### 9.1.5 内心世界层（P0）

| # | 文件 | 改动 |
|---|------|------|
| 19 | `host_brain/skills/impl/inner_world_skill.py` | 拆分为 `inner_world_numeric_tick`（60s）+ `inner_world_monologue_tick`（5min）|
| 20 | `host_brain/app/http_hooks.py` | 注册新的 monologue_tick 任务 |
| 21 | `host_brain/persona/inner_activity_agent.py` | LLM 化当前活动生成 |
| 22 | `host_brain/tribe/default_mode_agent.py` | 新增白日梦 LLM 生成 |

#### 9.1.6 记忆层（P0）

| # | 文件 | 改动 |
|---|------|------|
| 23 | `host_brain/tribe/hippocampus_agent.py` | LLM 情境化联想 |
| 24 | `host_brain/app/memory/distillation_engine.py` | LLM 语义摘要 |
| 25 | `host_brain/memory/long_term_distiller.py` | LLM 跨会话整合 |
| 26 | `host_brain/memory/episodic.py` | 写入前 LLM 情景摘要 |

#### 9.1.7 社交层（P0）

| # | 文件 | 改动 |
|---|------|------|
| 27 | `host_brain/tribe/social_brain_agent.py` | LLM 化关系动态解读 |
| 28 | `host_brain/bond/bond_narrative.py` | 强制 LLM 主路径 |
| 29 | `host_brain/tribe/mirror_neuron_agent.py` | LLM 情感镜像 |

#### 9.1.8 执行层（P0）

| # | 文件 | 改动 |
|---|------|------|
| 30 | `host_brain/tribe/prefrontal_agent.py` | 多步规划 LLM 化 |
| 31 | `host_brain/tribe/initiative_governor_agent.py` | 主动性判断 LLM 化 |

#### 9.1.9 反思与质量层（P0）

| # | 文件 | 改动 |
|---|------|------|
| 32 | `host_brain/reasoning/reflection_chain.py` | 全替换为 LLM（should_reflect 保留规则）|
| 33 | `host_brain/retrieval/style_distiller.py` | LLM 化风格抽象 |
| 34 | `host_brain/persona/response_policy.py` | 关键节点加 LLM 兜底 |

#### 9.1.10 调度与观测（P0）

| # | 文件 | 改动 |
|---|------|------|
| 35 | `host_brain/scheduler/scheduler.py` | sys_mode 分级限频 |
| 36 | `host_brain/observability/metrics.py` | 新增 llm_pool_call 指标 |
| 37 | `host_brain/audit/ledger_constants.py` | 新增 LLM_POOL_EXHAUSTED 错误码 |
| 38 | `scripts/ci/check_no_rule_degrade.py` | 新增 CI 降级检查脚本 |

#### 9.1.11 文档与索引

| # | 文件 | 改动 |
|---|------|------|
| 39 | `docs/00_index/00_DOCMAP.md` | 加入本方案文档索引 |
| 40 | `artifacts/acceptance_reports/DEV_TEST_REPORT_full_llm_restoration_20260422.md` | 按模板产出本地开发实测报告 |

### 9.2 环境变量新增（同步到 `.env.example`）

```bash
# ──────────────────────────────────────────────────────────
# 多模型池 / 全能力 LLM 恢复（2026-04-22）
# ──────────────────────────────────────────────────────────

# 是否启用多模型池主路径；关闭时退回 Gemini 单供应商（仅灰度 / 回滚用）
# 默认值：true（禁止生产环境关闭，关闭即视为架构降级）
HOST_LLM_MULTI_POOL_ENABLED=true

# 全池耗尽时的策略：reject（主对话）/ skip（后台任务）
HOST_LLM_POOL_EXHAUSTED_POLICY_CHAT=reject
HOST_LLM_POOL_EXHAUSTED_POLICY_BACKGROUND=skip

# ── MiniMax 接入 ──
# MiniMax 是否启用
HOST_MINIMAX_ENABLED=true
# MiniMax OpenAI-compatible base URL
HOST_MINIMAX_BASE_URL=https://api.minimax.chat/v1
# MiniMax API Key 引用（真实 Key 放在 secret store）
MINIMAX_API_KEY=
MINIMAX_API_KEY_REF=sec://llm/minimax/default
# MiniMax 超时（ms）
HOST_MINIMAX_TIMEOUT_MS=15000
# MiniMax 主模型 ID（A 等级主候选时使用）
HOST_MINIMAX_PRIMARY_MODEL_ID=MiniMax-M2.7-Pro
# MiniMax 标准模型 ID（D 等级主）
HOST_MINIMAX_STANDARD_MODEL_ID=MiniMax-M2.7
# MiniMax 伴侣专训模型 ID（B 等级主）
HOST_MINIMAX_HER_MODEL_ID=MiniMax-M2-her
# MiniMax 轻量模型 ID（C 等级备）
HOST_MINIMAX_LIGHT_MODEL_ID=MiniMax-abab6.5s
# MiniMax 次旗舰模型 ID（A 等级第三备）
HOST_MINIMAX_M26_MODEL_ID=MiniMax-M2.6

# ── 认知等级 → 模型池映射 ──
# 每层池按逗号分隔，顺序即主备顺序
HOST_LLM_POOL_TIER_A=gemini:gemini-2.5-pro,minimax:MiniMax-M2.7-Pro,minimax:MiniMax-M2.7,minimax:MiniMax-M2.6
HOST_LLM_POOL_TIER_B=minimax:MiniMax-M2-her,gemini:gemini-2.5-pro,minimax:MiniMax-M2.7-Pro,minimax:MiniMax-M2.7
HOST_LLM_POOL_TIER_C=gemini:gemini-2.5-flash-lite,minimax:MiniMax-abab6.5s,minimax:MiniMax-M2.7,gemini:gemini-2.5-pro
HOST_LLM_POOL_TIER_D=minimax:MiniMax-M2.7,minimax:MiniMax-abab6.5s,gemini:gemini-2.5-flash-lite,gemini:gemini-2.5-pro

# ── 切池触发 ──
# 429 限流冷却时间（ms）
HOST_LLM_POOL_RATE_LIMIT_COOLDOWN_MS=60000
# 单次调用最多切池次数（None = 走完整个池）
HOST_LLM_POOL_MAX_ATTEMPTS=

# ── 缓存策略 ──
# 是否启用短期缓存（仅 B/D 等级，A/C 不缓存）
HOST_LLM_POOL_CACHE_ENABLED=true
HOST_LLM_POOL_CACHE_TTL_S=300

# ── sys_mode 分级限频 ──
HOST_SCHED_LLM_SYSMODE_FOCUS_ALLOW=agent_health_check
HOST_SCHED_LLM_SYSMODE_GAMING_ALLOW=agent_health_check,gaming_companion_tick
HOST_SCHED_LLM_SYSMODE_SLEEP_ALLOW=agent_health_check

# ── 内心世界拆分 ──
# 数值 tick（纯规则）周期 / 秒
HOST_INNER_WORLD_NUMERIC_TICK_S=60
# 内心独白 tick（LLM）周期 / 秒
HOST_INNER_WORLD_MONOLOGUE_TICK_S=300
```

### 9.3 实施顺序（单 PR 内部的逻辑顺序）

在同一 PR 中，建议按以下顺序组织 commit，便于 review：

1. `feat(llm-pool): add complete_with_pool API + MiniMax provider`（基础设施）
2. `feat(model-plane): add MiniMax nodes + multi-provider pool profile`（配置）
3. `refactor(perception): upgrade thalamus + cortex intent to LLM-primary`（感知层）
4. `refactor(memory): add LLM-based association + distillation`（记忆层）
5. `refactor(emotion): add LLM-based emotion narrative + silence`（情感层）
6. `refactor(persona): remove template fallback in self_reflection/growth/desire`（人格层）
7. `refactor(inner-world): split numeric tick and monologue tick`（内心世界层）
8. `refactor(social): LLM-primary bond narrative + mirror neuron`（社交层）
9. `refactor(execution): LLM-based prefrontal planning + initiative`（执行层）
10. `refactor(reflection): replace rule-based reflect/revise with LLM`（反思层）
11. `feat(scheduler): sys_mode-aware LLM task gating`（调度层）
12. `feat(observability): add llm_pool_call metrics + LLM_POOL_EXHAUSTED error code`（观测）
13. `chore(ci): add no-rule-degrade check script`（CI）
14. `docs: add full LLM restoration plan + update DOCMAP + .env.example`（文档）

---

## 10. 验收标准

### 10.1 CI 自动化检查

- [x] `scripts/ci/check_no_rule_degrade.py` 已落库，并接入 `tools/static_scan/run_all.py`
- [ ] `configs/model_plane/*.yaml` 通过 schema 校验
- [x] `.env.example` 所有新增变量都有默认值和注释
- [x] 相关单元/契约测试已通过（见 DEV 实测报告）
- [ ] `ruff` / `mypy` 零 error

### 10.2 功能验证（DEV 本机）

- [x] 主对话切池底座验证：共享池 / 冷却 / 缓存 / 耗尽 / MiniMax 切换相关单测通过
- [ ] 内心独白生成：等 5 分钟 → 观察 `inner_stream` 写入真实文本（非数值）
- [x] Reflection / Bond / Style / Hippocampus / Distillation / Scheduler 相关桥接与契约测试通过
- [ ] Evolution 真实成长：凌晨 03:00 cron → 观察 `growth_awareness` 字段含 LLM 生成文本
- [x] 全池耗尽验证：pool helper / scheduler skip 路径单测通过
- [ ] sys_mode=focus 时：验证内心独白 tick 被拦截

### 10.3 运行节点验证（RUN 真机）

- [ ] 连续 3 次"你是" → 验证第 2 次和第 3 次是真实反思后的承接，非机械复读
- [ ] 独处 10 分钟 → 验证 inner_stream 累积了真实内心独白
- [ ] 周日 03:00 → 验证 `emergent_growth_weekly` 产出的成长意愿是 LLM 生成的真实文本
- [ ] 模拟 Gemini 全挂（断网）→ 验证切到 MiniMax 池继续正常运行
- [ ] 观察一整天 → 提取所有 `llm_pool_call` 审计，`degraded_to_rule` 应全为 `false`

### 10.3.1 当前 DEV 证据

- 历史 DEV 报告已从工作树清理，需要时从 Git 历史追溯。
- 当前本地已完成的定向验证包括共享池、反思/风格/关系桥接、内心世界双 tick、记忆蒸馏、scheduler 模式门控、CI 禁降级门禁等相关套件。

### 10.4 不变量清单

| 不变量 | 检查手段 |
|--------|---------|
| `degraded_to_rule` 永远为 false | 审计扫描 |
| 主对话 LLM 调用必走 complete_with_pool | grep `llm_runtime.invoke\(` 确认只剩物理兜底路径 |
| MiniMax provider 可正常调用 | 启动探针 |
| CI 禁止模式 0 命中 | CI 报告 |
| 所有新增 env 变量都在 .env.example | diff 校验 |

---

## 11. 风险与回滚方案

### 11.1 风险

| 风险 | 等级 | 缓解 |
|------|-----|------|
| MiniMax API 首次接入不稳定 | 高 | 主对话等级 A 仍主用 Gemini，MiniMax 仅作备用先跑一周观察 |
| LLM 调用量爆涨，成本失控 | 中 | sys_mode 分级限频 + 5 分钟缓存 + 内心独白频率降到 5 分钟 |
| 内心独白质量差，"妹妹变啰嗦" | 中 | prompt 模板设计 1-3 句硬上限 + response_policy 长度约束 |
| 切池延迟累积，首字过慢 | 中 | 单次切池最多 3 次 + 总超时 15s + 首次失败立即切无 backoff |
| LLM 生成有害内容 | 中 | 保留 `airlock` 出口过滤 + MiniMax / Gemini 自带安全层 |
| Reflection LLM 误判 / 错改好回复 | 中 | `should_reflect` 保留规则触发阈值，仅高风险场景启用 |

### 11.2 回滚方案

单 PR 落地后如需回滚：

```bash
# 单环境变量一键降级（灰度回滚）
HOST_LLM_MULTI_POOL_ENABLED=false
# 此时 llm_gateway.complete_with_pool 退化为调用现有单 Gemini 路径
# 但注意：所有已改造模块仍会走 LLM，不会回到规则降级
```

**完全回滚**需 revert 本 PR。回滚时需注意保留 `.env.example` 新增项（标注为"deprecated"），避免下次又被重复新增。

---

## 12. 待哥哥后期确认事项

以下项本 PR 暂不处理，后期补充：

- [ ] **Claude 接入**：哥哥决定后再加 Anthropic provider + Claude Sonnet 4.5 到 A 等级主候选
- [ ] **成本监控报警**：日均 token 消耗超阈值自动告警（需要新增 observability 规则）
- [ ] **A/B 对比框架**：同一次对话并发调 Gemini + MiniMax 做质量对比（科学评估哪个更适合伴侣场景）
- [ ] **多模态池分层**：vision LLM 是否独立一套池（GPT-4V / Gemini Vision / MiniMax Vision）

---

## 附录 A：与现有文档的关系

| 关联文档 | 关系 |
|----------|------|
| `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md` | 本文档不改变运行面拓扑，仅恢复各模块内部 LLM 能力 |
| `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md` | 仅在生产模型 authority 或主链事实变化后同步 |
| 2026-04-21 Conversation History 方案（Git 历史） | 并行方案；Reflection / DialogueState 改造与本方案解耦但可共用 LLM 池 |
| 2026-04-21 Tool Call Chain 分析（Git 历史） | Tool 链路 LLM 调用也纳入本方案的池化框架 |
| `docs/30_devspec/ModelPlane.md` | 本方案的模型平面扩展需反向更新 ModelPlane.md |

## 附录 B：本方案不涉及的范围

- 工具链（tool_routes / capability_forge）的调用协议不变
- Edge Client 的 TTS / VTS / ASR 配置不变
- 审计 schema 只新增字段不改旧字段
- 观测前端 UI 可在本 PR 后增量支持新指标（不阻塞）
- 白皮书 / PRD / DevSpec 不改（本方案是架构级恢复，非需求变更）

---

（EOF）
