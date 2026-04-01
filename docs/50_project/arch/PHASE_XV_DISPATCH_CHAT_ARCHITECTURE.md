受众：MASTER
标题：Phase XV 架构重构 — Dispatch Agent + Chat Agent 分离

# Phase XV: Dispatch Agent + Chat Agent 分离架构

**日期**：2026-04-01
**状态**：PROPOSAL（待哥哥确认）
**前置**：Phase XIV v4（TopAgent 顶级 Agent 架构）

---

## 1. 问题陈述

Phase XIV v4 将夏目蓝实现为单一的 TopAgent，身兼三职：

1. **意图识别 + 任务分解**（判断是否委托子 Agent）
2. **闲聊对话**（80% 路径，直接 LLM 流式生成）
3. **子 Agent 结果渲染**（将技术产出拼接进回复）

### 1.1 实际运行中暴露的严重缺陷

**缺陷 A：内部元数据泄漏给用户**

```
[ReAct 降级] agent_id=code_agent, 无 LLM gateway
"},"intent_draft":{},"meta":{"draft_only":true}}
```

根因：子 Agent 的 LLM gateway 构建依赖 `langchain_openai` 导入，在 RUN 节点静默失败（`except Exception: return None`）→ ReAct 降级 → 降级文本+旧链路 JSON 格式被 `_render_delegated_result_naturally()` 的正则兜底漏过 → 直接到达用户。

**缺陷 B：委托判断过于激进**

- "为什么啊"（纯追问）→ 被误委托给子 Agent
- "skill接入有问题吗"（技术闲聊）→ 触发 code_agent 委托
- 根因：prompt 中只有一行"如果是闲聊直接回复"的弱防护，`intent_agent` 描述太宽泛，无代码层后验校验

**缺陷 C：夏目蓝没有实际的 Skills 执行能力**

- Skills 系统有**三个来源**，共计 **57+ 个 Skills**：
  - **来源 1：内置 Builtin Skills**（7 个）— `builtin_registry.py`，4 个 noop + 3 个有实现的查询
  - **来源 2：领域 Registry Providers**（50 个）— 12 个 `*_registry.py`，由 `provider_catalog.py` 统一编排
    - Browser（8）/ Desktop（6）/ File（5）/ Personality（5）/ Emotion（4）/ IDE（4）/ System（4）/ Shell（3）/ Memory（3）/ Notification（3）/ Scheduler（3）/ Web（2）
    - 每个领域可通过 `HOST_SKILL_*_ENABLED` 环境变量独立开关
    - 有 16 个真正的 impl 执行器（browser_executor/web_executor/memory_executor 等）
  - **来源 3：Skills Hub 外部安装**（可扩展）— `hub_loader.py` 扫描 5 个目录自动发现
    - 主 Hub 目录（`skills/`）、`~/.skillhub/skills/`、`~/.openclaw/skills/`、`~/.codebuddy/skills/`、`HOST_SKILL_EXTERNAL_DIRS`
    - 支持 GitHub 远程安装：`install_skill("github:owner/repo/slug")`
    - 每个 Skill 是一个含 `SKILL.md` 的目录，可选 `provider.py` + `executor.py`
    - 当前已有 1 个示例 skill（`example-echo`）
  - **附加来源：自定义 Providers**（`custom_providers/`）— 放入 `*_provider.py` 自动发现
- **架构完备但主链路完全不走 Skills 系统**
- `_AUTO_RUNNABLE_SKILLS` 只有 7 个，其中 4 个是 noop
- SkillComposer 的 DAG 执行器没有注册 runner，全部退化为 placeholder
- 实际能力仅靠 4 个子 Agent（code/translate/intent/vision）的 ReAct 循环，而这些 ReAct 循环因 gateway 构建失败全部降级

**缺陷 D：TTS→Gateway 语音消息链路完全断裂**

"给我唱首歌"→ 应该：生成日语歌词 → TTS 合成语音 → 通过 QQ 发送语音消息。实际：

| 步骤 | 状态 | 断裂点 |
|------|------|--------|
| 识别为需要语音 | ❌ | 无"唱歌"→TTS 意图识别 |
| 生成日语文本 | ❌ | TopAgent 直接用中文回复"我没办法真的唱" |
| TTS 生成语音 | ⚠️ | `HOST_GATEWAY_TTS_ENABLED` 默认 false；`post_speak()` 走 Edge 本地扬声器 |
| Gateway 发送语音 | ⚠️ | `post_gateway_reply(msg_type="voice")` 存在但从未被调用 |

**核心发现**：`post_speak()` 和 `post_gateway_reply()` 是**两条独立的出站通道**：
- `post_speak()` → Edge RPC `/v1/edge/speak` → **本地扬声器+VTS**
- `post_gateway_reply()` → Gateway Server `/v1/gateway/reply` → **QQ/微信消息发送**

TTS 生成的音频**没有被回传给 Gateway Server**。

**缺陷 E：每个 Agent 的上下文管理严重断裂**

当前上下文管理只在 TopAgent 层面维护，子 Agent **完全无状态**：

| 维度 | TopAgent | 子 Agent（code/translate/intent/vision） |
|------|---------|----------------------------------------|
| 对话历史 | ✅ 最近 16 轮 + 压缩摘要（共 9000 tokens） | ❌ 零（每次全新空白上下文） |
| 记忆系统 | ✅ RecallFusion 5 路语义检索 + StateArbiter | ❌ 零 |
| 情感状态 | ✅ 完整情感管线（emotion_vector/echo/monologue） | ❌ 零 |
| Bond 上下文 | ✅ bond_texture / persona_expression_hint | ❌ 零 |
| 风格渲染 | ✅ 完整 style_pack（v5 蒸馏） | ❌ 零 |
| 跨轮次代码续作 | ⚠️ TopAgent 知道"继续刚才的代码"指什么，但传给子 Agent 的只有 task 字符串 | ❌ 无法看到之前的代码 |

**具体代码证据**：

1. **SubAgentRequest.context 只传 `gateway`**（`top_agent.py` Line 758）：
```python
context={"gateway": subagent_gateway} if subagent_gateway is not None else {}
```
→ 没有对话历史、没有记忆摘要、没有 bond 上下文。

2. **SubAgentDispatcher.dispatch() 传给 handler 的 payload**（`sub_agent_protocol.py` Line 129-134）：
```python
payload = {"user_input": req.task, "query": req.task, "request_id": req.request_id, **req.context}
```
→ `user_input` 就是一个任务描述字符串，无任何历史。

3. **ReAct 循环的 messages 初始化**（`react_loop.py` Line 206-209）：
```python
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_input},  # 只有任务描述
]
```
→ 每次调用都是全新消息列表，循环结束后全部销毁。

4. **ContextCompressor 只在 TopAgent 调用**（`top_agent_prompt.py` Line 260-279）：
```python
compressor = ContextCompressor(
    max_tokens=9000,           # 总 token 预算
    recent_turns=16,           # 最近 16 轮
    pre_compress_save_enabled=False,
)
```
→ 子 Agent 不会调用 ContextCompressor，也没有类似机制。

**后果**：
- 用户说"继续刚才的代码"→ TopAgent 委托 code_agent → code_agent 看到的只有 `"继续刚才的代码"` 五个字 → 无法续写
- 用户说"把刚才那段翻译成日语"→ translate_agent 不知道"刚才那段"是什么
- 子 Agent 无法感知用户情绪（bond/emotion），输出缺乏人格温度
- 多轮技能执行（如"先搜索再总结"）时，后续步骤无法引用前步结果

**与 Gateway Orchestrator 的对比**：Gateway 走的是**完整的 prompt 构建管线**（与 TopAgent 等价），有 StateArbiter 四路查询 + RecallPack，但它是独立链路，和 TopAgent 的子 Agent 链路互不相通。

**缺陷 F：全通道拟人化输出缺失 — TTS 一段念完、不分句、不分次**

拟人化回复不应该只是 Gateway 消息分路的事，而是**整个系统所有出站通道的设计原则**。真人说话是一句一句说的，有停顿、有节奏，不会一口气念完 200 字。

**当前 TTS 的致命问题**：

```python
# top_agent.py _async_post_process() Line 1193
# 一次性把整段 narrative 发给 Edge TTS — 可能几百字一口气念
_tts_result = await post_speak(
    text=narrative,  # ← 整段文本！
    ...
)
```

讽刺的是，`SentenceSplitter` **已经在流式阶段把句子切好了**（SENTENCE 事件），但这些句子只被前端 SSE 消费，**TTS 完全没用到**。Edge 端的 `SpeechPlaybackQueue` 也已经支持排队分句播放，但 Host 端只调了一次 `post_speak`。

**缺陷 G：`render_target_lang` 的根本性设计错误 — 文字通道和语音通道的语言被混为一谈**

**这是整个语言系统最根本的设计问题。**

当前 `infer_render_target_lang()` 根据用户输入的语言自动推断，然后**用同一个值同时控制**文字输出语言和 TTS 输入语言。这个设计前提就是错的。

**为什么需要日语通道？** 因为 TTS 声线模型是用日语语料训练的，只有日语文本才能被正确合成为自然的语音。这和用户说什么语言**完全无关**——就算用户说俄语，文字回复也应该是中文，TTS 输入也应该是日语。

**当前错误设计**：

```python
# prompt_builder.py Line 150-164 — 根据用户输入语言推断
def infer_render_target_lang(*, user_message: str, ...) -> str:
    if 包含假名: return "ja"    # 用户说日语 → 全系统日语
    if 包含汉字: return "zh"    # 用户说中文 → 全系统中文
    return "ja"                  # 默认日语

# 然后这个值被用于：
# - 角色人设（中文版 vs 日文版）
# - LLM 输出语言规则
# - 风格蒸馏（中文版 vs 日语版）
# - 台词参考检索
# - TTS lang_hint
# 全都是同一个值！
```

**正确的设计**：文字通道和语音通道的语言是**独立固定**的，不由用户输入语言决定。

```
用户说任何语言（中文/日文/英文/俄文/emoji/随便什么）
        │
        ▼
   LLM 生成（双轨）
        │
        ├──→ text_lang = "zh"（固定）
        │    用途：文字回复给用户看
        │    - Web SSE 流式文字 → 中文
        │    - QQ/微信文字消息 → 中文
        │    - 前端气泡显示 → 中文
        │
        └──→ tts_lang = "ja"（固定）
             用途：喂给 TTS 声线模型
             - TTS 声线是日语训练的 → 必须日语文本
             - 翻译步骤：中文 narrative → 日语 TTS 文本
             - 或 LLM 直接双语输出（narrative_zh + narrative_ja）
```

| 维度 | 当前（错误） | 正确 |
|------|------------|------|
| 文字回复语言 | 跟随用户输入语言变化 | **固定中文**（用户的母语） |
| TTS 输入语言 | 跟随用户输入语言变化 | **固定日语**（声线模型要求） |
| 角色人设 | 随 render_target_lang 切换 | 固定加载中文版（给 LLM 理解）+ 日文版人设作为语气参考 |
| 风格蒸馏 | 随 render_target_lang 切换 | 中文蒸馏用于文字输出风格，日语蒸馏用于 TTS 语气参考 |
| 台词参考 | 随 render_target_lang 切换 | 中文台词用于文字风格，日语台词用于 TTS 参考 |
| 翻译 Agent | 按 render_target_lang | 特殊场景，目标语言由任务指定，不受主通道影响 |

**`infer_render_target_lang()` 应该被废弃**，替换为明确的双轨语言常量：

```python
# 新设计
TEXT_OUTPUT_LANG = "zh"   # 文字通道永远中文
TTS_INPUT_LANG = "ja"     # 语音通道永远日语

# 而不是
render_target_lang = infer_render_target_lang(user_message=...)  # ← 废弃
```

**TTS 日语文本的获取方式**（三个候选方案，P7 实施时选择）：

| 方案 | 实现 | 延迟 | 质量 |
|------|------|------|------|
| A. LLM 双语输出 | prompt 要求同时输出 `narrative_zh` + `narrative_ja` | 低（一次生成） | 高（LLM 原生翻译） |
| B. 后置翻译 | 先生成中文 → 用翻译 API/LLM 转日语 | 高（+1 次 API） | 中高 |
| C. 仅日语生成 + 前置翻译 | LLM 生成日语 → 文字通道做日→中翻译 | 高 | 中（二次翻译损耗） |

**推荐方案 A**：LLM 一次生成双语。prompt 中明确要求 JSON 包含 `narrative_zh`（用户看的文字）和 `narrative_ja`（TTS 吃的日语），利用 LLM 的多语言能力一步到位。

**所有出站通道的正确语言分配**：

| 出站通道 | 语言 | 文本来源 |
|---------|------|---------|
| **SSE 流式**（Web 前端文字） | 中文 | `narrative_zh` |
| **TTS 本地语音**（ASR 通道） | 日语 | `narrative_ja`，分句后逐句 TTS |
| **VTS 虚拟形象** | — | emotion_tag 驱动，不涉及语言 |
| **Gateway 文字**（QQ/微信） | 中文 | `narrative_zh` |
| **Gateway 语音**（QQ/微信） | 日语 | `narrative_ja`，分句后逐句合成发送 |

**ASR 通道的理想拟人化体验**（修正版）：

```
用户（对着麦克风说）："今天怎么样？"（中文/日文/任何语言都一样）

Web 前端文字气泡（中文）:
  "今天过得还不错呢，早上去了公园散步，中午吃了拉面，不过下午有点困，你呢？"

同时，TTS 语音输出（日语，分句）:
  ← 「今日はなかなか良かったよ。」（TTS → 播放，~1.5 秒）
  ← 自然停顿 300ms
  ← 「朝は公園でお散歩したの。」（TTS → 播放，~1.5 秒）
  ← 自然停顿 200ms
  ← 「お昼はラーメン食べた。」（TTS → 播放，~1.5 秒）
  ← 自然停顿 400ms（思考感）
  ← 「午後はちょっと眠くて…あなたは？」（TTS → 播放，~2 秒）
```

**用户眼前看到中文文字，耳中听到日语语音**——这才是正确的双轨体验。

---

## 2. 架构改造方案

### 2.1 核心思想

将 TopAgent 拆分为两个角色清晰的 Agent：

```
当前（Phase XIV v4）:
  用户 → TopAgent(调度+闲聊+渲染) → 子Agent → 简单拼接 → 用户
                                              ↑ 渲染不经过人格管线

提案（Phase XV）:
  用户 → DispatchAgent(纯调度) → 子Agent → ChatAgent(夏目蓝人格渲染) → 用户
                                ↘ 闲聊也走 ChatAgent ↗
```

### 2.2 DispatchAgent（任务调度器）

**职责**：意图识别 + 任务分解 + 子 Agent 调度。**不生成面向用户的文字**。

**输入**：用户消息 + 情感上下文 + 检索上下文

**输出**：
- 闲聊路径：`{type: "chat", context: {recall, emotion, bond, ...}}`
- 执行路径：`{type: "execute", agent_id: "code_agent", task: "...", result: "子Agent产出"}`
- 技能路径：`{type: "skill", skill_id: "web_search", result: "搜索结果"}`
- 语音路径：`{type: "voice", text_for_tts: "日语歌词...", target: "gateway"}`

**实现策略**：
- 轻量 LLM 调用（低 temperature），或纯规则路由（短消息/追问直接判定为 chat）
- **硬规则防护**：用户消息 < 8 字且不含操作动词 → 强制 chat 路径
- 子 Agent gateway 改用 `stream_llm_chunks` 复用 TopAgent 已验证的 LLM 路径（消除 langchain 导入依赖）

### 2.3 ChatAgent（夏目蓝本人）

**职责**：人格表达 + 语言渲染 + 情感回应。**所有面向用户的输出必经此 Agent**。

**输入**：
- 闲聊路径：用户消息 + recall/emotion/bond context → 正常对话
- 执行路径：用户消息 + 子 Agent 技术产出 → 用人格语气归纳渲染
- 技能路径：用户消息 + Skill 执行结果 → 用人格语气呈现
- 语音路径：生成日语文本 → TTS → Gateway 语音发送

**核心保证**：
- 走完整的 prompt 构建管线（角色人设 + 风格蒸馏 + 情感回应 + Bond 风格 + 反思链）
- **子 Agent 的技术产出作为 context 注入 prompt**，而不是直接拼接到 narrative
- 即使子 Agent 返回垃圾/JSON/降级文本，ChatAgent 都会用夏目蓝的语气重新组织
- 永远不会泄漏内部元数据

### 2.4 Skills 系统接入

**当前问题**：三个来源共 57+ 个 Skills 存在但主链路不调用。

**改造方案**：
1. DispatchAgent 识别到技能型请求（搜索/记忆/桌面操作等）
2. DispatchAgent 通过 `SkillLoader.execute()` 调用 Skills 系统（走 Governor Token 审批）
3. Skill 执行结果交给 ChatAgent 渲染
4. Hub Skills 通过 `trigger_words` 字段自动匹配用户意图

**三个来源的接入策略**：

| 来源 | Skill 数量 | 接入方式 |
|------|-----------|---------|
| Builtin（内置） | 7 | 替换 noop handler 为真正实现，接入 `_AUTO_RUNNABLE_SKILLS` |
| Registry Providers（领域） | 50 | DispatchAgent 根据意图路由到对应 Provider，通过 SkillLoader 审批执行 |
| Hub Skills（外部安装） | 可扩展 | 通过 `trigger_words` 自动匹配 + SkillLoader 审批执行 |

**新增的 Skill 路由规则**（DispatchAgent 内部）：

| 用户意图 | 路由目标 | 说明 |
|---------|----------|------|
| 唱歌/读给我听/朗读 | `voice` 路径 | ChatAgent 生成日语文本 → TTS → Gateway 语音 |
| 搜索/查找/检索 | `skill:web_search` | web_executor → Tavily/DuckDuckGo |
| 写代码/修改代码 | `execute:code_agent` | ReAct 循环 → ChatAgent 渲染 |
| 翻译 | `execute:translate_agent` | 多节点翻译 → ChatAgent 渲染 |
| 记忆/回忆 | `skill:memory_recall` | memory_executor → ChatAgent 渲染 |
| 日常闲聊/追问/情感 | `chat` | 直接走 ChatAgent |

### 2.5 TTS→Gateway 语音消息链路打通

**完整链路**：

```
用户"唱首歌" 
  → DispatchAgent: type=voice
  → ChatAgent: 生成日语歌词文本（走完整人格 prompt）
  → TTS 合成: post_speak() → Edge TTS 引擎 → PCM 音频
  → 音频回传: 保存 PCM 到临时文件
  → Gateway 语音发送: post_gateway_reply(
      platform="qq", 
      conversation_id="qq_dm_xxx",
      msg_type="voice",
      media_path="/tmp/tts_xxx.wav"
    )
  → QQ 适配器: OneBot record 消息段 → 用户收到语音
```

**关键改动**：
1. Gateway Orchestrator 的 `_generate_tts_async()` 改为 **"双出站"模式**：
   - 本地端（原有）：`post_speak()` → Edge 扬声器+VTS
   - Gateway 端（新增）：TTS 音频文件 → `post_gateway_reply(msg_type="voice", media_path=...)` → QQ/微信语音消息
2. `HOST_GATEWAY_TTS_ENABLED` 默认改为 `true`
3. 增加 `HOST_GATEWAY_VOICE_REPLY_ENABLED` 环境变量，控制是否将 TTS 结果作为语音消息发送

### 2.6 每个 Agent 的上下文管理

**当前问题**：子 Agent 完全无状态，每次调用都是全新空白上下文。

**改造方案**：为每个 Agent 层级建立适当的上下文管理机制。

#### 2.6.1 ChatAgent（夏目蓝本人）— 完整人格上下文

ChatAgent 作为最终出口，必须持有**最丰富的上下文**：

| 上下文维度 | 来源 | 注入方式 |
|-----------|------|---------|
| 对话历史（16 轮 + 摘要） | `voice_event_log` + ContextCompressor | 复用现有 `_build_recent_session_context_block()` |
| 语义记忆 | RecallFusion 5 路检索 | 复用现有 RecallPack 槽位预算 |
| 角色人设 | `character_profile.json` | 常驻注入 prompt #1 位 |
| 风格蒸馏 | `style_profile_v5.json` | 常驻注入 style_pack slot |
| 情感状态 | emotion_vector / echo / monologue | 复用完整情感管线 |
| Bond 上下文 | BondEmotionalTexture + BondNarrativeLayer | 复用现有 bond_texture 注入 |
| PersonaExpression | PersonaExpressionAdapter | 复用现有 persona_expression_hint |
| 子 Agent 执行结果 | DispatchAgent 传递 | **新增**：作为 `agent_context` 注入 prompt |

**关键设计**：ChatAgent = 现有 TopAgent 的闲聊路径 + 接收来自 DispatchAgent 的子 Agent 结果作为额外 context。
ChatAgent 的 prompt 完全复用现有 `build_top_agent_prompt()` 管线，只是新增一个 `agent_execution_context` 块。

#### 2.6.2 DispatchAgent（纯调度）— 轻量上下文

DispatchAgent 不需要完整的人格上下文，但需要足够的上下文做出准确的调度决策：

| 上下文维度 | 来源 | 说明 |
|-----------|------|------|
| 最近 3 轮对话摘要 | `voice_event_log` | 仅供判断"继续刚才的"指什么 |
| 当前情感标签 | `emotion_tag` | 判断是否为情感交流（→直接走 chat） |
| Bond 阶段 | `bond_stage` | STRANGER 阶段不触发高级 Skills |
| sys_mode | 环境变量 | locked/maintenance 不调度子 Agent |
| 用户消息 | 原始输入 | 硬规则匹配 + LLM 意图判断 |

**token 预算**：DispatchAgent 的 prompt ≤ 2000 tokens（vs ChatAgent ~9000 tokens），确保调度延迟 <200ms。

#### 2.6.3 子 Agent（code/translate/intent/vision）— 任务级上下文

子 Agent 不再是空白上下文。DispatchAgent 在委托时注入**任务级上下文**：

```python
# 改造后的 SubAgentRequest
@dataclass
class SubAgentRequest:
    agent_id: str
    task: str
    user_message: str
    request_id: str
    session_id: str
    context: dict[str, Any]  # 改造重点 ↓
    timeout_ms: int = 30_000

# context 将包含：
context = {
    "gateway": subagent_gateway,       # LLM 网关（已有）
    # ── 新增上下文 ──
    "recent_turns": [                  # 最近 3 轮对话（精简版）
        {"role": "user", "content": "帮我写一个快排算法"},
        {"role": "assistant", "content": "好的，这是基本的快排实现：...（代码摘要 200 字）"},
        {"role": "user", "content": "继续优化它"},
    ],
    "previous_agent_result": "...",     # 上一次同类 Agent 的执行结果（如果有）
    "emotion_tag": "NEUTRAL",           # 当前情感（供人格温度调节）
    "bond_stage": "TRUSTED_FRIEND",     # 关系阶段（供语气调节）
    "render_target_lang": "zh",         # 渲染语言
}
```

**ReAct 循环改造**（`react_loop.py`）：

```python
# 改造后：将上下文注入 messages 初始化
messages = [
    {"role": "system", "content": system_prompt},
]

# 注入最近对话历史（如果有）
recent_turns = context.get("recent_turns", [])
for turn in recent_turns[-3:]:  # 最多 3 轮
    messages.append(turn)

# 注入上一次执行结果（如果有）
prev_result = context.get("previous_agent_result")
if prev_result:
    messages.append({
        "role": "user",
        "content": f"上一次的执行结果如下（供参考）：\n{prev_result[:500]}"
    })

# 当前任务
messages.append({"role": "user", "content": user_input})
```

#### 2.6.4 Gateway 通道 — 参数注入而非独立模块

**关键发现：`gateway_orchestrator_handler.py`（1075 行）是死代码，运行时无任何调用者。**

Phase XIV v4 废弃了 SceneRouter/MainChain 后，所有 Gateway（QQ/微信/飞书/企微）消息通过 `input_routes._run_top_agent_pipeline()` **直接走 TopAgent**，Gateway Orchestrator 从未被执行。

**当前 Gateway 消息的实际链路**：
```
QQ/微信用户发消息
  → Edge Gateway Server → POST /v1/host/input (source="GATEWAY")
  → input_routes._run_top_agent_pipeline()
    → ChatRequest(suppress_edge_tts=True, suppress_edge_vts=True)
    → top_agent_sse_generator()          ← 和 Web 前端走完全相同的路径
  → input_routes._deliver_gateway_reply()
    → post_gateway_reply(msg_type="text") → 发回 QQ/微信
```

**这导致的问题**：
- `render_target_lang` 默认 `"ja"` → QQ 用户可能收到日文回复
- 无 `_augment_gateway_prompt_for_reply_depth()` → 可能一句话敷衍
- LLM 错误 fallback 是 `"ちょっと待ってね…"` → QQ 用户看到日文错误消息

**改造方案**：不重新激活 Gateway Orchestrator，而是在 ChatAgent 中支持 Gateway 差异化参数：

```python
# input_routes 在构造 ChatRequest 时注入 Gateway 感知参数
ChatRequest(
    render_target_lang="zh",              # 强制中文（而非 infer 自动推断）
    reply_depth_policy="gateway",          # 回复深度策略（2-4 句,≥12 字）
    suppress_edge_tts=True,                # 已有
    suppress_edge_vts=True,                # 已有
    error_fallback_lang="zh",             # LLM 错误时用中文 fallback
)
```

ChatAgent 只需根据这些参数调整行为，不需要独立的 Gateway 处理模块。Gateway Orchestrator 的 1075 行代码可以在 Phase XV 完成后**安全删除**。

| Gateway Orchestrator 有价值的逻辑 | Phase XV 中的处理方式 |
|-------------------------------|---------------------|
| `render_target_lang="zh"` 强制中文 | ChatAgent 参数（必须） |
| `_augment_gateway_prompt_for_reply_depth` | ChatAgent `reply_depth_policy` 参数 |
| StateArbiter 四路查询 | TopAgent 的去重策略（仅 bond）已足够 |
| TTS suppress_local_play | input_routes 已实现 |
| 后置三路任务（airlock+episodic+bond） | ChatAgent 异步后处理已覆盖 |

好处：
- 所有平台用户经同一个 ChatAgent → 上下文一致、人格一致
- 消除 1075 行死代码
- Gateway 差异化通过参数而非分支实现，维护成本趋近于零

#### 2.6.5 上下文管理总览

```
┌─────────────────────────────────────────────────────────┐
│              DispatchAgent（轻量上下文 ≤2000 tokens）      │
│  ┌──────────────┐  ┌──────────┐  ┌───────────────┐     │
│  │ 最近3轮摘要   │  │emotion_tag│ │ bond_stage    │     │
│  └──────────────┘  └──────────┘  └───────────────┘     │
│                                                         │
│  决策: chat / execute / skill / voice                    │
└─────────────────┬──────────────────┬────────────────────┘
                  │                  │
        ┌─────────▼──────┐  ┌───────▼────────────────────┐
        │   子 Agent      │  │     ChatAgent（完整上下文）   │
        │ (任务级上下文)   │  │  ┌──────────────────────┐  │
        │                 │  │  │ 16 轮历史 + 压缩摘要   │  │
        │ • recent_turns  │  │  │ RecallFusion 5路检索   │  │
        │ • prev_result   │  │  │ 角色人设 + 风格蒸馏    │  │
        │ • emotion_tag   │  │  │ 情感管线 (完整)        │  │
        │ • bond_stage    │  │  │ Bond 上下文 (完整)     │  │
        │ • render_lang   │  │  │ PersonaExpression     │  │
        │                 │  │  │ + agent_context       │  │
        │ 产出: 技术结果    │  │  └──────────────────────┘  │
        └────────┬────────┘  │                            │
                 │            │  产出: 人格渲染后的最终回复   │
                 └────────────▶                            │
                              └────────────────────────────┘
```

**Token 预算分配**：

| Agent | max_tokens | 来源 |
|-------|-----------|------|
| DispatchAgent | ≤ 2,000 | 最近 3 轮摘要 + 规则 prompt |
| 子 Agent | ≤ 4,000 | system_prompt + recent_turns(3) + prev_result + 任务 |
| ChatAgent | ≤ 12,000 | 完整管线（现有 9000 历史 + 检索证据 + 情感/关系/人设） |
| Gateway（改造后） | = ChatAgent | 复用 ChatAgent 管线 |

### 2.7 双轨语言 + 全通道拟人化出站（解决缺陷 F + G）

**核心设计原则**：
1. **双轨语言**：文字通道永远中文，语音通道永远日语——这是固定的，不由用户输入语言决定。
2. **拟人化**：所有出站通道都应该像真人一样输出——分句、有节奏、有停顿。

#### 2.7.1 双轨语言架构（解决缺陷 G，最高优先级）

**废弃 `infer_render_target_lang()`**，替换为双轨常量：

```python
# 新设计：两条固定语言轨道
TEXT_OUTPUT_LANG = "zh"   # 文字通道——用户看的文字，永远中文
TTS_INPUT_LANG = "ja"     # 语音通道——TTS 声线是日语训练的，永远日语

# 废弃：
# render_target_lang = infer_render_target_lang(user_message=...)
```

**LLM 双语输出方案（推荐方案 A）**：

prompt 要求 LLM 一次生成包含双语的 JSON：

```json
{
  "narrative": {
    "text_zh": "今天过得还不错呢，早上去了公园散步。",
    "text_ja": "今日はなかなか良かったよ。朝は公園でお散歩したの。"
  },
  "intent_draft": {},
  "meta": {"draft_only": true}
}
```

- `text_zh`：喂给文字通道（Web SSE / QQ 文字 / 前端气泡）
- `text_ja`：喂给语音通道（TTS → 本地扬声器 / QQ 语音消息）
- LLM 本身就有极强的多语言能力，一次生成双语比后置翻译更快更准

**备选方案（如果方案 A 的 JSON 格式对 LLM 负担太大）**：

| 方案 | 实现 | 延迟增加 | 质量 |
|------|------|---------|------|
| A. LLM 双语输出（推荐） | prompt 要求同时输出 zh + ja | 0（一次生成） | 高 |
| B. 后置翻译 | 先生成中文 → 轻量 LLM/API 翻译成日语 | +200-500ms | 中高 |
| C. TTS 前置翻译 | 只在 post_speak 前做中→日翻译 | +200-500ms（但不阻塞文字通道） | 中高 |

方案 B/C 的翻译可以**异步不阻塞文字通道**——用户先看到中文文字，TTS 日语稍后跟上。

**受影响的模块和改造方式**：

| 模块 | 当前行为 | 改造 |
|------|---------|------|
| `infer_render_target_lang()` | 根据用户输入推断 zh/ja | **废弃**，用 `TEXT_OUTPUT_LANG="zh"` 替代 |
| `_load_character_profile()` | 按 render_target_lang 切换 cn/ja | 固定加载 `instruction_cn`（文字通道人设） |
| `_build_node_role_instruction()` | 按 render_target_lang 切换指令语言 | 固定中文版指令 |
| `language_rule` in prompt | `"Reply in Japanese"` / `"Reply in Chinese"` | 改为要求双语输出（zh + ja） |
| `_style_lang` 台词检索 | 按 render_target_lang 切 cn/ja | 中文台词用于文字风格，日语台词用于 TTS 参考 |
| `RecallFusion` 语言过滤 | 按 render_target_lang 过滤 | 按 `TEXT_OUTPUT_LANG="zh"` 过滤（主要内容中文） |
| `post_speak()` 的 `lang_hint` | 跟随 render_target_lang | 固定 `lang_hint="ja"` |
| `TtsContextRouter` 语言检测 | 根据输出文本假名检测 | 固定 `lang="ja"`（TTS 输入永远是日语） |
| COMMIT SSE 事件 | 只有一个 `narrative` | 改为 `narrative_zh` + `narrative_ja`（前端显示 zh） |

#### 2.7.2 分句 TTS Pipeline（ASR 通道核心改造）

将 TTS 从"整段一次性合成"改为"逐句流式合成+排队播放"：

```
ChatAgent LLM 生成 text_ja（日语 narrative）
  → SentenceSplitter 对 text_ja 分句（日语句号「。」）
    → 切出第 1 句 → 立即 post_speak(ja_sentence_1, utterance_id="utt_001", lang_hint="ja")
    → Edge SpeechPlaybackQueue 排队合成+播放
    → 切出第 2 句 → 立即 post_speak(ja_sentence_2, utterance_id="utt_002", lang_hint="ja")
    → ...依次类推

同时，文字通道：
  → text_zh 逐 token SSE 流式推送给 Web 前端（中文）
  → text_zh 整段发给 QQ/微信（中文）
```

**关键改动**：

1. **Host 端**：在 SENTENCE 事件产生时，**对 text_ja 立即调用 `post_speak`**，不等到 `_async_post_process`
2. **Edge 端**：`SpeechPlaybackQueue` 已支持排队播放，**不需要改 Edge 代码**
3. **句间停顿**：`post_speak` payload 增加 `pause_before_ms`（200-500ms）
4. **情感跟随**：每句话独立推断 `emotion_tag`，VTS 表情跟随每句话变化
5. **文字和语音并行**：文字通道推送 text_zh，语音通道同时处理 text_ja，互不阻塞

**首句延迟优化**：第一句日语生成完毕即可开始 TTS——**用户感知延迟从"全文生成+整段合成"缩短为"第一句生成+第一句合成"**。

#### 2.7.3 Gateway 语音消息分段发送

QQ/微信语音回复场景：

```
ChatAgent 生成 text_ja
  → SentenceSplitter 分句
  → 每句独立 TTS 合成 → 保存为临时音频文件
  → 每句独立 post_gateway_reply(msg_type="voice", media_path=...)
  → QQ 用户收到多条短语音消息（像真人发语音一样）

同时，QQ 文字通道：
  → text_zh 作为文字消息发送（用户能看到中文字幕）
```

#### 2.7.4 VTS 情感分句跟随

每句话独立推断 emotion_tag，表情实时变化：

```python
for ja_sentence in splitter.feed(new_ja_chunk):
    sentence_etag = _infer_emotion_tag_for_tts(ja_sentence, base_etag)
    if sentence_etag != current_vts_etag:
        asyncio.create_task(post_vts(action="set_expression", emotion_tag=sentence_etag))
        current_vts_etag = sentence_etag
```

#### 2.7.5 双轨出站总览

```
                    ChatAgent LLM 双语生成
                            │
                    ┌───────┴───────┐
                    ▼               ▼
              text_zh（中文）   text_ja（日语）
                    │               │
                    │         SentenceSplitter
                    │            分句(ja)
                    │               │
              ┌─────┤         ┌─────┼─────────┐
              ▼     ▼         ▼     ▼         ▼
           SSE流式  Gateway  TTS分句  Gateway  VTS分句
           (Web)   文字消息  post_speak 语音消息 表情跟随
           中文     中文     日语     日语      emotion
              │     │         │       │         │
              ▼     ▼         ▼       ▼         ▼
           用户看到  QQ看到   用户听到  QQ听到   用户看到
           中文文字  中文文字  日语语音  日语语音  表情变化
```

**用户体验**：眼前看到中文文字，耳中听到日语语音，表情实时变化——三个通道各自用最合适的语言，互不干扰。

#### 2.7.6 Gateway 智能回复形式策略（ReplyFormDecider）

**当前问题**：`post_gateway_reply(msg_type="text")` 写死文字，无论用户发什么类型的消息（文字/语音/图片/表情包），夏目蓝永远只回纯文字。

**设计理念**：真人在微信上不会机械地"你发文字我回文字、你发语音我回语音"。真人是根据**内容性质 + 情感强度 + 对话节奏**自然选择回复形式的。核心原则：**文字是日常，语音是惊喜——偶尔一条语音消息，比每次都发语音更打动人。**

**`ReplyFormDecider` 决策引擎**：

```python
@dataclass
class ReplyFormDecision:
    primary: MsgType          # 主要回复形式（TEXT / VOICE）
    secondary: MsgType | None # 可选附加（如先文字后语音）
    reason: str               # 决策原因（审计用）

class ReplyFormDecider:
    """根据上下文智能选择 Gateway 回复形式。"""
    
    def decide(
        self,
        *,
        user_msg_type: MsgType,       # 用户发的消息类型
        narrative_zh: str,            # 中文回复内容
        narrative_ja: str,            # 日语 TTS 文本
        emotion_tag: str,             # 当前情感
        bond_stage: str,              # 关系阶段
    ) -> ReplyFormDecision:
        length = len(narrative_zh)
        
        # ── 规则 1：长回复永远文字（语音太长不友好）
        if length > 80:
            return ReplyFormDecision(MsgType.TEXT, None, "long_reply")
        
        # ── 规则 2：低亲密度不发语音（避免过度亲密）
        if bond_stage in ("STRANGER", "ACQUAINTANCE"):
            return ReplyFormDecision(MsgType.TEXT, None, "low_intimacy")
        
        # ── 规则 3：热词场景 → 文字+语音双发（最暖的体验）
        _WARMTH_KEYWORDS = {"晚安", "早安", "加油", "抱抱", "辛苦了",
                            "想你", "别担心", "我在呢", "没事的", "好好休息"}
        if any(kw in narrative_zh for kw in _WARMTH_KEYWORDS):
            return ReplyFormDecision(MsgType.TEXT, MsgType.VOICE, "warmth_scenario")
        
        # ── 规则 4：高亲密 + 强情感 + 短回复 → 概率触发语音
        _VOICE_EMOTIONS = {"comfort", "coquettish", "happiness", "playful"}
        if emotion_tag in _VOICE_EMOTIONS and length <= 40:
            prob = {"FRIEND": 0.1, "CLOSE_FRIEND": 0.2,
                    "CONFIDANT": 0.3, "SOULMATE": 0.3}
            if random.random() < prob.get(bond_stage, 0):
                return ReplyFormDecision(MsgType.TEXT, MsgType.VOICE,
                                        "intimate_emotional")
        
        # ── 规则 5：用户发语音 → 偶尔回语音（不是每次）
        if user_msg_type == MsgType.VOICE and length <= 30:
            if random.random() < 0.2:
                return ReplyFormDecision(MsgType.VOICE, None, "voice_echo")
        
        # ── 默认：文字
        return ReplyFormDecision(MsgType.TEXT, None, "default_text")
```

**回复形式场景表**：

| 场景 | 用户发的 | 回复形式 | 原因 |
|------|---------|---------|------|
| 日常聊天 | 文字 | **文字** | 默认，效率高 |
| 吐槽/倾诉 | 语音 | **文字**（偶尔+语音） | 文字方便回看；偶尔语音表达陪伴 |
| 发图片 | 图片 | **文字** | 评论图片内容 |
| 发表情包 | 表情 | **文字** | 回应情绪 |
| 晚安 | 文字 | **文字 + 语音** | 语音说晚安比文字暖 10 倍 |
| 撒娇"想你了" | 文字 | **文字 + 语音**（概率） | 高亲密+强情感 |
| 技术问题 | 文字 | **文字** | 长回复+理性内容 |
| 简短应答 | 语音 | **语音**（20% 概率） | 偶尔镜像，不是每次 |

**双发模式（文字+语音）的体验**：

```
QQ 聊天窗口：

哥哥: 今天好累啊，晚安
夏目蓝: 辛苦了呢，好好休息吧。晚安 ☺    ← 中文文字（先到，即时）
夏目蓝: [语音消息 0:03]                   ← 日语短语音（后到，~1s 后）
         "お疲れ様、ゆっくり休んでね。おやすみ。"
```

用户先看到文字知道内容，再听到一条温柔的日语语音——**文字是字幕，语音是温度**。

**概率性设计的意义**：

- **不是每次都语音** → 语音成为「意外的温暖」而非「机械的行为」
- **30% 概率**（SOULMATE 阶段）→ 大约每 3-4 次情感互动冒出一条语音
- **概率随亲密度递增** → 关系越深，越愿意用语音表达
- **对方永远能收到文字** → 不方便听语音时也能看文字，不造成困扰

**环境变量**：
- `HOST_GATEWAY_VOICE_REPLY_ENABLED`（默认 true）：是否启用语音回复
- `HOST_GATEWAY_VOICE_PROBABILITY_SCALE`（默认 1.0）：语音概率倍率（调高=更频繁，调低=更少）

---

## 3. 数据流对比

### 3.1 闲聊路径（80%，无延迟增加）

```
Phase XIV v4:
  用户 → TopAgent LLM 流式 → narrative → 用户

Phase XV:
  用户 → DispatchAgent(规则判定: chat) → ChatAgent LLM 流式 → narrative → 用户
  （DispatchAgent 对短消息/闲聊零 LLM 调用，纯规则 <1ms）
```

### 3.2 执行路径

```
Phase XIV v4:
  用户 → TopAgent LLM 判断 → [DELEGATE:code_agent:task]
       → SubAgent(gateway=None → 降级)
       → _render_delegated_result_naturally(简单正则拼接) → 泄漏

Phase XV:
  用户 → DispatchAgent(LLM 判断: execute)
       → SubAgent(gateway=stream_llm_chunks, 复用已验证路径)
       → DispatchAgent 收集结果
       → ChatAgent(子Agent结果作为context注入完整prompt) → 人格渲染 → 用户
```

### 3.3 语音路径（新增）

```
用户"唱首歌"
  → DispatchAgent(规则判定: voice)
  → ChatAgent(prompt注入: "请生成一段可以哼唱的日语歌词")
  → 日语歌词文本
  → TTS 合成(lang_hint="ja") → PCM 音频
  → 保存为临时文件
  → post_gateway_reply(msg_type="voice") → QQ/微信 → 用户收到语音消息
```

---

## 4. 实施分阶段

### P1: ChatAgent 分离 + 子 Agent 结果渲染改造（核心，解决缺陷 A+B）

- [ ] 新建 `chat_agent.py`：封装 ChatAgent 的完整 prompt+LLM 流式管线
- [ ] ChatAgent 支持 `sub_agent_context` 参数：子 Agent 结果注入 prompt
- [ ] DispatchAgent 替代 TopAgent 的委托判断逻辑，增加硬规则防护
- [ ] 子 Agent gateway 改用 `stream_llm_chunks`
- [ ] 所有输出路径统一经 ChatAgent → narrative → 用户

### P2: Skills 系统接入（解决缺陷 C）

- [ ] DispatchAgent 增加 Skill 路由规则
- [ ] 接入 `SkillLoader.execute()` 到 DispatchAgent
- [ ] 为 web_search/memory_recall 等 Skill 注册真正的 runner
- [ ] Skill 执行结果交给 ChatAgent 渲染

### P3: TTS→Gateway 语音消息链路（解决缺陷 D）

- [ ] Gateway Orchestrator 增加"双出站"TTS 模式
- [ ] DispatchAgent 增加 voice 路由（唱歌/朗读/读给我听）
- [ ] ChatAgent voice 模式：生成日语文本 → TTS → Gateway 语音发送
- [ ] 环境变量 `HOST_GATEWAY_VOICE_REPLY_ENABLED`

### P4: Gateway 通道参数注入 + 废弃 Gateway Orchestrator

- [ ] `input_routes._run_top_agent_pipeline()` 注入 `render_target_lang="zh"` 到 ChatRequest
- [ ] ChatAgent 支持 `reply_depth_policy` 参数，Gateway 通道使用 "gateway" 策略
- [ ] TopAgent/ChatAgent LLM 错误 fallback 根据 `render_target_lang` 动态选择语言
- [ ] 验证 Gateway 通道回复强制中文 + 回复深度 ≥12 字
- [ ] 实现 `ReplyFormDecider`：根据 emotion_tag + bond_stage + 回复长度 + 用户消息类型智能选择回复形式
- [ ] `_deliver_gateway_reply` 改为从 ReplyFormDecider 获取 msg_type，不再写死 "text"
- [ ] 双发模式实现：先 `post_gateway_reply(msg_type="text")` 发中文字幕，再 `post_gateway_reply(msg_type="voice")` 发日语短语音
- [ ] 环境变量 `HOST_GATEWAY_VOICE_REPLY_ENABLED`（默认 true）+ `HOST_GATEWAY_VOICE_PROBABILITY_SCALE`（默认 1.0）
- [ ] 删除 `gateway_orchestrator_handler.py`（1075 行死代码）

### P5: Agent 上下文管理改造（解决缺陷 E）

- [ ] `SubAgentRequest.context` 扩展：注入 `recent_turns`（最近 3 轮精简版）、`previous_agent_result`、`emotion_tag`、`bond_stage`、`render_target_lang`
- [ ] 在 DispatchAgent 委托时构建任务级上下文（从 `voice_event_log` 读取最近 3 轮）
- [ ] ReAct 循环 `messages` 初始化改造：注入 `recent_turns` 和 `previous_agent_result`
- [ ] ChatAgent 接收 `agent_execution_context` 块（子 Agent 执行结果 + 调度元信息）
- [ ] DispatchAgent 构建轻量上下文（≤2000 tokens）：最近 3 轮摘要 + emotion_tag + bond_stage + sys_mode
- [ ] Gateway Orchestrator 改为复用 ChatAgent 管线，不再独立构建上下文

### P6: 双轨语言改造（解决缺陷 G，最高优先级）

- [ ] 定义双轨常量 `TEXT_OUTPUT_LANG="zh"` + `TTS_INPUT_LANG="ja"`
- [ ] LLM 输出格式改造：prompt 要求 JSON 包含 `text_zh`（中文文字）+ `text_ja`（日语 TTS 文本）
- [ ] 废弃 `infer_render_target_lang()`，所有调用点改为使用双轨常量
- [ ] `_load_character_profile()` 固定加载 `instruction_cn`（文字通道人设）
- [ ] `_build_node_role_instruction()` 固定中文版指令
- [ ] `RecallFusion` 语言过滤固定 `lang=zh`
- [ ] `post_speak()` 的 `lang_hint` 固定 `"ja"`，输入文本从 `text_ja` 取
- [ ] `TtsContextRouter` 语言检测废弃，固定 `lang="ja"`
- [ ] SSE COMMIT 事件改为输出 `narrative_zh`（前端显示用）+ `narrative_ja`（TTS 参考/调试用）
- [ ] 前端适配：WebUI 显示 `narrative_zh`，忽略 `narrative_ja`
- [ ] Gateway `post_gateway_reply` 固定发送 `text_zh`（中文文字消息）
- [ ] 环境变量 `HOST_DUAL_LANG_ENABLED`（默认 true），可回退到旧的 `infer_render_target_lang` 模式

### P7: 分句拟人化出站（解决缺陷 F）

- [ ] TTS 分句：对 `text_ja` 用 SentenceSplitter 分句，在 SENTENCE 事件处立即调用 `post_speak`（每句独立 utterance_id，`lang_hint="ja"`）
- [ ] 移除 `_async_post_process` 中的整段 TTS 调用
- [ ] Edge `SpeechPlaybackQueue` 验证排队分句播放效果（间隔 200-500ms 自然停顿）
- [ ] VTS 分句情感跟随：每个日语 SENTENCE 独立推断 emotion_tag，实时 `post_vts(set_expression)`
- [ ] Gateway 语音分段：voice 路径下每句日语独立 TTS → 独立 `post_gateway_reply(msg_type="voice")`
- [ ] 环境变量 `HOST_TTS_SENTENCE_STREAM_ENABLED`（默认 true），可回退到旧的整段模式

---

## 5. 不改什么

- **不动 Skills 基础设施**（SkillRegistry/SkillLoader/ToolExecutorRegistry/Hub/CustomProviders 已经很好）
- **不动子 Agent handler 内部逻辑**（code_agent_handler/translate_agent_handler 保持现状，只改入参 context）
- **不动 ReAct 循环核心逻辑**（react_loop.py 保持 Think→Act→Observe 不变，只改 messages 初始化）
- **不动 prompt 构建管线**（ChatAgent 复用现有完整管线）
- **不动 QQ/微信适配器**（已支持 voice 消息段发送）
- **不动 Gateway Server**（已支持 `msg_type=voice`）
- **不动 Bond/Emotion/Memory 子系统**
- **不动 `input_routes.py` 的外层调度**（只改 ChatRequest 构造参数）
- **废弃 `gateway_orchestrator_handler.py`**（1075 行死代码，运行时无调用者）

---

## 6. 预估收益

| 指标 | Phase XIV v4 | Phase XV |
|------|-------------|----------|
| 内部元数据泄漏风险 | 高（正则兜底） | 零（所有输出经 ChatAgent 人格渲染） |
| 委托误判率 | 高（纯 LLM 判断） | 低（硬规则+LLM 双层） |
| Skills 实际可用数 | 0（主链路不走 Skills） | 57+（三个来源全部通过 DispatchAgent 接入） |
| Gateway 语音消息 | 不可能 | 完整链路 |
| 子 Agent 上下文 | 空白（零历史/零记忆） | 任务级上下文（3 轮历史 + 前次结果 + 情感/Bond） |
| 跨轮次续作能力 | "继续刚才的代码"→ 无法续写 | 通过 recent_turns + previous_agent_result 可续写 |
| 跨平台上下文一致性 | Web 和 QQ 走同一 TopAgent 但缺 Gateway 感知 | 统一 ChatAgent + Gateway 参数注入，QQ 强制中文+回复深度 |
| 文字/语音语言分离 | 同一个 render_target_lang 控制所有通道 | 双轨：文字永远中文 + TTS 永远日语，互不干扰 |
| 用户输入语言影响 | 用户说英文/emoji → 默认日文回复 | 用户说任何语言 → 文字始终中文，TTS 始终日语 |
| 闲聊延迟影响 | 无 | 无（规则路由 <1ms） |
| TTS 首句延迟 | 整段生成+整段合成（~3-5s） | 第一句生成+第一句合成（~1-1.5s） |
| TTS 拟人化 | 一大段一口气念完 | 日语分句独立合成+自然停顿，像真人一句一句说 |
| VTS 表情跟随 | 一次性设置（整段情感） | 逐句情感变化，表情实时跟随 |
| Gateway 回复形式 | 写死文字，永远只回文字 | ReplyFormDecider 智能选择：文字为主，情感场景概率触发语音（文字字幕 + 日语语音双发） |
| Gateway 语音灵动性 | 不存在 | 关系越深语音越多（SOULMATE 30% 概率）；热词场景（晚安/加油/辛苦了）必触发语音 |
| 代码净变化 | — | 新增 ~1100 行，改动 ~500 行，删除 ~1075 行（Gateway Orchestrator） |
