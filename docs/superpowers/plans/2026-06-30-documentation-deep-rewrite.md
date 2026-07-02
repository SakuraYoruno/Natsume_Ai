# 文档深度重写 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以代码为唯一真源，逐模块深度重写所有核心文档，消除多轮迭代累积的文档-代码失配。

**Architecture:** 分 4 层递进重写。第 1 层建立准确的架构框架（CURRENT_CODE_ARCHITECTURE + dataflow_report），第 2 层填充模块参考细节（CORE_MODULES_REFERENCE + CAPABILITY + PENTEST_MODULES_ARCHITECTURE），第 3 层对齐入口与索引（README + DOCMAP），第 4 层补齐契约与叙事（DevSpec + PRD + Soul + theory）。

**Tech Stack:** Markdown, Git, Bash (for code fact extraction scripts)

**Design spec:** `docs/superpowers/specs/2026-06-30-documentation-deep-rewrite-design.md`

---

### Task 0: 提取代码事实基线脚本

**Files:**
- Create: `scripts/audit/extract_code_facts.sh`

- [ ] **Step 1: 创建代码事实提取脚本**

此脚本自动统计各模块文件数、行数、关键类名，作为所有文档重写的不可变参照。

```bash
#!/bin/bash
# scripts/audit/extract_code_facts.sh
# 提取代码事实基线，输出到 stdout
echo "=== CODE FACTS BASELINE $(date +%Y-%m-%d) ==="
echo ""
echo "## Host Brain 总览"
echo "- Total .py files: $(find host_brain -name '*.py' | wc -l)"
echo ""
echo "## 子目录规模"
for dir in app persona persona/surface persona/surface/scoring pentest pentest/agent pentest/adversarial pentest/swarm pentest/online pentest/tools pentest/data pentest/report pentest/utils runtime tribe memory memory/life_memory governor skills/impl; do
  if [ -d "host_brain/$dir" ]; then
    files=$(find "host_brain/$dir" -maxdepth 1 -name '*.py' | wc -l)
    lines=$(find "host_brain/$dir" -maxdepth 1 -name '*.py' -exec cat {} + | wc -l)
    echo "- $dir: $files files, $lines lines"
  fi
done
echo ""
echo "## Pentest agent/ 完整文件清单"
echo '```'
ls host_brain/pentest/agent/*.py | xargs -I{} basename {} | sort | while read f; do
  lines=$(wc -l < "host_brain/pentest/agent/$f")
  echo "  $f ($lines lines)"
done
echo '```'
echo ""
echo "## 路由注册"
echo '```'
grep -n "include_router\|prefix" host_brain/app/router_registry.py | head -40
echo '```'
echo ""
echo "## Digital Life 端点"
echo '```'
grep -n "@router\.\(get\|post\|put\|delete\)" host_brain/app/digital_life_routes.py
echo '```'
```

- [ ] **Step 2: 运行脚本，保存基线**

```bash
bash scripts/audit/extract_code_facts.sh > docs/50_project/arch/CODE_FACTS_BASELINE_2026-06-30.md
```

- [ ] **Step 3: 提交基线**

```bash
git add scripts/audit/extract_code_facts.sh docs/50_project/arch/CODE_FACTS_BASELINE_2026-06-30.md
git commit -m "audit: add code facts baseline extraction script and snapshot"
```

---

## 第 1 层：架构框架层

### Task 1: 重写 CURRENT_CODE_ARCHITECTURE.md (v4.1 → v4.2)

**Files:**
- Modify: `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md`
- Reference: `host_brain/app/router_registry.py`, `host_brain/app/digital_life_routes.py`, `host_brain/app/monitor_ui/src/app/routeManifest.jsx`

**变更要点：**

- [ ] **Step 1: 更新版本号和同步日期**

将文件头部的 `v4.1` 改为 `v4.2`，`最后同步：2026-06-30` 保持不变（今天就是 6/30）。

- [ ] **Step 2: §3 Host Main 装配 — 补充 obs_routes 说明**

在 `router_registry.py` include 列表后补充：

```markdown
Observability 路由（`host_brain/app/obs_routes_*.py`，23 个文件，~14K 行）不在 `router_registry.py` 中注册，
而是通过 `host_brain/app/obs_routes.py` 聚合器在 `obs_fastapi_app.py` 中单独装配。
```

- [ ] **Step 3: §4 Host 路由前缀 — 补充 proactive 和 obs 端点**

在现有路由表中补充：

```markdown
| `/v1/digital-life/proactive/*` | `digital_life_routes.py` | Proactive 主动发起（attempt/recent/episodes） |
| `/obs/proactive-autonomy` | `obs_routes_proactive_autonomy.py` | Proactive autonomy 快照 |
```

- [ ] **Step 4: §6 Persona/Surface — 重写为实际管线**

将现有简单分组替换为完整的管线描述：

```markdown
## 6. Persona / Surface

当前 Persona 层（75 文件）按 Phase I-XVI 组织。主干管线：

### 6.1 人格与状态
| 组 | 关键文件 | 说明 |
|---|---|---|
| 核心引擎 | `persona_engine.py`、`personality_traits.py`（7 维向量） | 特质加载/持久化、表达 hint |
| 内在世界 | `inner_world.py`(2,565行)、`inner_activity_agent.py`、`inner_monologue.py` | 离线状态机、独白、体感、沉默 |
| 情感系统 | `emotional_proactive.py`(845行)、`emotional_silence.py`、`emotion_persistence.py`、`emotion_reactivity.py` | 主动发起、沉默、跨会话持久化 |
| 成长演化 | `evolution_orchestrator.py`、`evolution_desire_engine.py`、`trait_drift_guard.py`、`drift_detector.py` | 人格漂移守护、涌现成长 |

### 6.2 Surface 生成管线
```text
SurfaceMainChain (surface_main_chain.py, 250行)
  → SurfaceRuntime (surface_runtime.py, 2,668行) — LLM 运行时编排
  → SurfaceMemoryRecall (surface_memory_recall.py, 4,539行) — 记忆召回注入
  → SurfacePacketCompiler (surface_packet_compiler.py, 713行) — 控制包编译
  → CandidateScoring (surface/candidate_scoring.py, 1,912行) — 候选评分与选择
    → scoring/parse.py (262行) — 候选解析
    → scoring/stage_positions.py (406行) — 场景位置评分
    → scoring/control_affect.py (398行) — 情感控制评分
    → scoring/texture.py (273行) — 纹理特征评分
```

### 6.3 关键数据模型
- `ReplyControlPacket`：inner-state leakage（inner_weather、body_state_hint、care_signal、continuity_signal、tension_hint、liveliness、active_loops）
- `SurfaceInfluencePacket`：7 种子影响类型（Task、Affect、Relationship、Memory、Continuity、Safety、Delivery）
```

- [ ] **Step 5: §7 Pentest — 补充双规划器和 agent 内部结构**

在现有 Pentest 部分补充：

```markdown
### 7.1 规划器
当前存在三条规划路径，由 `agent/compat.py:LegacyKernelAdapter` 和环境变量 `PENTEST_COGNITIVE_KERNEL` 控制：
| 规划器 | 文件 | 说明 |
|---|---|---|
| MCTS | `mcts_planner.py`(1,111行) | 经典蒙特卡洛树搜索 |
| ReAct | `llm_react_planner.py`(710行) | LLM 驱动的 ReAct 循环 |
| Cognitive | `agent/cognitive_engine.py`(834行) | LLM 假设生成 + 信念加权评分，替代旧 action_policy + MCTS 管线 |

### 7.2 Agent 内部子包
`pentest/agent/`（31 文件, 8,906 行）核心模块：
- 认知：`cognitive_engine.py`、`meta_cognition.py`、`situation_analyzer.py`
- 信念/目标：`belief_model.py`(394行)、`goal_model.py`(416行)
- 演化：`evolution_core.py`(693行) — Observe→Reflect→Adapt→Evolve 循环
- 攻击：`attack_planner.py`、`exploit_engine.py`、`payload_factory.py`
- 知识：`knowledge.py`、`threat_intel.py`、`online_research.py`(534行)
- 约束：`opsec.py`(433行)、`budget.py`(75行)、`reward.py`(102行)

### 7.3 报告模块
- `phase6_report.py`(492行)：实际报告生成
- `report/json_report.py`(5行) / `report/markdown_report.py`(20行)：存根
```

- [ ] **Step 6: §8 Tribe — 更新为实际规模**

```markdown
## 8. Tribe

`host_brain/tribe/`：34 文件，23,534 行。

- `registry.py`：23 个 family spec + 25 个默认 agent descriptor（实际 bootstrap 24 个）。
- Agent 大小分布：`thalamus_agent.py`(4,014行)、`limbic_agent.py`(1,922行)、`prefrontal_agent.py`(1,584行)、`immune_agent.py`(1,469行)、`tribe_state.py`(1,119行)、`social_brain_agent.py`(1,116行)、`ans_agent.py`(1,051行)...
- 辅助模块：`event_bus.py`(374行)、`handoff.py`(145行)、`cortex_route_decider.py`(131行)、`edge_command_pusher.py`(343行)
- 当前 agent class 总计 26 个（23 个 brain-region agent + cortex_intent_analyzer、cortex_context_collector、edge_command_pusher）。
```

- [ ] **Step 7: §9 Monitor UI — 补充完整路由分组表**

```markdown
## 9. Monitor UI

当前 route manifest（`routeManifest.jsx`）注册 37 条路由，分为 7 个导航组：

| 导航组 | 路由数 | 代表路由 |
|---|---|---|
| Cockpit | 1 | `/cockpit`（DigitalLifeOverview） |
| Life Systems | 7 | `/life/timeline`、`/inner-world`、`/proactive-autonomy`、`/life/runtime`、`/emotion-dashboard`、`/life/body`、`/life/self-model` |
| Observability | 3 | `/observability/trace`、`/observability/sessions`、`/observability/metrics` |
| Operations | 8 | `/chat/debug`、`/tools`、`/canon`、`/bond`、`/gateway` 等 |
| Execution | 5 | `/skills`、`/automation`、`/scheduler` 等 |
| Security Lab | 3 | `/security/pentest`、`/security/pentest/detail`、`/security/immune` |
| Integrations | 8 | `/integrations/*` 各平台配置页 |
```

- [ ] **Step 8: 新增 §10 Digital Life 运行时**

```markdown
## 10. Digital Life 运行时

`/v1/digital-life`（`digital_life_routes.py`，175行）提供 18 个端点：

| 类别 | 端点 | 说明 |
|---|---|---|
| 状态 | `GET /state`、`GET /runtime/health`、`GET /environment/current` | 完整状态快照 |
| 器官 | `GET /organs/state` | 23 个器官的运行时状态 |
| 驱动 | `GET /inner-drive/current`、`GET /embodiment/current`、`GET /action/current` | 内在驱动与体现 |
| 生命周期 | `POST /tick`、`POST /advance` | 手动 tick/推进 |
| Proactive | `POST /proactive/attempt`、`GET /proactive/recent`、`GET /proactive/episodes/recent` | 主动发起回路 |
| 经验 | `GET /experience/recent`、`POST /experience/ingest` | 经验管理 |
| 成长 | `GET /growth/state`、`GET /learning/recent`、`GET /causality/recent` | 成长与因果 |

Proactive 回路涉及：`runtime/autonomous_self.py`(1,009行) → `persona/proactive_voice.py`(1,477行) → `persona/emotional_proactive.py`(845行) → Edge push。

另有 `/obs/proactive-autonomy`（`obs_routes_proactive_autonomy.py`，94行）提供 proactive autonomy 快照。
```

- [ ] **Step 9: 更新 §11 已知仍需同步的文档**

将 `dataflow_report.md` 从"待同步"列表中移除（因为 Task 2 会同步它）。

- [ ] **Step 10: 提交**

```bash
git add docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md
git commit -m "docs: rewrite CURRENT_CODE_ARCHITECTURE v4.2 — add proactive, pentest dual-planner, surface pipeline, tribe scale, digital life 18 endpoints"
```

---

### Task 2: 重写 dataflow_report.md (v3.3 → v4.0)

**Files:**
- Modify: `docs/50_project/arch/dataflow_report.md`
- Reference: `host_brain/pentest/agent/cognitive_engine.py`, `host_brain/pentest/adversarial/game_loop.py`, `host_brain/pentest/swarm/coordinator.py`, `host_brain/pentest/online/safety_gate.py`, `host_brain/persona/surface/candidate_scoring.py`, `host_brain/runtime/autonomous_self.py`

**变更要点：**

- [ ] **Step 1: 更新头部**

```markdown
受众：MASTER
标题：当前数据流报告

版本：v4.0
日期：2026-06-30
定位：基于当前仓库代码，全面描述所有主流量路径。
```

- [ ] **Step 2: 更新代码证据入口（§0）**

补充 pentest、persona surface、runtime proactive 相关文件路径：

```markdown
Pentest Agent：
- `host_brain/pentest/agent/cognitive_engine.py`
- `host_brain/pentest/agent/evolution_core.py`
- `host_brain/pentest/agent/tool_registry.py`
- `host_brain/pentest/adversarial/game_loop.py`
- `host_brain/pentest/swarm/coordinator.py`
- `host_brain/pentest/online/safety_gate.py`

Persona Surface 管线：
- `host_brain/persona/surface/candidate_scoring.py`
- `host_brain/persona/surface/scoring/*`

Proactive Autonomy：
- `host_brain/runtime/autonomous_self.py`
- `host_brain/runtime/digital_life_supervisor.py`
- `host_brain/persona/proactive_voice.py`
- `host_brain/persona/emotional_proactive.py`
```

- [ ] **Step 3: 新增 §14 Pentest Agent Cognitive Loop 数据流**

```markdown
## 14. Pentest Agent Cognitive Loop

```text
PentestAgentKernel (autonomous_kernel.py)
  → CognitiveEngine.generate_hypotheses()  (LLM 假设生成)
  → BeliefModel.update()                    (信念加权评分)
  → GoalModel.select_action()               (目标驱动选择)
  → ToolRegistry.resolve()                  (capability→tool 解析)
  → guarded executor                        (scope/rate/approval guard)
  → EvolutionCore.observe()                 (结果观察)
  → EvolutionCore.reflect()                 (反思)
  → EvolutionCore.adapt()                   (策略适应)
  → EvolutionCore.evolve()                  (演化)
  → loop
```

三条规划路径选择：
- `PENTEST_COGNITIVE_KERNEL=1` → CognitiveEngine（假设生成 + 信念评分）
- `PENTEST_COGNITIVE_KERNEL=0` → MCTSPlanner 或 LLMReActPlanner
- `agent/compat.py:LegacyKernelAdapter` 负责切换
```

- [ ] **Step 4: 新增 §15 Adversarial Game Loop 数据流**

```markdown
## 15. Adversarial Game Loop

```text
AdversarialHook (adversarial_kernel.py, 注入到 _observe_success)
  → GameLoop.run_round()
    → RedMutationEngine.mutate()    (payload/tool/timing/route 变异)
    → BlueDetector.evaluate()       (检测评估)
    → RedMutationEngine.adapt()     (红队适应)
    → BlueLearner.learn()           (蓝队规则学习)
    → AdversarialScoring.score()    (对抗评分)
  → StrategyCoevolution.evolve()    (遗传算法：pop=20, elite=4, crossover=0.7, mutation=0.15)
  → AdversarialArchive.record()     (演化档案)
```
```

- [ ] **Step 5: 新增 §16 Swarm Coordinator 消息流**

```markdown
## 16. Swarm Coordinator 消息流

```text
SwarmCoordinator (coordinator.py)
  → TaskDistributor.shard()          (按 IP/CIDR/service/phase 分片)
  → AgentLifecycleManager.spawn()    (agent 生命周期)
  → MessageBus.publish()             (pub/sub 消息总线, 500 msg buffer)
  → RoleSpecialization.assign()      (RECON/VULN/EXPLOIT/POST/AD/GENERAL/BLUE/MANAGER)
  → GraphMerger.merge()              (AttackGraph 合并 + 冲突解决)
  → SwarmResult
```
```

- [ ] **Step 6: 新增 §17 Online Safety Gate 三层控制流**

```markdown
## 17. Online Safety Gate 三层控制流

```text
SafetyGate.check()
  → ONLINE_SCAN_MODE check      (仅 offline | manual，无 auto)
  → AuthorizationManager.verify() (bug_bounty | pentest_contract | internal_lab)
  → RateGovernor.check()         (硬编码限速：google_dork 10/day, shodan 30/min, censys 20/min, dns 100/min)
  → TargetQueue.enqueue()        (仅手动添加，不自动发现)
```
```

- [ ] **Step 7: 新增 §18 Persona Surface Scoring 管线数据流**

```markdown
## 18. Persona Surface Scoring 管线

```text
SurfaceMainChain.run()
  → SurfaceRuntime.generate()              (LLM 调用，生成候选回复)
  → CandidateScoring.score_candidates()     (候选评分入口)
    → scoring/parse.py                      (候选解析，问题预算计数)
    → scoring/stage_positions.py            (场景位置评分：back/morning/low-energy/self-doubt)
    → scoring/control_affect.py             (控制情感评分：情绪基调 + 关系距离)
    → scoring/texture.py                    (纹理评分：签名纹理命中)
  → 最终选择最高分候选
```

Inner-state leakage 路径：
```text
OfflineInnerWorld.tick()
  → InnerWorldState (loneliness, anticipation, mood...)
  → ReplyControlPacket (inner_weather, body_state_hint, care_signal, continuity_signal, tension_hint, liveliness, active_loops)
  → IsolatedSurfaceInput
  → SurfaceRuntime → LLM 生成
```
```

- [ ] **Step 8: 新增 §19 Proactive Autonomy 回路**

```markdown
## 19. Proactive Autonomy 回路

```text
DigitalLifeSupervisor.tick_loop()
  → autonomous_self.apply_autonomous_self_cycle()
    → compute_dialogue_state_closure()     (8 维自适应特质评分)
    → build_adaptive_self_pack()           (curiosity, assertiveness, playfulness...)
  → PersonaProactiveVoice.evaluate()       (场景感知：LATE_NIGHT, LONG_ABSENT, POST_INTENSE, TOPIC_FOLLOWUP, MORNING_GREET, HOLIDAY_GREET)
  → EmotionalProactiveEpisode              (冲动追踪)
  → proactive_voice.generate()
  → ProactiveExpression
  → life_continuity.record_proactive_outbox()
  → Edge RPC proactive_push
```

API 端点：
- `POST /v1/digital-life/proactive/attempt` — 手动触发
- `GET /v1/digital-life/proactive/recent` — 近期 outbox
- `GET /v1/digital-life/proactive/episodes/recent` — 近期 episodes
- `GET /obs/proactive-autonomy` — proactive autonomy 快照
```

- [ ] **Step 9: 更新 §5 记忆 SSOT — 反映 life_memory/ 子包分解**

在现有 §5 中补充：

```markdown
`host_brain/memory/life_memory/` 子包（7 文件）：
- `store.py`(3,275行) — LifeMemoryStore 主存储
- `semantic.py`(2,411行) — 语义记忆操作
- `consolidator.py`(925行) — LifeMemoryConsolidator
- `sleep.py`(183行) — MemorySleepReplayTask
- `utils.py`(1,664行) — 辅助工具
- `constants.py`(172行) — 真值状态、episode 类型、保留层级
- `models.py`(22行) — 数据模型

顶层 `memory/` 包（16 文件）为通用记忆子系统：canon 世界存储(763行)、ChromaDB 客户端(783行)、episodic 记忆(1,000行)、蒸馏(1,238行)、上下文压缩(440行)、记忆价值评估(206行)。
```

- [ ] **Step 10: 提交**

```bash
git add docs/50_project/arch/dataflow_report.md
git commit -m "docs: rewrite dataflow_report v4.0 — add pentest cognitive/adversarial/swarm/online flows, persona scoring pipeline, proactive autonomy circuit, life_memory decomposition"
```

---

## 第 2 层：模块参考层

### Task 3: 更新 CORE_MODULES_DETAILED_REFERENCE.md (v4.1 → v4.2)

**Files:**
- Modify: `docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md`

- [ ] **Step 1: §3 Persona — 补齐遗漏的 25+ 文件**

将现有 3.1（20 文件）扩展为完整清单：
- 补充 proactive_voice.py(1,477行)、emotional_proactive.py(845行)
- 补充 self_model.py(1,323行)、evolution_orchestrator.py(964行)
- 补充 inner_activity_agent.py(1,829行)、positive_style_bank.py(928行)
- 补充 growth_feedback_collector.py(584行)、interaction_growth_analyzer.py(535行)
- 补充 interaction_pattern_analyzer.py(558行)、organ_influence_contract.py(445行)
- 补充 surface_organ_influence.py(789行)、surface_influence_packet.py(759行)
- 补充 surface_affect_control.py(570行)、surface_original_quote_bank.py(1,311行)
- 补充 isolated_surface_continuity.py(507行)
- 补充 galgame_text_contract.py(1,631行)、galgame_text_scorer.py(383行)
- 补充 sample_guided_text_scorer.py(1,203行)
- 补充 assistant_followup_surface.py、soul_reply_surface.py、surface_low_info_guidance.py
- 补充 image_social_surface.py、runtime_labels.py、affect_policy.py、fallback_policy.py

- [ ] **Step 2: §3.4 surface/scoring/ — 补充管线描述**

```markdown
### 3.4 `persona/surface/scoring/` 子包（6 文件, ~1,395 行）

| 模块 | 行数 | 当前职责 |
|---|---|---|
| `parse.py` | 262 | 候选解析、问题预算计数、控制违规检测 |
| `stage_positions.py` | 406 | 场景位置评分（back/morning/low-energy/self-doubt/positive） |
| `control_affect.py` | 398 | 控制情感评分（情绪基调 + 关系距离） |
| `texture.py` | 273 | 纹理评分（签名纹理命中 + 场景质量样本评分） |
| `constants.py` | 37 | 本地评分常量 |
| `__init__.py` | 19 | Re-export facade |
```

- [ ] **Step 3: §4 Memory — 更新为 life_memory/ 子包结构**

- [ ] **Step 4: §5 Runtime — 补充 autonomous_self 细节**

- [ ] **Step 5: §6 Tribe — 补充遗漏文件和实际 agent 数量**

- [ ] **Step 6: §9 Pentest agent/ — 补齐遗漏的 10 文件**

- [ ] **Step 7: 提交**

---

### Task 4: 更新 CAPABILITY.md (v2.2 → v2.3)

**Files:**
- Modify: `host_brain/pentest/CAPABILITY.md`

- [ ] **Step 1: §9 数据文件 — 补充 JSON/TXT 数据资产**

- [ ] **Step 2: 新增 §12 agent 内部模块能力矩阵**

- [ ] **Step 3: 提交**

---

### Task 5: 更新 PENTEST_MODULES_ARCHITECTURE.md (v1.0 → v1.1)

**Files:**
- Modify: `docs/50_project/arch/PENTEST_MODULES_ARCHITECTURE.md`

- [ ] **Step 1: §6 自主 Agent — 补齐遗漏的 10 文件**

- [ ] **Step 2: 新增双规划器说明**

- [ ] **Step 3: §10 Data/Report — 标注存根 vs 实际实现**

- [ ] **Step 4: 新增重复类型文档化**

- [ ] **Step 5: 提交**

---

## 第 3 层：入口与索引层

### Task 6: 更新 README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 补充 Digital Life proactive 路由**

- [ ] **Step 2: 更新 Pentest 事实**

- [ ] **Step 3: 验证 Monitor UI 路由数量**

- [ ] **Step 4: 提交**

---

### Task 7: 更新 DOCMAP.md (v5.3 → v5.4)

**Files:**
- Modify: `docs/00_index/00_DOCMAP.md`

- [ ] **Step 1: 更新 LIVING 文档版本号**

- [ ] **Step 2: 验证所有文档路径引用**

- [ ] **Step 3: 提交**

---

## 第 4 层：契约与叙事层

### Task 8: 重写 DevSpec.md (v2.2 → v2.3)

- [ ] 补充 Pentest agent 工程契约
- [ ] 补充 Persona surface/scoring 管线契约
- [ ] 补充 Digital Life proactive 回路契约
- [ ] 提交

### Task 9: 更新 PRD.md (v2.2 → v2.3)

- [ ] FR-SURFACE 更新
- [ ] 新增 FR-PENTEST
- [ ] 新增 FR-PROACTIVE
- [ ] 提交

### Task 10: 验证 Soul.md

- [ ] 对照 personality_traits.py、bond_aware_style.py 验证一致性
- [ ] 提交

### Task 11: 验证 7 份 theory 契约

- [ ] CORE_LAYER_MODULE_REFERENCE.md
- [ ] INTEGRATIONS_LAYER_CONTRACT.md
- [ ] RETRIEVAL_LAYER_CONTRACT.md
- [ ] GOVERNOR_SCHEDULER_CONTRACT.md
- [ ] TOOLS_MEMORY_LAYER_CONTRACT.md
- [ ] PERIPHERAL_LAYERS_CONTRACT.md
- [ ] APP_AUDIT_SKILLS_CONTRACT.md
- [ ] 提交

---

## Self-Review

**1. Spec coverage:** 每层对应设计规格的一个层级 — ✓
**2. Placeholder scan:** 第 2-4 层的 Step 有概要但缺少详细代码块 — 需要在执行时展开，但这是合理的，因为 2-4 层依赖于第 1 层的输出格式
**3. Type consistency:** 所有路径使用实际存在的文件路径 — ✓

**注意：** Task 3-11 的步骤描述较简略（以 `- [ ] Step N: 描述` 形式），需要在执行到对应 Task 时根据实际代码进一步细化。这是有意为之——第 1 层的输出将确立文档格式模板，后续层复用。
