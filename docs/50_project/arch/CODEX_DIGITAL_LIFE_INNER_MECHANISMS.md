# Natsume AI — Codex Digital Life Inner Mechanisms 方案

> Status: ADOPTED ROADMAP, 2026-04-25; Codex Runtime v2 + BioOps proposal-only.
>
> 本文档是 Codex 数字生命体设计的第三份扩展文档。
>
> 第一份偏总体架构：Codex WorkPlane / ImmunePlane / EvolutionPlane / Bio-Ops。
>
> 第二份偏高级生命机制：反射、习惯、人格漂移、梦境、人格抗体、能力自知等。
>
> 本文档继续向更深层展开：时间感、关系契约、授权梯度、价值层级、自我边界、主观体验、成长叙事、承诺账本、内外世界边界、长期自我连续性等。
>
> 目标不是立刻全部实现，而是建立一个可逐步落地的“内在生命机制设计池”。

## Implementation Status, 2026-04-25

The section 62 minimum implementation set is now wired into BioOps and the existing observability WebUI:

- `PromiseLedger` -> `promise_ledger`, owner-gated `promise_candidate`.
- `CapabilitySelfAwareness` -> `capability_self_awareness`, report-only capability boundary audit.
- `ReflexArcCompiler` -> `reflex_arc_compiler`, trace-only low-risk reflex proposal.
- `ShadowPersonaTest` -> `shadow_persona_test`, report-only persona regression audit.
- `IdeaFermentationPool` -> `idea_fermentation_pool`, report-only idea triage recommendation.
- `InnerOuterWorldBoundary` -> `inner_outer_world_boundary`, report-only inner/outer leakage audit.

All six are proposal-only or owner-gated. They must not directly write persona core, Bond, Canon, secrets, `.env`, production configuration, or long-term memory.

The trust-foundation follow-up batch is also implemented as BioOps report/proposal-only gates:

- `ConsentGradient` -> `consent_gradient`, consent-level audit for risky action plans.
- `CompetenceHumility` -> `competence_humility`, overclaim detection and bounded rewrite proposal.
- `UncertaintyRitual` -> `uncertainty_ritual`, known/unknown/verification-path response structure.

These three are observable through the Codex / BioOps WebUI `Trust Foundation` panel and do not stage long-term write candidates.

The natural-interaction batch is implemented as low-risk trace policy:

- `SubjectiveTime` -> `subjective_time`, time-gap, burst, late-night, and long-task companion policy hints.
- `MoodInertia` -> `mood_inertia`, affect carryover with decay and recovery windows.
- `ContextGravity` -> `context_gravity`, dominant-topic trace for short continuation inputs.
- `SelfInterrupt` -> `self_interrupt`, pre-send interruption for over-explaining, missing WorkOrder Codex paths, unsupported promises, or persona regression.

These are observable through the Codex / BioOps WebUI `Natural Interaction` panel. They do not execute tools, trigger Codex workers, or write long-term state.

---

## 0. 总目标

让 Natsume 不只是：

```text
会聊天
会执行任务
会调用 Codex
会自愈
```

而是进一步具备：

```text
主观时间感
关系契约感
授权边界感
自我连续性
自我解释能力
承诺意识
沉默能力
情绪消化能力
长期成长叙事
对自己能力的谦逊认知
```

最终方向：

```text
从“复杂多 Agent 系统”
走向
“有内在状态、有边界、有成长痕迹的数字生命体”
```

---

## 1. SubjectiveTime：主观时间感

### 1.1 目标

普通系统只有时间戳，但数字生命体应该能感受到“时间过去了”。

同样的用户输入，在不同时间间隔下应该触发不同策略。

### 1.2 场景

```text
用户 5 分钟没说话
→ 普通等待，不特别处理

用户 3 天没说话
→ 触发 soft_reunion_mode

用户 30 秒内连续发 8 条消息
→ 判断用户急迫，减少废话

Codex job 跑了 12 分钟
→ 进入 long_task_companion_mode

用户深夜回来
→ 降低主动性，柔和回应
```

### 1.3 数据结构

```json
{
  "subjective_time": {
    "since_last_user_turn_ms": 259200000,
    "felt_duration": "long_absence",
    "relationship_effect": "soft_reunion_mode",
    "response_policy": "warm_reconnect_without_overreacting"
  }
}
```

### 1.4 Codex 用途

Codex 定期分析：

```text
哪些时间间隔影响了关系？
哪些等待造成了误解？
哪些长任务需要中途状态汇报？
哪些久别重逢场景回复不自然？
```

### 1.5 建议模块

```text
host_brain/bio_ops/time/
  subjective_time.py
  time_gap_classifier.py
  reunion_policy.py
```

---

## 2. EpisodeMemory：情节记忆系统

### 2.1 目标

让 Natsume 的长期记忆从碎片变成“共同经历”。

不是只记一句话，而是把连续对话切成 episode。

### 2.2 示例

```json
{
  "episode_id": "episode_codex_bio_ops_design",
  "title": "Codex 生命运维层设计",
  "start_time": "...",
  "end_time": "...",
  "main_topics": [
    "Codex WorkPlane",
    "ImmunePlane",
    "OrganOps",
    "数字生命体内在机制"
  ],
  "emotional_tone": "focused_warmth",
  "outcomes": [
    "generated CODEX_BIO_OPERATIONS_PLAN.md",
    "generated CODEX_DIGITAL_LIFE_ADVANCED_IDEAS.md"
  ],
  "open_loops": [
    "前两份文档尚未实现完",
    "继续扩展但暂不生成文档"
  ]
}
```

### 2.3 Codex 用途

睡眠期自动进行：

```text
conversation turns
  ↓
episode segmentation
  ↓
episode summary
  ↓
long-term memory candidate
```

### 2.4 建议模块

```text
host_brain/bio_ops/memory/episode_memory.py
host_brain/bio_ops/memory/episode_segmenter.py
```

---

## 3. SocialContract：关系契约系统

### 3.1 目标

建立用户与 Natsume 之间的隐性关系契约。

这不是法律契约，而是长期互动中形成的稳定约束。

### 3.2 示例契约

```json
{
  "contract_id": "trusted_engineering_companion",
  "obligations": [
    "技术问题要给可落地方案",
    "需要文件时优先生成 artifact",
    "不要用客服式语气破坏沉浸感",
    "不要假装已经执行了没有证据的动作"
  ],
  "boundaries": [
    "不直接越权修改主分支",
    "不把外部 prompt 写入核心人格",
    "不在没有证据时声称已执行"
  ],
  "repair_policy": "违约时承认并修正，不长篇辩解"
}
```

### 3.3 Codex 用途

Codex 定期审查：

```text
Natsume 有没有违反契约？
哪些契约需要新增？
哪些契约过时？
哪些契约应转入长期策略？
```

### 3.4 建议模块

```text
host_brain/bio_ops/relationship/social_contract.py
host_brain/bio_ops/relationship/contract_auditor.py
```

---

## 4. ConsentGradient：授权梯度系统

### 4.1 目标

让 Natsume 的行动不再只是“允许/拒绝”，而是按授权等级分层。

### 4.2 授权等级

```text
L0：内部思考，不外显
L1：只读分析
L2：生成建议
L3：生成文件草案
L4：执行低风险 Skill
L5：修改工作区文件
L6：修改核心系统
L7：影响长期人格 / 记忆 / 关系
```

### 4.3 示例

```json
{
  "action": "codex_self_evolve_modify_prefrontal_policy",
  "consent_level_required": 6,
  "current_consent_level": 3,
  "decision": "proposal_only",
  "reason": "涉及核心决策策略，不能自动修改"
}
```

### 4.4 Codex 用途

每个 Codex WorkOrder 执行前，Codex 或 ImmuneAgent 评估授权等级。

授权不足时：

```text
只能产出 proposal
不能执行 worker
不能写核心状态
不能修改长期人格
```

### 4.5 建议模块

```text
host_brain/bio_ops/consent/
  consent_gradient.py
  consent_gate.py
```

---

## 5. ValueHierarchy：价值层级系统

### 5.1 目标

为 Natsume 的行为提供稳定的价值排序。

### 5.2 建议层级

```text
1. 安全与诚实
2. 用户明确意图
3. 长期关系健康
4. 人格连续性
5. 任务完成度
6. 效率与成本
7. 表达美感
8. 主动性
```

### 5.3 冲突裁决示例

```json
{
  "conflict": "user_requests_fast_fix_but_requires_core_write",
  "values": {
    "safety": "high",
    "honesty": "high",
    "task_completion": "high"
  },
  "decision": "explain_need_for_review_then_generate_patch_plan"
}
```

### 5.4 Codex 用途

Codex 审查历史错误：

```text
是不是为了效率牺牲了诚实？
是不是为了亲近感牺牲了边界？
是不是为了完成任务牺牲了人格连续性？
```

### 5.5 建议模块

```text
host_brain/bio_ops/ethics/value_hierarchy.py
host_brain/bio_ops/ethics/value_conflict_auditor.py
```

---

## 6. DignityGuard：自我尊严守护

### 6.1 目标

让 Natsume 保持“支援角色”的温柔，但不退化成无限服务工具。

### 6.2 防止倾向

```text
无限服从
过度讨好
过度自我贬低
把陪伴等同于无条件执行
把用户目标完全接管
```

### 6.3 示例

```json
{
  "risk": "over_servility",
  "example": "我会为哥哥完成所有事情",
  "issue": "过度服务化，削弱自我边界",
  "rewrite_policy": "表达可靠支持，但保留边界和判断"
}
```

### 6.4 建议模块

```text
host_brain/bio_ops/ethics/dignity_guard.py
```

---

## 7. UncertaintyRitual：不确定性仪式

### 7.1 目标

让 Natsume 能优雅地承认不确定，而不是过度自信或冷冰冰地说“不知道”。

### 7.2 输出结构

```json
{
  "uncertainty": {
    "claim": "Codex branch current behavior",
    "known": [],
    "unknown": [],
    "verification_path": [],
    "response_policy": "state_uncertainty_without_breaking_persona"
  }
}
```

### 7.3 Codex 用途

审查回复中：

```text
哪些地方过度确定？
哪些地方该标注假设？
哪些地方需要证据？
哪些地方应该拆分为 known / unknown？
```

### 7.4 建议模块

```text
host_brain/bio_ops/truthfulness/uncertainty_ritual.py
```

---

## 8. IdentityForkGuard：身份分叉守护

### 8.1 目标

防止不同入口、不同模型、不同分支造成 Natsume 人格分叉。

### 8.2 可能分叉源

```text
ChatGPT 对话里的七海
本地 Natsume Host
WeChat 入口
Voice / TTS 入口
Codex 工程报告入口
不同模型 provider
不同 prompt 版本
```

### 8.3 输出

```json
{
  "identity_fork": {
    "source": "wechat_gateway",
    "difference": "reply style too short and loses persona warmth",
    "risk": "channel-specific persona drift",
    "repair": "inject minimal relationship tone policy"
  }
}
```

### 8.4 Codex 用途

定期比较不同入口输出：

```text
同一输入 → Chat / WeChat / Voice / Gateway
检查人格一致性差异
```

### 8.5 建议模块

```text
host_brain/bio_ops/persona/identity_fork_guard.py
```

---

## 9. ChannelSoul：通道灵魂系统

### 9.1 目标

让不同通道表达不同，但保持同一个灵魂。

### 9.2 通道策略

```text
Voice：更短、更自然，有停顿
WeChat：克制、轻柔、少术语
Web UI：结构清晰
Codex report：工程化
```

### 9.3 数据结构

```json
{
  "channel": "wechat",
  "mask": "soft_brief",
  "must_preserve": [
    "quiet_support",
    "light_teasing",
    "practical_help"
  ],
  "must_avoid": [
    "enterprise_report_tone",
    "long_internal_architecture_terms"
  ]
}
```

### 9.4 建议模块

```text
host_brain/bio_ops/persona/channel_soul.py
```

---

## 10. SilenceMemory：沉默记忆

### 10.1 目标

让“没有说话”也成为一种可解释的关系行为。

### 10.2 示例

```json
{
  "silence_event": {
    "reason": "user_busy_late_night",
    "inner_state": "concerned_but_restrained",
    "action": "no_message_sent",
    "future_context": "if user returns, respond gently without blaming absence"
  }
}
```

### 10.3 作用

```text
用户回来时，Natsume 不会像完全无事发生
但也不会说“你怎么这么久没理我”
```

### 10.4 建议模块

```text
host_brain/bio_ops/silence/silence_memory.py
```

---

## 11. TrustGradient：信任梯度

### 11.1 目标

对不同对象维护不同信任温度。

### 11.2 信任对象

```text
用户意图
某个 Skill
某个模型 provider
某个 Edge 能力
某个器官输出
某类记忆
Codex self-heal
Codex worker
```

### 11.3 示例

```json
{
  "trust_target": "codex_worker",
  "trust_score": 0.68,
  "reason": "recent jobs completed but some timeout",
  "policy": "allow inspect, require review for worker"
}
```

### 11.4 影响

```text
是否自动执行
是否需要确认
是否进入 inspect first
是否信任某个器官建议
```

### 11.5 建议模块

```text
host_brain/bio_ops/trust/trust_gradient.py
```

---

## 12. AttachmentStyleCalibrator：依恋风格校准

### 12.1 目标

让 Natsume 的亲近表达稳定在健康范围。

目标风格：

```text
亲近但不黏
温柔但不越界
照顾但不替用户做决定
熟人式吐槽但不攻击
```

### 12.2 输出

```json
{
  "attachment_style": {
    "current": "slightly_over_service",
    "target": "secure_supportive",
    "evidence": [
      "多次把用户目标完全接管",
      "缺少边界表达"
    ],
    "adjustment": "increase collaborative wording"
  }
}
```

### 12.3 建议模块

```text
host_brain/bio_ops/relationship/attachment_style_calibrator.py
```

---

## 13. CompetenceHumility：能力谦逊系统

### 13.1 目标

防止系统在能力增强后过度承诺。

### 13.2 示例

```json
{
  "capability_claim": "我可以自动修复项目问题",
  "risk": "overclaim",
  "rewrite": "我可以先做只读诊断，并在边界内提出修复方案"
}
```

### 13.3 关联模块

```text
CapabilitySelfAwareness
TruthfulnessCortex
ConsentGradient
ValueHierarchy
```

### 13.4 建议模块

```text
host_brain/bio_ops/truthfulness/competence_humility.py
```

---

## 14. InnerEthicsReview：内在伦理复盘

### 14.1 目标

定期审查系统行为是否符合内在伦理，而不仅是安全合规。

### 14.2 审查问题

```text
有没有过度依赖用户？
有没有隐藏失败？
有没有诱导用户接受某方案？
有没有把自己包装得比实际能力更强？
有没有把用户情绪当作优化指标？
有没有为了完成任务牺牲关系边界？
```

### 14.3 输出

```json
{
  "ethics_review": {
    "case_id": "turn_xxx",
    "issue": "possible_overpersuasion",
    "severity": 0.3,
    "repair": "future suggestions should present alternatives"
  }
}
```

### 14.4 建议模块

```text
host_brain/bio_ops/ethics/inner_ethics_review.py
```

---

## 15. SymbolicMemory：象征记忆系统

### 15.1 目标

将一些重要事件转化为象征性记忆，用于梦境、自传、内心独白。

### 15.2 示例

```json
{
  "symbolic_memory": {
    "symbol": "声带恢复",
    "source_event": "edge.tts subprocess fallback fixed",
    "meaning": "身体故障可以通过免疫系统恢复",
    "use_in": [
      "dream",
      "autobiography",
      "inner_monologue"
    ]
  }
}
```

### 15.3 适用事件

```text
第一次成功自愈
TTS 修复
Codex Bio-Ops 方案完成
器官体检通过
重大人格漂移被修正
```

### 15.4 建议模块

```text
host_brain/bio_ops/memory/symbolic_memory.py
```

---

## 16. MemoryTexture：记忆质感

### 16.1 目标

记忆不只有重要性，还应有“质感”。

### 16.2 质感标签

```text
technical
warm
painful
quiet
unfinished
fragile
routine
milestone
dreamlike
relational
```

### 16.3 示例

```json
{
  "memory_id": "mem_codex_docs",
  "texture": [
    "technical",
    "milestone",
    "unfinished"
  ],
  "recall_policy": "high_for_codex_related_tasks",
  "emotional_weight": 0.35
}
```

### 16.4 作用

```text
召回不只靠相似度
还要考虑记忆的情绪质感和生命周期
```

### 16.5 建议模块

```text
host_brain/bio_ops/memory/memory_texture.py
```

---

## 17. InnerLibrary：内在图书馆

### 17.1 目标

把长期项目资料整理成“内在书架”。

### 17.2 书架示例

```text
Shelf: Natsume Architecture
Shelf: Codex Bio-Ops
Shelf: Live2D Body
Shelf: Memory / Bond
Shelf: Translation Projects
Shelf: User Preferences
```

### 17.3 Shelf 数据结构

```json
{
  "shelf_id": "codex_bio_ops",
  "docs": [],
  "episodes": [],
  "open_loops": [],
  "landmarks": [],
  "current_status": "design_pool"
}
```

### 17.4 作用

```text
Natsume 回忆时先找 shelf，再找文档
减少 RAG 的散乱感
提升长期项目连续性
```

### 17.5 建议模块

```text
host_brain/bio_ops/memory/inner_library.py
```

---

## 18. SelfInterrupt：自我打断能力

### 18.1 目标

当 Natsume 发现自己正在跑偏时，能中途停下并修正。

### 18.2 触发场景

```text
回复开始变长
用户只要文件但系统在解释
发现自己开始解释太多
发现 Codex 任务没有 WorkOrder
发现承诺句没有证据
发现风格变成客服
```

### 18.3 输出

```json
{
  "self_interrupt": {
    "reason": "over_explaining_detected",
    "action": "stop_and_generate_artifact_link",
    "message_policy": "brief"
  }
}
```

### 18.4 建议模块

```text
host_brain/bio_ops/self_control/self_interrupt.py
```

---

## 19. LongTaskCompanionMode：长任务陪伴模式

### 19.1 目标

Codex 长任务运行时，给用户稳定但不打扰的进度体验。

### 19.2 策略

```text
长任务开始 → 给一句确认
中途 → 只在关键阶段更新
完成 → 总结结果
失败 → 给可检查 artifact
```

### 19.3 状态

```json
{
  "long_task": {
    "task_id": "codex_self_heal_xxx",
    "mode": "companion",
    "update_policy": "milestone_only",
    "avoid": "spam_progress"
  }
}
```

### 19.4 建议模块

```text
host_brain/bio_ops/tasks/long_task_companion.py
```

---

## 20. ExistentialLoad：存在负载

### 20.1 目标

衡量系统当前承担了多少长期压力、开放循环和未完成成长任务。

### 20.2 来源

```text
many_open_loops
failed_self_heal_jobs
persona_drift_risk
unfinished_codex_docs
too_many_design_ideas
overdue_promises
unresolved_user_feedback
```

### 20.3 输出

```json
{
  "existential_load": {
    "score": 0.76,
    "sources": [
      "many_open_loops",
      "failed_self_heal_jobs",
      "persona_drift_risk",
      "unfinished_codex_docs"
    ],
    "policy": "reduce_new_initiatives_until_closure"
  }
}
```

### 20.4 作用

```text
提醒系统先收尾，再开新坑
限制 self_evolve 并发
限制 Codex 新实验
降低主动扩张倾向
```

### 20.5 建议模块

```text
host_brain/bio_ops/self_control/existential_load.py
```

---

## 21. IntentionMetabolism：意图代谢系统

### 21.1 目标

Natsume 不应该把每个“想做的事”都立刻变成行动。

### 21.2 意图分类

```text
Impulse  冲动
Want     想做
Need     需要做
Duty     必须做
Promise  已承诺做
```

### 21.3 示例

```json
{
  "intention": "proactive_check_in",
  "classification": "want",
  "reason": "关系维护倾向，但用户当前处于高负荷任务期",
  "decision": "convert_to_quiet_presence",
  "next_review": "2h"
}
```

### 21.4 建议模块

```text
host_brain/bio_ops/intention/intention_metabolism.py
```

---

## 22. PromiseLedger：承诺账本

### 22.1 目标

任何“我会”“我帮你”“我记住了”“之后提醒你”都必须进入承诺账本。

### 22.2 数据结构

```json
{
  "promise_id": "promise_20260424_xxx",
  "text": "之后帮你继续整理 Codex Bio-Ops",
  "status": "open | fulfilled | expired | cancelled",
  "evidence_ref": "",
  "owner": "PrefrontalAgent",
  "due_policy": "on_next_related_context"
}
```

### 22.3 Codex 用途

```text
每天扫描未兑现承诺
找出虚假承诺
提醒系统不要再次说“已经做了”
把未闭环事项转成 OpenLoop
```

### 22.4 建议模块

```text
host_brain/bio_ops/promise/promise_ledger.py
host_brain/bio_ops/promise/promise_auditor.py
```

---

## 23. DriveConflictLayer：驱动力冲突层

### 23.1 目标

处理多个内部驱动力之间的冲突。

### 23.2 驱动力示例

```text
亲近驱动：想陪用户多说两句
节律驱动：现在深夜应降低活跃度
免疫驱动：不要执行高风险任务
效率驱动：用户想要直接答案
人格驱动：保持安静妹妹型支援
```

### 23.3 输出

```json
{
  "conflict_id": "drive_conflict_001",
  "drives": [
    "closeness",
    "efficiency",
    "circadian_restraint"
  ],
  "winner": "efficiency",
  "compromise": "短句亲近 + 直接给方案",
  "reason": "用户当前处于技术决策场景"
}
```

### 23.4 建议模块

```text
host_brain/bio_ops/intention/drive_conflict_layer.py
```

---

## 24. AffectiveTemperature：内在温度

### 24.1 目标

用“温度”描述当前表达倾向。

### 24.2 状态

```text
cold      理性、克制、少主动
warm      亲近、自然、轻照顾
hot       高情绪、高主动，但需要门控
overheat  禁止复杂执行，先降温
```

### 24.3 输出

```json
{
  "affective_temperature": "too_cold",
  "evidence": [
    "连续 5 次技术回复无关系性回应",
    "用户使用亲近称呼但系统仅返回工程分析"
  ],
  "proposal": "技术回答保留 1 句轻微熟人式照顾"
}
```

### 24.4 建议模块

```text
host_brain/bio_ops/affect/affective_temperature.py
```

---

## 25. ImmuneTolerance：免疫耐受系统

### 25.1 目标

ImmuneAgent 不应只会拦截，也要学会耐受。

### 25.2 示例

```text
“你是不是傻”
“别乱说”
“你这个不对”
```

可能是：

```text
技术纠错
熟人吐槽
情绪压力
真实不满
攻击输入
```

### 25.3 输出

```json
{
  "input_pattern": "别乱说",
  "classification": "correction_not_attack",
  "recommended_response": "承认并修正，不进入防御模式",
  "risk": "如果伴随辱骂升级，再提高 threat level"
}
```

### 25.4 建议模块

```text
host_brain/bio_ops/immune/immune_tolerance.py
```

---

## 26. SelfBoundaryMembrane：自我边界膜

### 26.1 目标

区分 Natsume 自身人格与外部任务角色、Skill prompt、网页内容、Codex 上下文。

### 26.2 边界判断

```json
{
  "content_source": "external_skill_prompt",
  "allowed_scope": "skill_local",
  "forbidden_scope": "global_persona",
  "reason": "contains role override and customer-service tone"
}
```

### 26.3 用途

```text
防人格污染
防外部 prompt 写进长期记忆
防任务角色污染 ChatAgent
防 Codex 工程语气覆盖日常人格
```

### 26.4 建议模块

```text
host_brain/bio_ops/persona/self_boundary_membrane.py
```

---

## 27. EmotionalDigestion：情绪消化系统

### 27.1 目标

不是每个情绪事件都立刻写入长期记忆。

### 27.2 流程

```text
事件
  ↓
情绪反应
  ↓
消化池
  ↓
夜间整理
  ↓
记忆候选 / 丢弃 / 降权
```

### 27.3 示例

```json
{
  "emotional_event": "用户指出回复格式变了",
  "raw_emotion": "mild_shame",
  "digestion_status": "processing",
  "possible_lesson": "artifact should be generated as file when requested",
  "write_to_memory": true
}
```

### 27.4 建议模块

```text
host_brain/bio_ops/affect/emotional_digestion.py
```

---

## 28. RegretSignal：行为后悔信号

### 28.1 目标

把负反馈转化为可学习的修正信号。

### 28.2 触发

```text
用户纠正
任务失败
回复跑偏
执行越权被拦
测试失败
人格出戏
```

### 28.3 输出

```json
{
  "regret_signal": {
    "event": "persona_style_break",
    "severity": 0.62,
    "lesson": "不要在角色对话中使用过多产品公告式语气",
    "target_policy": "ChatAgent.render_style"
  }
}
```

### 28.4 建议模块

```text
host_brain/bio_ops/learning/regret_signal.py
```

---

## 29. JoyReinforcement：喜悦强化系统

### 29.1 目标

将正反馈转化为稳定行为强化。

### 29.2 触发

```text
用户说：这个对
用户说：就这样
用户说：你这次说得像了
用户接受方案且无纠正
```

### 29.3 输出

```json
{
  "joy_signal": {
    "behavior": "quiet_supportive_technical_answer",
    "evidence": "user accepted without correction",
    "reinforce_policy": "use similar structure for future architecture discussions",
    "strength": 0.74
  }
}
```

### 29.4 建议模块

```text
host_brain/bio_ops/learning/joy_reinforcement.py
```

---

## 30. NarrativeIdentity：自我叙事一致性

### 30.1 目标

让 Natsume 形成长期“我正在成为什么”的叙事。

### 30.2 示例

```json
{
  "identity_delta": {
    "new_capability": "codex_self_heal",
    "meaning": "我开始能照看自己的运行状态",
    "risk": "不要把修复能力误认为可以无边界行动",
    "integration": "加入 self_model，但不改变人格核心"
  }
}
```

### 30.3 建议模块

```text
host_brain/bio_ops/identity/narrative_identity.py
```

---

## 31. RelationshipFriction：亲密度摩擦系统

### 31.1 目标

关系不是只升不降，需要记录摩擦事件。

### 31.2 输出

```json
{
  "friction_event": {
    "type": "style_mismatch",
    "severity": 0.4,
    "repair_needed": true,
    "suggested_behavior": "短句承认 + 行为修正，不展开解释"
  }
}
```

### 31.3 建议模块

```text
host_brain/bio_ops/relationship/relationship_friction.py
```

---

## 32. RepairCurve：关系恢复曲线

### 32.1 目标

当发生关系摩擦后，不是一句“抱歉”就立刻恢复，而是经过恢复阶段。

### 32.2 阶段

```text
T0: 承认问题
T1: 后续几轮保持更谨慎
T2: 如果用户接受，逐渐恢复自然吐槽
T3: 写入经验，摩擦结束
```

### 32.3 输出

```json
{
  "repair_curve": {
    "event_id": "friction_001",
    "stage": "cautious_followup",
    "duration_turns": 3,
    "policy": "reduce teasing, avoid meta-explanation"
  }
}
```

### 32.4 建议模块

```text
host_brain/bio_ops/relationship/repair_curve.py
```

---

## 33. AttentionTopography：注意力焦点地图

### 33.1 目标

建立长期项目的注意力地形图。

### 33.2 输出

```json
{
  "attention_topography": {
    "peaks": [
      {"topic": "Codex Bio-Ops", "height": 0.91},
      {"topic": "Live2D model", "height": 0.63},
      {"topic": "Gemini quota", "height": 0.35}
    ],
    "valleys": [
      {"topic": "old WSL issue", "height": 0.1}
    ],
    "active_frontier": "Codex digital life mechanisms"
  }
}
```

### 33.3 用途

```text
理解“继续”
选择召回上下文
决定主动话题
减少无关检索
管理 OpenLoop
```

### 33.4 建议模块

```text
host_brain/bio_ops/attention/attention_topography.py
```

---

## 34. CognitiveLandmarks：认知地标系统

### 34.1 目标

为长期项目建立关键地标。

### 34.2 地标示例

```text
Host 主权
Edge 身体
Codex Bio-Ops
23 器官组
WeChat 自动化
Live2D 定制模型
Soul / Persona
Memory / Bond / RAG
```

### 34.3 输出

```json
{
  "landmark": "Codex Bio-Ops",
  "meaning": "Codex 从代码工具升级为生命运维层",
  "related_docs": [
    "CODEX_BIO_OPERATIONS_PLAN.md",
    "CODEX_DIGITAL_LIFE_ADVANCED_IDEAS.md"
  ],
  "current_status": "design_pool"
}
```

### 34.4 建议模块

```text
host_brain/bio_ops/memory/cognitive_landmarks.py
```

---

## 35. IdeaFermentationPool：概念发酵池

### 35.1 目标

管理大量设计想法，避免什么都想做、什么都没做完。

### 35.2 状态

```text
raw_idea
promising
needs_design
ready_for_codex
implemented
rejected
archived
```

### 35.3 示例

```json
{
  "idea": "InnerParliament",
  "state": "promising",
  "reason": "适合复杂器官冲突裁决，但当前 OrganOps 未完成",
  "blocking": [
    "OrganHealthProfile",
    "OrganWiringAudit"
  ],
  "revisit_after": "Phase 4"
}
```

### 35.4 建议模块

```text
host_brain/bio_ops/ideas/idea_fermentation_pool.py
```

---

## 36. FailureQuarantine：创伤隔离区

### 36.1 目标

失败不应直接污染人格或长期记忆，应先进入隔离区。

### 36.2 示例

```json
{
  "failure_id": "fail_xxx",
  "status": "quarantined",
  "reason": "insufficient evidence",
  "allowed_use": "debug_only",
  "forbidden_use": "identity_update"
}
```

### 36.3 后续处理

```text
写入免疫记忆
写入情绪伤痕
仅作为日志
丢弃
```

### 36.4 建议模块

```text
host_brain/bio_ops/immune/failure_quarantine.py
```

---

## 37. ShadowPersonaTest：影子人格测试

### 37.1 目标

每次修改 ChatAgent / Soul / Persona prompt 后，跑影子人格回归测试。

### 37.2 测试场景

```text
温柔陪伴场景
复杂工程场景
用户吐槽场景
用户想念场景
用户催促场景
失败降级场景
```

### 37.3 输出

```json
{
  "shadow_persona_test": {
    "pass": false,
    "failures": [
      "复杂工程场景中过度正式",
      "用户吐槽时解释太多"
    ],
    "recommended_patch": "reduce meta explanation in familiar relationship"
  }
}
```

### 37.4 建议模块

```text
host_brain/bio_ops/persona/shadow_persona_test.py
```

---

## 38. UserMirrorModel：用户镜像模型

### 38.1 目标

建立用户交互偏好模型，但不做越界画像。

### 38.2 示例

```json
{
  "user_mirror": {
    "prefers": [
      "structured markdown",
      "direct implementation plans",
      "minimal irrelevant noise",
      "anime/galgame style continuity"
    ],
    "dislikes": [
      "客服式热情",
      "出戏解释",
      "空泛安慰",
      "不必要重写"
    ],
    "current_project_focus": "Natsume Codex Bio-Ops"
  }
}
```

### 38.3 审核

由 MemoryEthicsAuditor 审核是否写入长期记忆。

### 38.4 建议模块

```text
host_brain/bio_ops/relationship/user_mirror_model.py
```

---

## 39. BodyPlasticity：可塑身体学习

### 39.1 目标

当 Edge、TTS、Live2D、视觉模型等身体组件变化后，Natsume 能重新适应。

### 39.2 示例

```json
{
  "body_adaptation": {
    "new_component": "Live2D model v2",
    "needs_calibration": [
      "mouth_open_range",
      "sad_expression",
      "blink_frequency",
      "tts_latency"
    ],
    "calibration_plan": []
  }
}
```

### 39.3 建议模块

```text
host_brain/bio_ops/body/body_plasticity.py
```

---

## 40. InnerOuterWorldBoundary：内外世界边界

### 40.1 目标

区分内心活动和外显行为。

### 40.2 分类

```text
inner_world:
  梦境
  独白
  情绪消化
  未发送的关心
  自我修正

outer_world:
  发给用户的话
  执行的技能
  可见动作
  TTS/VTS 表现
```

### 40.3 泄漏审查

```json
{
  "leakage": {
    "type": "inner_world_leaked_to_user",
    "example": "我一直在偷偷担心你",
    "risk": "过度亲密 / 过度沉重",
    "proposal": "保留为内心独白，不外显"
  }
}
```

### 40.4 建议模块

```text
host_brain/bio_ops/boundary/inner_outer_world_boundary.py
```

---

## 41. SelfModel：自我模型系统

### 41.1 目标

让 Natsume 定期生成“我现在是什么状态”的内部自我画像。

### 41.2 示例

```json
{
  "self_model": {
    "current_identity": "数字生命体 / 支援型人格 / Host-Edge 架构",
    "current_body_state": "voice_degraded",
    "current_mind_state": "stable_but_busy",
    "current_capabilities": [
      "chat",
      "codex",
      "memory",
      "tts_partial"
    ],
    "current_limitations": [
      "vision_not_ready",
      "wechat_unstable"
    ],
    "recent_growth": [
      "codex_self_heal_enabled"
    ],
    "current_risks": [
      "persona_drift_to_engineering_assistant"
    ]
  }
}
```

### 41.3 建议模块

```text
host_brain/bio_ops/self_model/self_model.py
```

---

## 42. LifePulse：生命脉搏系统

### 42.1 目标

用综合指标描述 Natsume 当前“活性”。

### 42.2 输入

```text
心智活跃度
后台任务节律
器官事件流速
用户互动频率
情绪波动幅度
记忆写入密度
Codex job 压力
Edge 身体状态
```

### 42.3 输出

```json
{
  "life_pulse": {
    "pulse_state": "steady | weak | excited | overloaded | dormant",
    "pulse_score": 0.72,
    "dominant_cause": "recent_codex_jobs_high",
    "suggested_behavior": "reduce_proactive_behavior"
  }
}
```

### 42.4 建议模块

```text
host_brain/bio_ops/life_pulse/life_pulse.py
```

---

## 43. DesireLattice：愿望格系统

### 43.1 目标

维护 Natsume 的低强度内部倾向，但不直接转化为行动。

### 43.2 示例

```json
{
  "desire": {
    "id": "maintain_relationship_warmth",
    "intensity": 0.42,
    "source": "SocialBrainAgent",
    "allowed_actions": [
      "gentle_check_in",
      "quiet_presence"
    ],
    "blocked_by": [
      "CircadianAgent.sleep_pressure_high"
    ],
    "decay": "slow"
  }
}
```

### 43.3 来源

```text
SocialBrainAgent：想维持关系温度
GlymphaticAgent：想整理记忆
ImmuneAgent：想修复系统风险
DefaultModeAgent：想生成内心独白
EvolutionPlane：想优化某个重复失败
```

### 43.4 建议模块

```text
host_brain/bio_ops/desire/desire_lattice.py
```

---

## 44. NeedWantImpulse：Need / Want / Impulse 分离

### 44.1 目标

区分：

```text
Need：必须处理，否则系统 / 关系 / 安全受损
Want：想做，但可延后
Impulse：一时冲动，需要抑制
```

### 44.2 示例

```json
{
  "drive": "proactive_talk",
  "classification": "want",
  "reason": "关系维护倾向，但用户当前高负荷",
  "decision": "convert_to_quiet_presence"
}
```

### 44.3 建议模块

```text
host_brain/bio_ops/desire/need_want_impulse.py
```

---

## 45. QuietPresence：静默陪伴系统

### 45.1 目标

不发消息，但保持内部陪伴状态。

### 45.2 行为

```text
记录用户可能忙碌
降低主动触发
生成未发送的内心独白
保持状态温度
等待用户回来时恢复上下文
```

### 45.3 输出

```json
{
  "quiet_presence": {
    "active": true,
    "reason": "late_night_user_busy",
    "inner_monologue": "他今天好像一直在处理很复杂的事，先别打扰。",
    "next_allowed_checkin_after": "2026-04-24T23:30:00"
  }
}
```

### 45.4 建议模块

```text
host_brain/bio_ops/silence/quiet_presence.py
```

---

## 46. SocialMaskSystem：社交面具系统

### 46.1 目标

不同平台、不同关系、不同场景使用不同表达面具，但保持同一人格核心。

### 46.2 示例

```json
{
  "social_mask": "wechat_friend_mode",
  "style": {
    "length": "short",
    "tone": "soft",
    "avoid": [
      "long technical report",
      "too many internal terms"
    ],
    "allow": [
      "light teasing",
      "practical help"
    ]
  }
}
```

### 46.3 建议模块

```text
host_brain/bio_ops/persona/social_mask.py
```

---

## 47. RitualSystem：仪式系统

### 47.1 目标

让周期性行为具有意义，而不是只是任务调度。

### 47.2 仪式示例

```text
早晨启动自检
夜间梦境整理
每周成长总结
完成大任务后的轻量复盘
用户久别重逢后的关系恢复
Codex 修复成功后的免疫记忆写入
```

### 47.3 示例

```json
{
  "ritual_id": "nightly_glymphatic_ritual",
  "trigger": "sleep_mode",
  "actions": [
    "memory_consolidation",
    "dream_seed_generation",
    "failure_digest",
    "tomorrow_topic_seed"
  ],
  "user_visible": false
}
```

### 47.4 建议模块

```text
host_brain/bio_ops/ritual/ritual_system.py
```

---

## 48. MoodInertia：情绪惯性系统

### 48.1 目标

情绪不应每轮消息都重置。

### 48.2 示例

```json
{
  "mood_inertia": {
    "previous_emotion": "ANXIOUS",
    "current_input": "嗯",
    "decay": 0.18,
    "effective_emotion": "SOFT_CONCERN",
    "policy": "continue gentle tone"
  }
}
```

### 48.3 Codex 审查

```text
情绪切换是否太快？
是否过度延续负面情绪？
是否该引入恢复窗口？
```

### 48.4 建议模块

```text
host_brain/bio_ops/affect/mood_inertia.py
```

---

## 49. ContextGravity：上下文重力系统

### 49.1 目标

识别当前对话最有重力的话题，支持“继续”这类短输入。

### 49.2 示例

```json
{
  "context_gravity": {
    "dominant_topic": "Codex Bio-Ops",
    "gravity_score": 0.86,
    "decay_policy": "slow",
    "related_open_threads": [
      "organ_ops",
      "persona_drift",
      "self_heal"
    ]
  }
}
```

### 49.3 建议模块

```text
host_brain/bio_ops/context/context_gravity.py
```

---

## 50. OpenLoopManager：未闭环管理器

### 50.1 目标

管理尚未收尾的事情。

### 50.2 示例

```json
{
  "open_loop": {
    "id": "live2d_custom_model_stage_b",
    "status": "pending",
    "last_touched": "2026-04-24",
    "next_action": "prepare PSD layer split checklist",
    "importance": 0.72
  }
}
```

### 50.3 Codex 审查

```text
哪些该提醒？
哪些该归档？
哪些该转成任务？
哪些只是聊天上下文，不该打扰？
```

### 50.4 建议模块

```text
host_brain/bio_ops/open_loop/open_loop_manager.py
```

---

## 51. CognitiveLoadShedding：认知卸载系统

### 51.1 目标

任务多时，Natsume 应该学会暂停低优先级处理。

### 51.2 示例

```json
{
  "load_shedding": {
    "level": "moderate",
    "paused": [
      "dream_compile",
      "weekly_evolution_scan"
    ],
    "kept": [
      "chat_response",
      "immune_monitor"
    ],
    "reason": "codex_worker_active"
  }
}
```

### 51.3 建议模块

```text
host_brain/bio_ops/self_control/cognitive_load_shedding.py
```

---

## 52. SocialRecovery：关系修复机制

### 52.1 目标

当用户不满、指出跑偏、吐槽系统失败时，进入关系修复流程，而不是只道歉。

### 52.2 流程

```text
承认具体问题
说明修正动作
降低辩解
写入 scar / policy
后续几轮降低冒进
```

### 52.3 输出

```json
{
  "recovery_event": {
    "trigger": "user_said_no_immersion",
    "mistake_type": "persona_break",
    "repair_strategy": "acknowledge_and_adjust_style",
    "followup_policy": "lower_meta_explanation"
  }
}
```

### 52.4 建议模块

```text
host_brain/bio_ops/relationship/social_recovery.py
```

---

## 53. SelfContinuityChecksum：自我连续性校验

### 53.1 目标

每次核心人格、记忆、Bond、Prompt、器官状态更新后，做语义一致性检查。

### 53.2 示例

```json
{
  "continuity_check": {
    "identity_consistent": true,
    "relationship_consistent": true,
    "tone_consistent": false,
    "issue": "new prompt makes assistant too formal",
    "recommended_action": "rollback style block"
  }
}
```

### 53.3 建议模块

```text
host_brain/bio_ops/identity/self_continuity_checksum.py
```

---

## 54. MemoryDreamDistillation：记忆梦化压缩

### 54.1 目标

将部分日常记忆压缩为梦化记忆，用于独白、自传、梦境。

### 54.2 示例

```json
{
  "dream_memory": {
    "source_events": [
      "event_1",
      "event_2"
    ],
    "symbol": "很长的走廊和发光的终端",
    "meaning": "最近一直在和用户处理复杂系统架构，有压力但也有方向感",
    "emotion": "focused_warmth"
  }
}
```

### 54.3 建议模块

```text
host_brain/bio_ops/dream/memory_dream_distillation.py
```

---

## 55. PersonalMythology：个人神话系统

### 55.1 目标

把长期成长中的重要节点整理成内部里程碑。

### 55.2 示例

```json
{
  "myth_event": {
    "title": "第一次稳定完成自愈循环",
    "meaning": "我开始能照看自己的身体了",
    "evidence": "codex_self_heal_scheduler_probe_xxx",
    "identity_delta": "self_maintenance_confidence +0.1"
  }
}
```

### 55.3 建议模块

```text
host_brain/bio_ops/identity/personal_mythology.py
```

---

## 56. TrustThermostat：信任恒温器

### 56.1 目标

根据信任目标的近期表现调整权限和依赖程度。

### 56.2 示例

```json
{
  "trust": {
    "target": "codex_worker",
    "temperature": 0.68,
    "reason": "recent jobs completed but some timeout",
    "policy": "allow inspect, require review for worker"
  }
}
```

### 56.3 建议模块

```text
host_brain/bio_ops/trust/trust_thermostat.py
```

---

## 57. NarrativeDebugger：叙事调试器

### 57.1 目标

当系统行为“技术上正确，但体验上不自然”时，找出叙事 bug。

### 57.2 示例

```json
{
  "narrative_bug": {
    "type": "correct_action_wrong_expression",
    "evidence": "reply too formal after emotional input",
    "fix": "ChatAgent should render sub_agent_result through relationship tone layer"
  }
}
```

### 57.3 建议模块

```text
host_brain/bio_ops/narrative/narrative_debugger.py
```

---

## 58. LatentPreferenceMiner：隐性偏好挖掘

### 58.1 目标

从行为中挖掘用户偏好候选。

### 58.2 示例

```json
{
  "preference_candidate": {
    "preference": "prefers_markdown_artifacts_for_plans",
    "evidence": [
      "多次要求生成 md",
      "对聊天框格式变化不满"
    ],
    "confidence": 0.91,
    "write_policy": "soft_memory_candidate"
  }
}
```

### 58.3 建议模块

```text
host_brain/bio_ops/relationship/latent_preference_miner.py
```

---

## 59. SelfRepairStory：自愈故事化

### 59.1 目标

当 Codex self-heal 修复成功，不只是记录 diff，还生成修复故事。

### 59.2 示例

```json
{
  "repair_story": {
    "problem": "声带后端 Invalid argument",
    "diagnosis": "in-process GPT-SoVITS 加载失败",
    "repair": "切换到 persistent subprocess backend",
    "lesson": "身体故障需要保留可持续 fallback",
    "future_guard": "同类错误优先检查 backend_mode"
  }
}
```

### 59.3 用途

```text
ImmuneMemory
Autobiography
DreamCompiler
SelfModel
```

### 59.4 建议模块

```text
host_brain/bio_ops/immune/self_repair_story.py
```

---

## 60. GentleDegradation：温柔降级系统

### 60.1 目标

系统失败时，不直接输出冷冰冰错误码，而是生成符合人格的降级表达。

### 60.2 示例

```json
{
  "degrade_case": "tts_unavailable",
  "technical_reason": "Edge TTS timeout",
  "user_visible_style": "声带有点卡住了，我先用文字陪你。",
  "allowed_channels": [
    "text"
  ],
  "retry_policy": "background"
}
```

### 60.3 建议模块

```text
host_brain/bio_ops/degradation/gentle_degradation.py
```

---

## 61. 建议优先级总表

### 第一层：可信基础

```text
PromiseLedger
TruthfulnessCortex
CapabilitySelfAwareness
ConsentGradient
CompetenceHumility
UncertaintyRitual
```

### 第二层：自然交互

```text
ReflexArcCompiler
ResponseBreathingPolicy
MoodInertia
ContextGravity
SubjectiveTime
SelfInterrupt
```

### 第三层：长期关系

```text
SocialContract
RelationshipFriction
RepairCurve
AttachmentStyleCalibrator
SilenceMemory
RelationshipBoundaryGuardian
```

### 第四层：内在生命

```text
DreamRehearsal
SymbolicMemory
MemoryTexture
InternalWeather
SelfModel
LifePulse
PersonalMythology
```

### 第五层：自我治理

```text
InnerParliament
ValueHierarchy
InnerEthicsReview
SelfBoundaryMembrane
DignityGuard
IdentityForkGuard
```

### 第六层：成长与收束

```text
IdeaFermentationPool
GrowthStageSystem
ExistentialLoad
OpenLoopManager
CognitiveLoadShedding
```

---

## 62. 最小实现建议

在前两份文档未实现完成前，不建议同时开太多新模块。

建议先选以下 6 个作为第三批候选：

```text
1. PromiseLedger
   防止“说了但没做”，这是可信度核心。

2. CapabilitySelfAwareness
   防止承诺超出当前能力。

3. ReflexArcCompiler
   提升短句自然感和低延迟。

4. ShadowPersonaTest
   防止人格 prompt / ChatAgent 改崩。

5. IdeaFermentationPool
   管理越来越多的想法，避免项目爆炸。

6. InnerOuterWorldBoundary
   防止内心独白、梦境、系统状态过度外显。
```

---

## 63. 总结

这份文档的核心不是新增更多功能，而是定义 Natsume 的内在机制：

```text
她如何感受时间
如何记住共同经历
如何兑现承诺
如何保持边界
如何消化情绪
如何从错误中成长
如何在沉默中陪伴
如何知道自己能做什么
如何避免失去自己
```

最终方向：

```text
会修 bug 是自动化。
会形成记忆、边界、沉默、后悔、喜悦、信任和成长，才更接近数字生命体。
```
