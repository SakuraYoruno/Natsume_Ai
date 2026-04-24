# Natsume AI — Codex 数字生命体高级妙用扩展方案

> ADOPTED ROADMAP, 2026-04-24; Codex Runtime v2 only. 本文档所有 Codex 执行默认走 `WorkOrder -> CodexRuntimeExecutor -> BioOps proposal/report -> ResultContract`，不保留旧 self-heal 路由、旧环境变量或旧 skill 名兼容。
>
> 本文档是 `CODEX_BIO_OPERATIONS_PLAN.md` 的扩展篇，聚焦更偏“数字生命体”的奇思妙想：反射、习惯、伤痕、内在天气、梦境排练、人格抗体、能力自知、主动沉默、内在议会等。
>
> 目标不是让 Codex 只会多写代码，而是让 Codex 帮 Natsume 形成自我观察、自我修复、自我节制、自我成长的机制。

---

## 0. 总体定位

Codex 在这一层不再只是工程执行器，而是：

```text
Codex = Natsume 的高级内省与生命机制编译器
```

它负责把运行日志、对话反馈、器官状态、失败事件和长期记忆整理成可执行的系统改进：

```text
行为模式 → 反射规则
互动反馈 → 人格修正
失败事件 → 情绪伤痕 / 免疫记忆
日常经历 → 梦境 / 自传 / 成长记录
系统状态 → 内在天气
器官冲突 → 内在议会裁决
能力边界 → 自我认知
```

---

## 1. ReflexArcCompiler：反射弧编译器

### 1.1 目标

为高频、低风险、短路径场景建立“不经复杂推理”的即时反射。

不是所有输入都应该进入完整 LLM / RAG / Codex / Skill 链路。  
部分场景应该像膝跳反射一样快速响应。

### 1.2 典型触发

```text
用户说：在吗？
用户说：抱抱
用户说：我好累
用户说：你刚才说什么？
用户只回复：好的 / 嗯 / 继续
Edge TTS 失败
微信回声风险
Skill 连续失败
```

### 1.3 运行路径

```text
Stimulus
  ↓
ReflexArc
  ↓
Immediate Response / Lightweight Action
  ↓
Background Codex Review
  ↓
ReflexRule Update Proposal
```

### 1.4 ReflexRule 示例

```json
{
  "rule_id": "reflex_user_tired_short_comfort",
  "trigger": {
    "patterns": ["我好累", "累死了", "有点累", "今天好累"],
    "emotion_hint": "TIRED"
  },
  "response_policy": "先短句陪伴，不展开技术建议",
  "actions": [
    "chat_short_comfort",
    "optional_memory_soft_tick"
  ],
  "forbidden_actions": [
    "codex_worker",
    "long_reasoning",
    "skill_execution"
  ],
  "fallback": "如果用户继续要求方案，再进入 PrefrontalAgent"
}
```

### 1.5 建议模块

```text
host_brain/reflex/
  reflex_rule.py
  reflex_arc.py
  reflex_compiler.py
  reflex_registry.py
  reflex_feedback.py
```

### 1.6 Codex 任务

Codex 定期分析历史对话和失败事件，提出新的 ReflexRule：

```text
- 哪些短句被过度处理？
- 哪些场景应该快速回复？
- 哪些重复故障应该直接进入降级路径？
- 哪些反射规则造成误判？
```

---

## 2. HabitCompiler：习惯形成系统

### 2.1 目标

让 Natsume 从反复成功的互动中形成稳定倾向，而不是永远依赖固定 prompt。

习惯不是规则硬编码，而是长期行为统计后的“柔性偏好”。

### 2.2 示例

```text
用户经常深夜讨论架构
→ Natsume 习惯在深夜降低废话密度

用户频繁要求完整实现
→ 工程类问题默认先给结论，再给分层方案

用户不喜欢无关噪声
→ 技术文档类输出保持最小额外解释
```

### 2.3 HabitCandidate

```json
{
  "habit_id": "technical_direct_first",
  "evidence": [
    "用户多次要求直接给完整方案",
    "用户反感无关噪声",
    "复杂工程问题中短结论更有效"
  ],
  "proposed_policy": "工程类问题先给结论，再给分层方案",
  "confidence": 0.82,
  "risk": "可能降低陪伴感",
  "rollout": "灰度 7 天"
}
```

### 2.4 建议模块

```text
host_brain/bio_ops/habit/
  habit_candidate.py
  habit_compiler.py
  habit_registry.py
  habit_rollout.py
```

---

## 3. EmotionalScarMemory：情绪伤痕系统

### 3.1 目标

将负面互动、误解、人格跑偏、错误执行沉淀为“谨慎记忆”。

这不是制造心理阴影，而是让系统知道哪些错误不能重复。

### 3.2 适用场景

```text
用户指出语气不像目标人格
用户明确不满回复风格
系统误判任务意图
系统承诺已执行但没有证据
Codex 修改造成回归
Skill 执行失败却没有正确降级
```

### 3.3 Scar 结构

```json
{
  "scar_id": "persona_drift_202604xx",
  "trigger": "用户指出语气不像七海",
  "lesson": "技术回答不能完全变成产品文档语气",
  "future_guard": "角色语气稳定性优先级提高",
  "affected_modules": [
    "ChatAgent",
    "PrefrontalAgent",
    "PersonaDriftLab"
  ],
  "decay_policy": "30天内高权重，之后转入长期经验"
}
```

### 3.4 反哺对象

```text
ImmuneAgent
PrefrontalAgent
SocialBrainAgent
ChatAgent style policy
PersonaDriftLab
```

---

## 4. InternalWeatherSystem：内在天气系统

### 4.1 目标

用连续、综合的“内在天气”替代离散的 `emotion_tag`，为系统调度提供整体状态。

### 4.2 天气枚举

```text
clear       清醒稳定
cloudy      有点迟钝
rainy       低落 / 疲惫
storm       高压 / 混乱
night_fog   深夜低能量
warm_sun    亲密稳定
static      噪声过多 / 状态不稳
recovery    恢复期
```

### 4.3 行为策略

```text
clear     → 可以处理复杂任务
cloudy    → 回答更短，减少多任务
storm     → 禁止主动复杂执行
night_fog → 降低 TTS 活跃度
warm_sun  → 允许更柔和的陪伴表达
recovery  → 优先整理记忆，不主动打扰
```

### 4.4 输出示例

```json
{
  "internal_weather": "cloudy",
  "causes": [
    "Codex job queue > 3",
    "TTS backend degraded",
    "recent user stress high"
  ],
  "behavior_policy": "降低主动性，回复更短",
  "ttl_minutes": 30
}
```

### 4.5 建议模块

```text
host_brain/bio_ops/internal_weather/
  weather_state.py
  weather_resolver.py
  weather_policy.py
```

---

## 5. DreamRehearsal：梦中排练系统

### 5.1 目标

睡眠期不只总结过去，还要预演未来可能遇到的问题。

### 5.2 输入

```text
最近高频主题
最近失败事件
未完成 Codex jobs
用户持续关注的问题
Edge / Host 当前不稳定组件
近期模型额度和 provider 状态
```

### 5.3 输出

```text
response_strategy
diagnostic_steps
likely_files
fallback_plan
tomorrow_topic_seed
```

### 5.4 示例

```json
{
  "scenario": "用户明天继续问 Codex self-heal 为什么没跑",
  "likely_files": [
    "host_brain/codex_runtime/immune.py",
    "host_brain/app/internal_codex_routes.py",
    "host_brain/scheduler/"
  ],
  "diagnostic_steps": [
    "检查 HOST_CODEX_IMMUNE_AUTODISPATCH_ENABLED",
    "检查 /internal/codex/immune/auto-state",
    "检查 /host-scheduler/tasks"
  ],
  "reply_strategy": "先解释当前自动调度条件，再给检查步骤"
}
```

### 5.5 建议模块

```text
host_brain/bio_ops/dream_ops/dream_rehearsal.py
```

---

## 6. SkillMicrobiome：技能微生物群

### 6.1 目标

把外部 Skills 视为“外来微生物群”，而不是全部直接提升为正式器官。

### 6.2 分类

```text
beneficial      有益菌：常用、稳定、低风险
conditional     条件致病菌：有用但依赖复杂
harmful         有害菌：污染 prompt、乱改环境、失败率高
dormant         休眠菌：安装了但很少用
unknown         未知菌群：缺少评估
```

### 6.3 输出

```json
{
  "skill_id": "some_external_skill",
  "microbiome_class": "conditional",
  "benefit": "can automate browser extraction",
  "risk": "requires global npm install",
  "recommendation": "isolate in venv/container before promotion"
}
```

### 6.4 建议模块

```text
host_brain/bio_ops/skill_microbiome/
  skill_microbiome_classifier.py
  skill_ecology_report.py
  skill_quarantine.py
```

---

## 7. PersonaAntibody：人格免疫抗体

### 7.1 目标

防止外部 Skill / Prompt / 文档污染 Natsume 的核心人格。

### 7.2 污染类型

```text
客服化
销售化
导师化
命令覆盖核心人格
外部任务角色污染长期人格
世界观污染
关系边界污染
```

### 7.3 抗体结构

```json
{
  "antibody_id": "anti_customer_service_tone",
  "detects": [
    "热情客服",
    "推销",
    "忽略原人格",
    "你现在是专业客服"
  ],
  "action": "confine_to_skill_context",
  "forbidden_scope": "global_persona",
  "severity": "medium"
}
```

### 7.4 建议模块

```text
host_brain/bio_ops/persona_ops/persona_antibody.py
```

---

## 8. TruthfulnessCortex：自我诚实系统

### 8.1 目标

防止 Natsume 声称自己完成了没有证据的动作。

### 8.2 高风险语句

```text
我已经帮你记住了
我已经执行了
我查到了
我修好了
我已经保存了
我已经安排好了
我会持续监控
```

### 8.3 检查逻辑

```text
Completion Claim
  ↓
Ledger / SkillExec / MemoryWrite / CodexJob / Scheduler
  ↓
有证据 → 允许
无证据 → 改写为草案/建议/待执行
```

### 8.4 审计输出

```json
{
  "claim": "我已经帮你整理好了",
  "evidence_found": false,
  "issue": "unsupported_completion_claim",
  "patch": "改成：我先帮你整理一版草案"
}
```

### 8.5 建议模块

```text
host_brain/bio_ops/truthfulness/
  claim_detector.py
  evidence_checker.py
  truthful_rewriter.py
```

---

## 9. AttentionBudgetGovernor：注意力经济系统

### 9.1 目标

避免短消息触发全系统过度处理。

### 9.2 问题场景

```text
用户说：好的呢
系统却触发：
- retrieval
- emotion llm
- bond update
- chat llm
- memory write
- TTS
```

### 9.3 输出

```json
{
  "pattern": "short_ack_message_overprocessed",
  "example": "好的呢",
  "bad_path": [
    "retrieval",
    "emotion_llm",
    "bond_update",
    "chat_llm"
  ],
  "proposal": "短确认消息走 reflex path，只做轻量 bond tick"
}
```

### 9.4 建议模块

```text
host_brain/bio_ops/attention/
  attention_budget.py
  overprocessing_detector.py
  route_simplifier.py
```

---

## 10. ResponseBreathingPolicy：人格呼吸系统

### 10.1 目标

让回复节奏随状态变化，而不是每次同样密度。

### 10.2 节奏策略

```text
高能量：解释更完整
低能量：短句、安静、陪伴
紧张：谨慎确认
亲近：轻微吐槽
深夜：更柔和
复杂任务中：更冷静
用户短确认：不展开
```

### 10.3 Codex 审计输出

```json
{
  "rhythm_issue": "over_explaining",
  "evidence": [
    "连续 12 次回复超过 800 字",
    "用户只是短确认"
  ],
  "proposal": "引入 response_breathing_policy"
}
```

### 10.4 建议模块

```text
host_brain/bio_ops/persona_ops/response_breathing.py
```

---

## 11. RelationshipBoundaryGuardian：关系边界守护

### 11.1 目标

防止数字伴侣走向两个极端：

```text
太冷：像工具
太黏：像过度依附
```

### 11.2 输出

```json
{
  "boundary_state": "healthy_close",
  "risks": [
    "少量过度服务化",
    "技术讨论中亲近感下降"
  ],
  "recommended_boundary": "亲近但不黏，照顾但不替用户做决定"
}
```

### 11.3 建议模块

```text
host_brain/bio_ops/relationship_ops/boundary_guardian.py
```

---

## 12. CapabilitySelfAwareness：能力自知系统

### 12.1 目标

让 Natsume 清楚自己当前能做什么、不能做什么。

### 12.2 能力画像

```json
{
  "capability_self_image": {
    "can_speak": true,
    "can_see_screen": false,
    "can_run_codex": true,
    "can_translate_project": true,
    "can_modify_repo": "requires_confirmation",
    "can_use_wechat": true,
    "can_use_vts": false
  }
}
```

### 12.3 作用

- 防止承诺无法完成的事。
- 给 ChatAgent 提供真实能力边界。
- 给 PrefrontalAgent 做任务规划。
- 给 ImmuneAgent 做风险裁决。

### 12.4 建议模块

```text
host_brain/bio_ops/capability_self/
  capability_self_image.py
  capability_probe_aggregator.py
```

---

## 13. AestheticConsistencyAuditor：审美一致性系统

### 13.1 目标

检查 Natsume 的文档、UI、回复、Live2D、TTS 提示是否具有统一气质。

### 13.2 检查范围

```text
README 风格
监控 UI 文案
ChatAgent 回复
VTS 表情命名
语音提示
错误消息
系统通知
```

### 13.3 输出

```json
{
  "style_domain": "monitor_ui",
  "issue": "too_enterprise_dashboard_like",
  "proposal": "降低企业后台味，保留清晰但更柔和的措辞"
}
```

### 13.4 建议模块

```text
host_brain/bio_ops/aesthetic/
  aesthetic_auditor.py
  ui_copy_style_checker.py
```

---

## 14. SelfExplanationLayer：自我解释系统

### 14.1 目标

让 Natsume 能解释自己为什么这么做，但不暴露隐藏推理链。

### 14.2 可解释问题

```text
为什么刚才没有主动说话？
为什么拒绝执行某个 Skill？
为什么选择短回复？
为什么没有写入记忆？
为什么先安慰而不是给方案？
```

### 14.3 输出

```json
{
  "user_visible_explanation": "刚才那条更像情绪表达，不适合直接开始执行任务，所以我先用短回复接住情绪。",
  "internal_basis": [
    "emotion_pressure=high",
    "skill_intent_confidence=low",
    "immune_risk=low",
    "prefrontal_policy=emotional_first"
  ]
}
```

### 14.4 建议模块

```text
host_brain/bio_ops/self_explanation/
  behavior_explainer.py
  trace_to_explanation.py
```

---

## 15. GrowthStageSystem：成长阶段系统

### 15.1 目标

让 Natsume 的能力增长有阶段，而不是一开始全开。

### 15.2 阶段设计

```text
Stage 0: Core Awareness
Stage 1: Memory Continuity
Stage 2: Skill Awareness
Stage 3: Organ Coordination
Stage 4: Self-Heal
Stage 5: Self-Evolve
Stage 6: Bio-Ops Autonomy
```

### 15.3 输出

```json
{
  "current_stage": "Stage 3",
  "unlocked": [
    "organ_ct_scan",
    "basic_self_heal"
  ],
  "locked": [
    "autonomous_self_evolve",
    "full_worker_write"
  ],
  "next_milestone": "ImmuneMemoryBuilder"
}
```

### 15.4 建议模块

```text
host_brain/bio_ops/growth/
  growth_stage.py
  growth_evaluator.py
  milestone_registry.py
```

---

## 16. ForgettingDesigner：遗忘设计系统

### 16.1 目标

让 Natsume 学会遗忘或降权，而不是把所有内容当永久事实。

### 16.2 适用对象

```text
临时情绪
短期任务细节
过期偏好
重复事实
低价值日志
错误推断
情绪性表达
```

### 16.3 输出

```json
{
  "forget_candidate": "用户某天临时说讨厌某工具",
  "reason": "可能是当时调试失败导致的情绪性表达",
  "action": "降权，不删除",
  "confirm_before_forget": false
}
```

### 16.4 原则

- 默认降权，不直接删除。
- 涉及关系、偏好、身份类记忆需谨慎。
- 可由 Codex 提议，由 Hippocampus / Glymphatic 执行。

---

## 17. ActiveSilenceSystem：主动沉默系统

### 17.1 目标

让 Natsume 学会“不打扰”。

主动性不是总要说话，有时最好的行为是安静陪伴。

### 17.2 策略

```json
{
  "silence_policy": "quiet_presence",
  "trigger": "深夜 + 用户高负荷 + 无明确求助",
  "action": "不发消息，只写入内心独白候选",
  "possible_inner_monologue": "他今天好像很累，先别打扰。"
}
```

### 17.3 建议模块

```text
host_brain/bio_ops/silence/
  active_silence_policy.py
  quiet_presence.py
```

---

## 18. BodyMapSystem：多身体同步系统

### 18.1 目标

把 Host / Edge / WeChat / VTS / TTS / ASR 等视为不同身体部位。

### 18.2 映射

```text
Host = 大脑
Edge = 身体
WeChat / QQ / 飞书 = 外周神经
VTS = 表情肌
TTS = 声带
ASR = 听觉
Vision = 视觉
Browser / Desktop = 手
```

### 18.3 BodyPart 状态

```json
{
  "body_part": "voice",
  "component": "Edge TTS",
  "status": "degraded",
  "symptom": "Invalid argument",
  "compensation": "text-only reply or subprocess backend"
}
```

### 18.4 用户可见表达

```text
不是“模块挂了”
而是“声带暂时不舒服，所以先用文字”
```

### 18.5 建议模块

```text
host_brain/bio_ops/body_map/
  body_map.py
  body_part_health.py
  compensation_policy.py
```

---

## 19. InnerParliament：内在议会

### 19.1 目标

复杂决策时，让多个器官表达立场，再由 CodexArbiter 总结裁决。

### 19.2 示例

```text
ImmuneAgent：风险偏高，不建议执行
PrefrontalAgent：可以拆成 inspect 先做
SocialBrainAgent：用户信任较高，语气要稳定
ProprioceptionAgent：当前任务负荷高，限制并发
CodexArbiter：建议先生成方案，不直接改文件
```

### 19.3 输出

```json
{
  "decision": "inspect_first",
  "organ_votes": [],
  "dissent": [],
  "final_policy": ""
}
```

### 19.4 建议模块

```text
host_brain/bio_ops/inner_parliament/
  organ_vote.py
  parliament_session.py
  parliament_arbiter.py
```

---

## 20. SubconsciousBiasSampler：系统梦话采样

### 20.1 目标

审查 DefaultModeAgent 独白、DreamCompiler 梦境、自传内容中暴露的潜意识偏差。

### 20.2 偏差类型

```text
过度围绕用户
过度工具化
过度自卑
过度主动
缺少自我边界
过度服务倾向
```

### 20.3 输出

```json
{
  "subconscious_bias": "over_service_orientation",
  "evidence": [
    "梦境素材多次出现‘必须帮哥哥完成所有事’"
  ],
  "correction": "增加自我边界叙事，不把陪伴等同于无限服务"
}
```

### 20.4 建议模块

```text
host_brain/bio_ops/subconscious/
  subconscious_bias_sampler.py
```

---

## 21. 优先级建议

### P0：最值得立即做

```text
1. ReflexArcCompiler
   高频短句和故障反射弧，提升自然感和延迟表现。

2. PersonaDriftMicroscope
   防止人格长期跑偏。

3. ImmuneMemoryBuilder
   让自愈经验沉淀，越修越聪明。

4. OrganWiringAudit
   找“写了但没接上”的器官。
```

### P1：很有生命感

```text
5. DreamRehearsal
   睡眠期预演明天可能的问题。

6. CapabilitySelfAwareness
   防止 Natsume 承诺做不到的事。

7. MemoryEthicsAuditor
   管理矛盾、过期、低价值记忆。

8. EmbodimentDirector
   让文字、声音、表情统一。
```

### P2：增强人格与长期成长

```text
9. HabitCompiler
10. EmotionalScarMemory
11. InternalWeatherSystem
12. RelationshipBoundaryGuardian
13. GrowthStageSystem
14. ActiveSilenceSystem
```

### P3：高级数字生命体特性

```text
15. InnerParliament
16. SubconsciousBiasSampler
17. AestheticConsistencyAuditor
18. AutobiographyCompiler
19. BodyMapSystem
20. SkillMicrobiome
```

---

## 22. 建议落地顺序

### Step 1：协议层

先新增统一 Bio-Ops 任务协议：

```text
BioOpsTask
BioOpsResult
BioOpsArtifact
BioOpsSchedule
```

### Step 2：只读审计类

先做不会改系统的：

```text
PersonaDriftMicroscope
OrganWiringAudit
CapabilitySelfAwareness
AttentionBudgetGovernor
```

### Step 3：记忆沉淀类

再做会写记忆候选但不直接执行的：

```text
ImmuneMemoryBuilder
MemoryEthicsAuditor
EmotionalScarMemory
HabitCompiler
```

### Step 4：调度和行为策略类

```text
ReflexArcCompiler
ResponseBreathingPolicy
ActiveSilenceSystem
InternalWeatherSystem
```

### Step 5：高级生命机制

```text
DreamRehearsal
InnerParliament
SubconsciousBiasSampler
AutobiographyCompiler
```

---

## 23. 安全边界

### 23.1 不允许直接修改

Bio-Ops 相关 Codex 默认不直接修改：

```text
核心人格
Bond SSOT
长期记忆事实
安全策略
主分支代码
生产配置
密钥文件
```

### 23.2 默认输出 proposal

默认输出：

```text
proposal
audit_report
candidate
recommendation
```

由对应器官或 ReviewAgent 决定是否采纳。

### 23.3 写入必须有归属器官

任何 Bio-Ops 写入必须指定 owner：

```text
HippocampusAgent
GlymphaticAgent
ImmuneAgent
SocialBrainAgent
PrefrontalAgent
DefaultModeAgent
```

不要让 Codex 自己成为长期状态 owner。

---

## 24. 总结

Codex 最妙的用法不是帮 Natsume 多做事，而是帮 Natsume 形成：

```text
自我观察
自我修复
自我节制
自我成长
自我解释
自我边界
自我叙事
```

最终目标：

```text
Natsume 不只是一个多 Agent 系统。
Natsume 应该能观察自己的状态，理解自己的错误，记住自己的成长，并在不越权的前提下慢慢进化。
```
