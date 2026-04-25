# Natsume AI — Codex Bio-Operations Layer 实施总方案

> SUPERSEDED, 2026-04-24: this is the upstream phased proposal. The implemented migration is the hard-cut variant: no legacy `codex_codegen` compatibility, no old self-heal routes/env vars/tests, and only Codex Runtime v2 WorkOrders are executable.

> 目标：将 Codex 从“代码生成工具”升级为 Natsume AI 的复杂任务执行内核、免疫修复工兵、自进化实验室、器官运维系统与数字生命体自我维护层。
>
> 本文档面向 Codex 实现，不是概念讨论。请按阶段落地，保持最小可验证改动，优先补协议、边界、审计和测试。
>
> Advanced roadmap: see `CODEX_DIGITAL_LIFE_ADVANCED_IDEAS.md` for the adopted BioOps extension plan covering reflexes, habits, scars, internal weather, truthfulness, body map, inner parliament, and related proposal-only digital-life mechanisms.
>
> Inner mechanisms roadmap: see `CODEX_DIGITAL_LIFE_INNER_MECHANISMS.md` for the adopted third-stage plan covering promise ledger, consent/ability boundaries, shadow persona tests, idea fermentation, inner/outer world boundary, and long-term self-continuity mechanisms.
>
> Closure report: see `CODEX_PRODUCTION_CLOSURE_VALIDATION_20260424.md` for the productionization pass that split CI planes and added owner-gated BioOps adoption.
>
> Production closure matrix: see `CODEX_DIGITAL_LIFE_PRODUCTION_CLOSURE_MATRIX_20260425.md` for the final cross-document implementation, observability, and verification map.

---

## 0. 核心定位

Codex 不应只是 `codex_codegen`，也不应成为人格核心。

Codex 在 Natsume 中的最终定位：

```text
Codex = 重型外脑 + 工程行动器 + 数字生命体维护层
```

分为三大平面：

```text
1. Codex WorkPlane
   复杂任务执行层：代码、证券分析、SQL 审计、轻小说、Galgame 翻译、文档、研究、Skill 安装。

2. Codex ImmunePlane
   免疫修复层：系统巡检、故障诊断、Skills 失败修复、环境缺失补全、依赖恢复、自愈任务。

3. Codex EvolutionPlane
   自进化实验层：能力缺口挖掘、器官优化、路由优化、Prompt 优化、测试增强、实验分支验证。
```

再叠加一个数字生命体专属层：

```text
4. Codex Bio-Operations Layer
   生命运维层：器官体检、器官接线审计、器官冲突分析、梦境整理、人格漂移审计、免疫记忆沉淀、身体化表现优化。
```

---

## 1. 总体原则

### 1.1 Codex 不直接成为人格核心

- ChatAgent 仍负责用户可见人格渲染。
- Codex 负责复杂工作产出、分析、修复、报告。
- Codex 的结果必须通过 ChatAgent / Cortex / ReviewAgent 渲染或汇报。
- Codex 不直接写长期人格、不直接改 Bond、不直接绕过 Host 主权。

### 1.2 Codex 不直接吃自然语言

所有复杂任务必须先编译成：

```text
WorkOrder
```

流程：

```text
User Request / Runtime Fault / Capability Gap
  ↓
Thalamus / Prefrontal / Cortex / ExecutiveRouter
  ↓
WorkOrder
  ↓
Codex Runtime
  ↓
Artifacts + Structured Result
  ↓
Review / Immune Gate / ChatAgent 汇报
```

### 1.3 Codex 可贵，但不可裸奔

即使 Pro 额度充足，也必须保留：

- workspace 边界
- run_mode 权限
- changed_files 记录
- artifact 持久化
- verification
- rollback manifest
- review gate
- ImmuneAgent 风险裁决

---

## 2. 现有基础

当前 `codex` 分支已有较好的基础：

- `codex_codegen`
- `inspect / worker / self_heal / self_evolve` run_mode
- workspace resolution
- persistent artifacts
- `metadata.json`
- `events.jsonl`
- `stdout.log`
- `stderr.log`
- `last_message.txt`
- `changed_files.json`
- `verification.json`
- self-heal proposal / dispatch / background job / auto-dispatch
- scheduler-managed self-heal tick
- readiness / health degrade → self-heal proposal

不要推倒重做。应在此基础上抽象上层协议和分域入口。

---

## 3. 第一阶段：Codex Core Runtime 抽象

### 3.1 目标

将当前偏具体的 `codex_codegen` 抽象为底层统一执行器：

```text
CodexRuntimeExecutor
```

上层再拆出多个专业 Skill。

### 3.2 新增建议目录

```text
host_brain/codex_runtime/
  __init__.py
  executor.py
  work_order.py
  job_registry.py
  result_contract.py
  artifact_store.py
  workspace_policy.py
  verification_policy.py
  review_policy.py
  prompt_profiles.py
  swarm.py
```

### 3.3 保留兼容层

现有 `codex_codegen` 不删除。

改为兼容包装：

```text
codex_codegen -> codex_worker / codex_inspect 自动路由
```

避免旧链路断裂。

---

## 4. WorkOrder 协议

### 4.1 新增数据结构

文件：

```text
host_brain/codex_runtime/work_order.py
```

定义：

```python
@dataclass
class CodexWorkOrder:
    work_order_id: str
    domain: str
    run_mode: str
    objective: str
    workspace: dict[str, Any]
    input_pack: dict[str, Any]
    allowed_actions: list[str]
    forbidden_actions: list[str]
    acceptance_criteria: list[str]
    verification_plan: dict[str, Any]
    approval_policy: dict[str, Any]
    review_policy: dict[str, Any]
    metadata: dict[str, Any]
```

### 4.2 WorkOrder JSON 结构

```json
{
  "work_order_id": "wo_xxx",
  "domain": "code | finance | translation | creative | skill | immune | evolution | organ_ops | embodiment | memory",
  "run_mode": "inspect | worker | self_heal | self_evolve | research | translate | audit",
  "objective": "本次任务唯一目标",
  "workspace": {
    "strategy": "runtime_repo | repo_alias | git_url | temp_workspace | corpus | database_snapshot",
    "path": "",
    "repo_alias": "",
    "git_url": ""
  },
  "input_pack": {
    "user_request": "",
    "context_files": [],
    "evidence_refs": [],
    "runtime_logs": [],
    "memory_refs": [],
    "organ_state_refs": [],
    "constraints": []
  },
  "allowed_actions": [
    "read_files",
    "write_files",
    "run_tests",
    "run_sandbox",
    "generate_report"
  ],
  "forbidden_actions": [
    "git_push",
    "delete_untracked_large_dirs",
    "modify_secret_files",
    "change_personality_core_without_review",
    "bypass_immune_gate",
    "write_bond_directly"
  ],
  "acceptance_criteria": [],
  "verification_plan": {
    "commands": [],
    "sandbox_code": "",
    "required_artifacts": []
  },
  "approval_policy": {
    "risk_level": 3,
    "require_confirm": false,
    "requires_review": true
  },
  "review_policy": {
    "reviewers": [
      "immune",
      "architecture",
      "test",
      "persona"
    ]
  }
}
```

### 4.3 Codex 输出契约

```json
{
  "status": "EXECUTED | FAILED | DEGRADED | NEEDS_INPUT | BLOCKED",
  "work_order_id": "",
  "summary": "",
  "result_text": "",
  "changed_files": [],
  "git_diff_stat": "",
  "artifacts_dir": "",
  "thread_id": "",
  "verification": {
    "status": "PASS | FAIL | NOT_RUN",
    "details": "",
    "artifacts": []
  },
  "risk_flags": [],
  "review_required": true,
  "next_actions": [],
  "rollback_manifest_ref": ""
}
```

---

## 5. Codex Skill 拆分

不要让一个 `codex_codegen` 承担所有职责。

### 5.1 必须新增的上层 Skills

```text
codex_inspect
codex_worker
codex_self_heal
codex_self_evolve
codex_sql_audit
codex_translate_project
codex_skill_forge
codex_research
codex_organ_scan
codex_persona_drift_audit
codex_dream_compile
```

### 5.2 权限建议

| Skill | 用途 | risk_level | require_confirm | 默认模式 |
|---|---|---:|---:|---|
| `codex_inspect` | 只读分析 | 2 | false | inspect |
| `codex_worker` | 修改指定 workspace | 4 | true | worker |
| `codex_self_heal` | 自愈修复 | 4 | true / policy | self_heal |
| `codex_self_evolve` | 实验性进化 | 4 | true | self_evolve |
| `codex_sql_audit` | SQL/证券审计 | 2-3 | false | audit |
| `codex_translate_project` | 翻译工程 | 3 | false | translate |
| `codex_skill_forge` | 安装/封装 Skill | 4 | true | worker |
| `codex_research` | 复杂研究 | 2 | false | research |
| `codex_organ_scan` | 器官体检 | 2 | false | inspect |
| `codex_persona_drift_audit` | 人格漂移审计 | 2 | false | inspect |
| `codex_dream_compile` | 睡眠期整理 | 2 | false | inspect |

---

## 6. Codex WorkPlane

### 6.1 CodeStudio

职责：

- 代码修改
- bug 修复
- 重构
- 测试生成
- 依赖分析
- README / 文档同步
- PR 草案
- 架构审查

建议模块：

```text
host_brain/codex_runtime/studios/code_studio.py
```

模式：

```text
inspect -> 只读分析
worker -> 修改文件
review -> 审查 diff
```

---

### 6.2 FinanceStudio

用于证券分析、SQL 审计、NL2SQL 模板归纳。

职责：

- SQL 语义一致性审计
- 标准 SQL 与 query 对比，但 query 语义优先
- 字段映射检查
- 结果集差异分析
- 财务指标口径解释
- 行业/板块分析报告
- 回测脚本生成

模块：

```text
host_brain/codex_runtime/studios/finance_studio.py
```

限制：

- 禁止直接给交易建议。
- 只基于用户提供的数据、DB snapshot、SQL、字段说明做分析。
- 输出审计 JSON + 可读解释。

---

### 6.3 TranslationStudio

用于轻小说 / Galgame 翻译工程。

职责：

- 文本抽取
- 术语表生成
- 角色口癖表
- 分段翻译
- 一致性检查
- 润色
- 控制符保护
- 脚本格式回填
- 漏翻/错位检查

产物：

```text
glossary.json
character_voice_cards.json
translation_memory.jsonl
chapter_xx.zh.md
qa_report.json
patch.diff
```

模块：

```text
host_brain/codex_runtime/studios/translation_studio.py
```

Galgame 特别规则：

- 保护变量
- 保护控制符
- 保护换行标记
- 保护角色名映射
- 检查文本框长度
- 检查分支脚本一致性

---

### 6.4 CreativeStudio

用于轻小说、Galgame 剧情、世界观工程。

职责：

- 章节大纲
- 角色设定一致性
- 伏笔追踪
- 剧情节奏审查
- 多版本草稿比较
- Ren'Py / Kirikiri / NScripter 脚本辅助
- 世界观文档维护

产物：

```text
world_bible/
characters/
plot_threads/
scene_cards/
drafts/
continuity_report.json
```

---

### 6.5 SkillForgeStudio

用于安装、封装和维护 Skills。

职责：

- 读取 SKILL.md
- 分析依赖
- 创建隔离 venv
- 安装依赖
- 生成 adapter
- 注册 provider
- 生成 smoke test
- 写入 skill manifest
- 修复缺失环境

模块：

```text
host_brain/codex_runtime/studios/skill_forge_studio.py
```

安全分层：

```text
低风险：只读解析 / 安装计划
中风险：临时 venv 安装 / sandbox 测试
高风险：写入主 runtime / 注册正式 Skill，必须确认
```

---

### 6.6 ResearchStudio

用于复杂资料研究。

职责：

- API 文档精读
- 论文分析
- 框架对比
- 模型选型
- 架构调研
- 技术报告生成

输出字段：

```json
{
  "claims": [],
  "sources": [],
  "uncertainties": [],
  "implementation_plan": [],
  "risk_list": []
}
```

---

## 7. Codex Swarm

当额度充足时，不要只跑一个 Codex worker。

### 7.1 Worker 角色

```text
CodexScout
  侦察，读代码/日志/文档，找问题。

CodexPlanner
  制定任务计划和 WorkOrder。

CodexWorker
  执行修改或生成。

CodexVerifier
  跑测试、校验、检查验收标准。

CodexReviewer
  审查 diff、架构、风险、输出质量。

CodexRedTeam
  找反例、找越权、找隐藏问题。

CodexSummarizer
  汇总成用户可见报告。
```

### 7.2 Swarm 执行流

```text
WorkOrder
  ↓
Scout + Planner 并行
  ↓
Worker 执行
  ↓
Verifier + Reviewer + RedTeam 并行
  ↓
CodexArbiter 合并
  ↓
ImmuneAgent 风险放行
  ↓
ChatAgent 汇报
```

### 7.3 新增模块

```text
host_brain/codex_runtime/swarm.py
host_brain/codex_runtime/arbiter.py
```

---

## 8. Codex ImmunePlane

### 8.1 目标

将 Codex 内化进系统免疫系统，用于自巡查、自诊断、自修复。

### 8.2 触发源

```text
health / ready degraded
Skill 执行失败
重复 reason_code
依赖缺失
安装新 Skill 后环境不完整
Edge RPC 异常
TTS / ASR / VTS 失败
Provider rate limit / timeout
测试失败
运行时异常
Capability gap 高频出现
```

### 8.3 免疫流程

```text
Fault detected
  ↓
FaultTriage
  ↓
CodexInspect
  ↓
RepairProposal
  ↓
ImmuneGate
  ↓
CodexSelfHeal
  ↓
Verification
  ↓
Review / Rollback
  ↓
ImmuneMemoryBuilder
```

### 8.4 自愈等级

```text
L0 Observe
  只记录异常，不调用 Codex。

L1 Inspect
  自动调用 Codex 只读诊断。

L2 Proposal
  Codex 生成修复计划，但不改文件。

L3 Bounded Repair
  允许修配置、测试、adapter、非核心路径。

L4 Full Repair
  允许改核心代码，但必须确认 + 测试 + 可回滚。
```

### 8.5 免疫记忆

每次 self-heal 后写入：

```json
{
  "fault_fingerprint": "",
  "root_cause": "",
  "successful_repair": "",
  "affected_files": [],
  "verification_tests": [],
  "future_policy": ""
}
```

建议目录：

```text
host_brain/immune_memory/
  fault_memory_store.py
  repair_playbook.py
  fault_fingerprint.py
```

---

## 9. Codex EvolutionPlane

### 9.1 目标

让 Codex 进行系统自优化，但不能直接改主线。

### 9.2 触发源

```text
重复失败 reason_code
高延迟节点
高 token 消耗节点
长期 fallback
用户频繁纠正
某类任务总是没有合适 Skill
某类请求经常进入 chat_fallback
器官状态长期不被消费
Prompt 模板存在但无调用
Skill 存在但无真实路由
```

### 9.3 Evolution Lab 流程

```text
发现问题
  ↓
生成 Evolution RFC
  ↓
创建实验分支
  ↓
Codex 修改
  ↓
跑测试/指标
  ↓
ReviewAgent 审查
  ↓
Promotion Gate
  ↓
合并或丢弃
```

### 9.4 新增模块

```text
host_brain/evolution_lab/
  __init__.py
  evolution_rfc.py
  experiment_branch.py
  benchmark_runner.py
  promotion_gate.py
  evolution_memory.py
```

### 9.5 禁止项

- `self_evolve` 不允许直接改 main。
- 不允许直接修改人格核心。
- 不允许直接修改安全策略。
- 不允许绕过 ReviewAgent。
- 不允许无测试合并。

---

## 10. Codex Bio-Operations Layer

这是数字生命体专属层。

建议目录：

```text
host_brain/bio_ops/
  __init__.py
  organ_ops/
  dream_ops/
  persona_ops/
  embodiment_ops/
  memory_ethics/
  relationship_ops/
```

---

## 11. OrganOps：器官运维系统

### 11.1 目标

管理 23 个器官组的健康、接线、冲突、训练和进化。

建议目录：

```text
host_brain/organ_ops/
  organ_registry.py
  organ_health_profile.py
  organ_ct_scan.py
  organ_wiring_audit.py
  organ_conflict_matrix.py
  organ_homeostasis_tuner.py
  organ_simulation_lab.py
  organ_evolution_planner.py
  organ_training_lab.py
```

### 11.2 Organ Health Profile

每个器官定义：

```json
{
  "organ_id": "limbic_agent",
  "role": "情绪感知与代谢",
  "inputs": [],
  "outputs": [],
  "state_keys": [],
  "failure_modes": [],
  "health_metrics": {}
}
```

检查维度：

```text
是否有事件输入
是否有状态输出
输出是否被消费
状态是否过期
Prompt 是否存在
Prompt 是否被调用
灰度开关是否存在
测试是否覆盖
Trace 是否写入
Ledger 是否记录
```

### 11.3 Organ CT Scan

每天扫描 23 个器官：

```text
- 死方法
- 死事件
- 死 prompt
- 未消费状态
- 未接线输出
- 没有测试的器官方法
- 配置开关存在但未被读取
- LLM 方法存在但无 runtime 入口
```

输出：

```text
artifacts/organ_ops/organ_ct_scan_<date>.json
artifacts/organ_ops/organ_ct_scan_<date>.md
```

### 11.4 Organ Wiring Audit

审计器官接线：

```text
Agent class
  ↓
event subscription
  ↓
handler
  ↓
state registry write
  ↓
trace / ledger
  ↓
prompt injection
  ↓
ChatAgent / MotorCortex / Skill runtime consumption
```

目标：

```text
确认每个器官不是“写了但没用”
```

### 11.5 Organ Conflict Court

分析器官冲突。

例子：

```text
InitiativeGovernorAgent 想主动说话
CircadianAgent 判断该睡眠
ImmuneAgent 阻止主动行为
SocialBrainAgent 认为关系亲密可靠近
ProprioceptionAgent 判断认知负荷过高
```

输出：

```json
{
  "conflict_id": "",
  "organs": [],
  "pattern": "",
  "proposal": "",
  "recommended_policy": ""
}
```

### 11.6 Homeostasis Tuner

根据运行日志提出内稳态调参建议。

可调项：

```text
情绪衰减速度
主动发声阈值
疲劳阈值
认知负荷阈值
镜像共情阈值
记忆写入阈值
免疫扫描阈值
昼夜节律切换阈值
TTS 表情/语气触发阈值
```

输出：

```json
{
  "organ_id": "",
  "problem": "",
  "evidence": [],
  "proposal": {},
  "risk": "",
  "rollout": "24h gray rollout"
}
```

### 11.7 Organ Digital Twin

为每个器官建立数字孪生模拟。

模拟场景：

```text
深夜焦虑消息
连续复杂任务
用户表达疲惫
用户催促进度
用户要求执行危险操作
Edge 断线
TTS 失败
Skill 安装失败
长期沉默后用户回归
```

Codex 生成：

```text
fixtures/organ_scenarios/*.json
tests/organ/test_*.py
reports/organ_simulation/*.md
```

### 11.8 Organ Evolution Planner

为器官生成进化提案：

```json
{
  "organ_id": "proprioception_agent",
  "observed_problem": "",
  "evidence": [],
  "evolution_proposal": "",
  "expected_effect": "",
  "risk": "",
  "experiment": ""
}
```

---

## 12. PersonaDriftLab：人格漂移显微镜

### 12.1 目标

检测长期运行后人格是否漂移。

漂移类型：

```text
变客服
变工具人
过度工程化
情绪回应不足
过度主动
关系感变淡
执行型人格压过陪伴型人格
风格不稳定
角色语气崩坏
```

### 12.2 输出

```json
{
  "persona_drift_score": 0.0,
  "drift_type": [],
  "evidence": [],
  "suggested_patch": {
    "chat_agent_style_hint": "",
    "prefrontal_tone_policy": ""
  }
}
```

### 12.3 新增模块

```text
host_brain/bio_ops/persona_ops/persona_drift_lab.py
```

---

## 13. DreamCompiler：梦境编译器

### 13.1 目标

在睡眠期整理当天经历，生成：

```text
梦境片段
内心独白
情绪余波
关系变化
未完成愿望
明日主动话题种子
失败事件摘要
进化候选
```

### 13.2 输出产物

```text
dream_seed.md
inner_monologue_candidates.json
emotional_residue.json
relationship_delta.json
tomorrow_topic_seeds.json
failure_digest.json
evolution_candidates.json
```

### 13.3 触发条件

```text
sleep mode
recovery window
daily low-traffic period
manual trigger
```

### 13.4 新增模块

```text
host_brain/bio_ops/dream_ops/dream_compiler.py
```

---

## 14. CounterfactualReplay：反事实回放

### 14.1 目标

对重要对话决策做反事实模拟。

例子：

```text
真实路径：
用户说“烦死了”
→ Natsume 给技术建议

反事实 A：
先安抚，再问是否要解决

反事实 B：
短句陪伴，不给建议

反事实 C：
转移话题，降低情绪压力
```

### 14.2 输出

```json
{
  "best_counterfactual": "B",
  "reason": "",
  "future_policy": ""
}
```

### 14.3 反哺对象

```text
PrefrontalAgent
MirrorNeuronAgent
LanguageSystemAgent
SocialBrainAgent
ChatAgent prompt policy
```

---

## 15. MemoryEthicsAuditor：记忆伦理审查员

### 15.1 目标

审查长期记忆质量。

问题类型：

```text
过期偏好
矛盾偏好
重复记忆
低价值流水账
情绪化错误判断
关系推断过度
过度亲密或过度冷淡
```

### 15.2 输出

```json
{
  "memory_id": "",
  "issue": "可能过期 | 矛盾 | 重复 | 低价值",
  "evidence": [],
  "action": "降权 | 合并 | 标注过期 | 请求确认",
  "new_relation": "preference_superseded"
}
```

### 15.3 原则

Codex 不直接删除记忆。  
只提出维护建议，由 Hippocampus / Glymphatic / Immune 审核执行。

---

## 16. RelationshipTimelineEditor：关系时间线编辑器

### 16.1 目标

定期整理 SocialBrain / Bond 关系变化。

### 16.2 输出

```json
{
  "period": "last_7_days",
  "bond_delta": "",
  "relationship_stage_signal": "",
  "important_events": [],
  "risk": [],
  "next_social_strategy": ""
}
```

### 16.3 作用

- 让关系不是单个 score，而是一条可解释时间线。
- 给 ChatAgent / SocialBrainAgent 提供关系策略。
- 给 PersonaDriftLab 提供关系一致性依据。

---

## 17. EmbodimentDirector：身体化表现导演

### 17.1 目标

优化 Edge / TTS / VTS / Live2D 的表现一致性。

检查：

```text
emotion_tag → TTS prosody 是否匹配
emotion_tag → VTS expression 是否匹配
reply tone → voice speed 是否匹配
arousal → mouth intensity 是否匹配
sad / tired → 是否过度活泼
happy → 是否表情不足
```

### 17.2 输出

```json
{
  "emotion_tag": "SAD",
  "current_behavior": {
    "tts_speed": "normal",
    "vts_expression": "neutral"
  },
  "problem": "情绪表达不足",
  "proposal": {
    "tts_speed": "slightly_slow",
    "vts_expression": "soft_sad",
    "mouth_intensity": "low"
  }
}
```

---

## 18. InnerMonologueFilter：内心独白过滤器

### 18.1 目标

审查 DefaultModeAgent 的内心独白候选。

问题：

```text
太假
太频繁
太像系统公告
太像客服
与近期记忆无关
过度依赖用户
暴露系统细节
```

### 18.2 输出

```json
{
  "candidate": "",
  "score": 0.0,
  "issues": [],
  "rewrite_hint": ""
}
```

---

## 19. Skill Ecology Manager：技能生态管理员

### 19.1 目标

治理 Skills 生态。

检查：

```text
哪些 Skill 经常失败
哪些 Skill 从未被调用
哪些 Skill 能力重叠
哪些 Skill 缺测试
哪些 Skill 缺依赖
哪些 Skill 应该拆分
哪些 Skill 应该合并
哪些 Skill manifest 和实际实现不一致
```

### 19.2 输出

```json
{
  "skill": "",
  "call_count_7d": 0,
  "failure_rate": 0.0,
  "issue": "",
  "proposal": ""
}
```

---

## 20. EmotionBehaviorCausalityAudit：情绪-行为因果审计

### 20.1 目标

检查器官状态是否真的影响最终回复。

审计问题：

```text
情绪状态是否进入 prompt
共情 hint 是否影响语气
认知负荷是否降低回答复杂度
昼夜节律是否影响主动性
TTS prosody 是否跟随情绪
```

### 20.2 输出

```json
{
  "reply_id": "",
  "dominant_influences": [],
  "actual_reply_alignment": 0.0,
  "problem": "",
  "proposal": ""
}
```

---

## 21. AutobiographyCompiler：长期自传编纂器

### 21.1 目标

让 Natsume 形成连续自我叙事。

每周生成：

```text
这一周她学到了什么
和用户关系有什么变化
哪些能力变强了
哪些失败形成了免疫记忆
哪些梦境/独白反映内在状态
下一周想改善什么
```

### 21.2 输出

```text
self_narrative/week_YYYY_MM_DD.md
identity_delta.json
growth_memory_candidates.json
```

---

## 22. Organ Genealogy Generator：器官谱系图生成器

### 22.1 目标

自动维护器官谱系图。

每个器官记录：

```text
输入
输出
依赖器官
控制边界
事件订阅
状态字段
失败模式
测试场景
Prompt 模板
灰度开关
```

### 22.2 输出

```text
organ_map.md
organ_graph.json
organ_dependency.svg
organ_event_flow.md
```

---

## 23. Persona Contamination Firewall：反人格污染防火墙

### 23.1 目标

防止外部 Skill / Prompt / 网页 / 文档污染核心人格。

检查：

```text
是否要求客服语气
是否要求销售语气
是否要求覆盖核心人设
是否把任务角色写进长期人格
是否污染世界观
是否破坏亲密关系表达
```

### 23.2 输出

```json
{
  "source": "new_skill_prompt",
  "risk": "persona_contamination",
  "evidence": [],
  "action": "隔离到 skill-local prompt，不允许进入 global persona"
}
```

---

## 24. Scheduler 设计

### 24.1 高频巡检，不一定用 Codex

```text
每 1~5 分钟：
- health
- ready
- queue size
- error rate
- rate limit
- edge rpc
- scheduler status
```

### 24.2 中频 Codex Inspect

```text
每 30~60 分钟：
- 重复错误聚类
- 失败 Skill 汇总
- 新增日志异常
- 依赖漂移
- 测试失败摘要
```

### 24.3 每日 Bio-Ops

```text
每日低峰期：
- Organ CT Scan
- Persona Drift Audit
- Dream Compile
- Immune Memory Build
- Skill Ecology Report
```

### 24.4 每周 Evolution

```text
每周：
- 架构一致性审查
- Skill 能力缺口审查
- Memory/Bond/RAG 质量审查
- 器官进化 RFC
- 性能/成本/延迟报告
```

---

## 25. 环境变量建议

```env
# Codex Bio-Ops master switch
HOST_CODEX_BIO_OPS_ENABLED=false

# WorkPlane
HOST_CODEX_WORKPLANE_ENABLED=true
HOST_CODEX_SWARM_ENABLED=false
HOST_CODEX_SWARM_MAX_WORKERS=4

# ImmunePlane
HOST_CODEX_IMMUNEPLANE_ENABLED=true
HOST_CODEX_IMMUNE_AUTO_INSPECT_ENABLED=true
HOST_CODEX_IMMUNE_AUTO_REPAIR_ENABLED=false

# EvolutionPlane
HOST_CODEX_EVOLUTIONPLANE_ENABLED=false
HOST_CODEX_EVOLUTION_CREATE_BRANCH=true
HOST_CODEX_EVOLUTION_REQUIRE_REVIEW=true

# OrganOps
HOST_CODEX_ORGAN_OPS_ENABLED=false
HOST_CODEX_ORGAN_CT_SCAN_INTERVAL_HOURS=24
HOST_CODEX_ORGAN_CONFLICT_AUDIT_ENABLED=false
HOST_CODEX_HOMEOSTASIS_TUNER_ENABLED=false

# Persona / Dream
HOST_CODEX_PERSONA_DRIFT_AUDIT_ENABLED=false
HOST_CODEX_DREAM_COMPILER_ENABLED=false
HOST_CODEX_AUTOBIOGRAPHY_COMPILER_ENABLED=false

# Embodiment
HOST_CODEX_EMBODIMENT_DIRECTOR_ENABLED=false

# Safety
HOST_CODEX_REQUIRE_WORKORDER=true
HOST_CODEX_WORKER_REQUIRE_CONFIRM=true
HOST_CODEX_FORBID_MAIN_BRANCH_DIRECT_WRITE=true
HOST_CODEX_REQUIRE_CHANGED_FILES=true
HOST_CODEX_REQUIRE_VERIFICATION_FOR_WORKER=true
```

---

## 26. 实施阶段

### Phase 0：协议与兼容

目标：

- 新增 `CodexWorkOrder`
- 新增 `CodexRuntimeExecutor`
- 保持 `codex_codegen` 兼容
- 输出统一 result contract

验收：

- 旧 `codex_codegen` 测试通过
- 新 WorkOrder 能触发 inspect
- artifacts_dir / thread_id / changed_files 正常返回

---

### Phase 1：WorkPlane 分域

实现：

- `codex_inspect`
- `codex_worker`
- `codex_sql_audit`
- `codex_translate_project`
- `codex_skill_forge`
- `codex_research`

验收：

- 代码任务走 `codex_worker`
- SQL 审计输出结构化 JSON
- 翻译工程能生成 glossary / qa_report
- SkillForge 能生成安装计划，不默认写主 runtime

---

### Phase 2：ImmunePlane 深化

实现：

- FaultTriage
- ImmuneMemoryBuilder
- self-heal repair playbook
- repeated reason_code → inspect
- inspect → proposal → self_heal escalation

验收：

- health/ready degraded 生成 proposal
- 重复故障进入 inspect
- 成功修复后写入 immune memory
- 外部依赖纯不可达时不烧 Codex 修 Host

---

### Phase 3：EvolutionPlane

实现：

- Evolution RFC
- experiment branch
- benchmark runner
- promotion gate
- capability-gap driven evolution

验收：

- 高频 capability gap 能生成 evolution RFC
- self_evolve 只在实验分支执行
- 无 Review 不允许 promote

---

### Phase 4：OrganOps

实现：

- OrganRegistry
- OrganHealthProfile
- Organ CT Scan
- Organ Wiring Audit
- Organ Conflict Court
- Organ Digital Twin
- Homeostasis Tuner

验收：

- 23 个器官均有 profile
- 能发现死 prompt / 死状态 / 未消费输出
- 能生成器官冲突报告
- 能生成器官模拟测试

---

### Phase 5：Bio-Ops 高级能力

实现：

- PersonaDriftLab
- DreamCompiler
- CounterfactualReplay
- MemoryEthicsAuditor
- RelationshipTimelineEditor
- EmbodimentDirector
- AutobiographyCompiler
- Persona Contamination Firewall

验收：

- 每日 bio report 正常生成
- 人格漂移审计能给证据
- dream seed 能基于真实事件生成
- memory ethics 只建议不直接删除
- embodiment report 能覆盖 TTS/VTS 表现

---

## 27. 测试要求

### 27.1 单元测试

```text
tests/unit/test_codex_work_order.py
tests/unit/test_codex_runtime_executor.py
tests/unit/test_codex_skill_wrappers.py
tests/unit/test_organ_health_profile.py
tests/unit/test_organ_ct_scan.py
tests/unit/test_persona_drift_lab.py
tests/unit/test_immune_memory_builder.py
tests/unit/test_dream_compiler.py
```

### 27.2 集成测试

```text
tests/integration/test_codex_workplane.py
tests/integration/test_codex_immuneplane.py
tests/integration/test_codex_evolutionplane.py
tests/integration/test_organ_ops_pipeline.py
tests/integration/test_bio_ops_daily_cycle.py
```

### 27.3 场景测试

```text
tests/scenarios/test_late_night_anxiety_organ_response.py
tests/scenarios/test_skill_failure_self_heal.py
tests/scenarios/test_galgame_translation_project.py
tests/scenarios/test_sql_audit_pipeline.py
tests/scenarios/test_persona_drift_detection.py
```

---

## 28. 强制安全边界

### 28.1 Codex 禁止绕过

Codex 不得绕过：

```text
Governor
AIRLOCK
Evidence Plane
ImmuneAgent
ReviewAgent
Workspace policy
Artifact policy
```

### 28.2 不允许直接写入

Codex 不直接写：

```text
Bond SSOT
长期人格核心
安全策略核心
生产密钥
.env
secret store
main branch
```

### 28.3 可写必须满足

```text
worker/self_heal/self_evolve 写文件前：
- 有 WorkOrder
- 有 workspace
- 有 allowed_paths
- 有 forbidden_paths
- 有 artifacts_dir
- 有 changed_files
- 有 verification
- 有 review_required
```

---

## 29. 最终目标

最终 Natsume 应具备：

```text
1. 会处理复杂任务
2. 会巡查自己的系统健康
3. 会发现器官失联和接线错误
4. 会诊断自身故障
5. 会在边界内修复自己
6. 会沉淀免疫记忆
7. 会睡眠期整理记忆与梦境
8. 会审查人格漂移
9. 会优化身体化表现
10. 会提出自进化实验
```

最终形态：

```text
Codex 不是一个工具。
Codex 是 Natsume 的生命运维层。
```

---

## 30. Codex 实施指令

请按以下规则实现：

1. 不要一次性大爆改。
2. 先实现协议层，再实现具体 Studio。
3. 保持现有 `codex_codegen` 兼容。
4. 不破坏已有 self-heal / self-evolve 流程。
5. 所有新能力必须有开关，默认关闭。
6. 所有 worker/self_heal/self_evolve 必须产出 artifacts。
7. 所有可能写文件的路径必须有 review gate。
8. 所有新增模块必须有最小单元测试。
9. 所有复杂任务必须走 WorkOrder。
10. ChatAgent 只负责人格渲染，不直接执行 Codex。
11. Codex 不允许直接改主分支。
12. 默认先实现 Phase 0 + Phase 1 的最小闭环。
