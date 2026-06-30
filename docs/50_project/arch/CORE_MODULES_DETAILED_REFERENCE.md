受众：CODEX
标题：核心模块详细参考

# CORE_MODULES_DETAILED_REFERENCE

版本：v4.2 Living
最后同步：2026-06-30

本文件是实现层查代码的模块索引，不替代代码。每个条目只写当前文件实际承担的职责，避免把历史计划或未接线方案写成已实现事实。

## 1. Host App 主链

| 模块 | 当前职责 | 备注 |
|---|---|---|
| `host_brain/app/asgi.py` | 导出 Host ASGI app | 被 `host_brain/__main__.py` 的 uvicorn 入口使用 |
| `host_brain/app/app_factory.py` | 创建 Host FastAPI app、注册 router、注册 `/v1/chat`、`/v1/edge/events`、`/metrics` | `/v1/chat` 不在普通 router registry 中 |
| `host_brain/app/router_registry.py` | include Host routers，注入 ledger/tool/bond/scheduler provider | 当前 include 顺序见 `CURRENT_CODE_ARCHITECTURE.md` |
| `host_brain/app/server_config.py` | HTTP middleware 配置 | app factory 调用 |
| `host_brain/app/http_hooks.py` | request id/audit、启动/关闭生命周期 hook、运行时模块启动 | 含后台模块启动与关闭逻辑 |
| `host_brain/app/http_hooks_inbound.py` | inbound path 过滤与跳过规则 | 包含 `/monitor/`、`/api/ui/` 等路径规则 |
| `host_brain/app/runtime_metrics.py` | RuntimeMetrics 与 `/metrics` 响应 | Host app metrics |

## 2. Chat / Executive / Surface 链路

| 模块 | 当前职责 |
|---|---|
| `host_brain/app/chat_routes.py` | `/v1/chat` 的 wrapper：request id、BioOps preflight、immune gate、调用 `handle_chat` |
| `host_brain/app/chat_handler.py` | 主 SSE generator，协调 governor、runtime、surface 输出和 MateEngine cue |
| `host_brain/app/agent_runtime_shared.py` | 共享 runtime hub、检索预取、上下文记忆 fastpath、sub-agent gateway |
| `host_brain/app/executive_runtime_core.py` | 硬规则路由、skill-first 执行、多步计划、sub-agent dispatch |
| `host_brain/app/executive_runtime.py` | 薄入口，转入 executive runtime core |
| `host_brain/app/executive_router.py` | 薄入口，提供 router/lifecycle |
| `host_brain/app/cortex_orchestrator.py` | 薄入口，连接 cortex prompt builder |
| `host_brain/app/surface_reply_preprocessor.py` | Surface 输入结构、SSE/截断/守卫相关预处理 |
| `host_brain/app/surface_main_chain_adapter.py` | App 层到 Persona Surface 主链的桥接与结果适配 |
| `host_brain/app/surface_reply_runtime.py` | Surface reply runtime 封装 |
| `host_brain/app/surface_reply_postprocessor.py` | PII 脱敏、Broca/TTS 参数映射、输出后处理 |

主链简图：

```text
chat_routes -> chat_handler -> agent_runtime_shared -> executive_runtime_core
  -> surface_main_chain_adapter -> persona.surface_main_chain
```

## 3. Persona / Surface

### 3.1 人格和内在状态

| 模块 | 当前职责 |
|---|---|
| `host_brain/persona/persona_engine.py` | Persona 编排入口，连接特质和表达 hint |
| `host_brain/persona/personality_traits.py` | `PersonalityTraits`、`TraitsDelta` 等特质模型 |
| `host_brain/persona/expression_adapter.py` | 将 trait/emotion/bond 转换为表达 hint |
| `host_brain/persona/bond_aware_style.py` | 羁绊阶段风格适配 |
| `host_brain/persona/inner_world.py` | 离线内心状态、驱动、absence、continuity 计算 |
| `host_brain/persona/inner_activity_agent.py` | LLM 内心活动生成 |
| `host_brain/persona/inner_monologue.py` | 内心独白质量评分与过滤 |
| `host_brain/persona/body_sense.py` | fatigue/drowsiness/loneliness 等体感模型 |
| `host_brain/persona/emotional_silence.py` | 沉默意愿与惩罚 |
| `host_brain/persona/emotional_proactive.py` | 情感驱动主动发起 |
| `host_brain/persona/emotion_reactivity.py` | 情绪反应 |
| `host_brain/persona/emotion_persistence.py` | 情绪跨会话持久化/衰减 |
| `host_brain/persona/self_model.py` | 自我模型快照和成长里程碑 |
| `host_brain/persona/persona_growth.py` | 人格成长接口 |
| `host_brain/persona/emergent_growth_engine.py` | 涌现成长逻辑 |
| `host_brain/persona/evolution_orchestrator.py` | 演化编排 |
| `host_brain/persona/evolution_desire_engine.py` | 演化意愿 |
| `host_brain/persona/trait_drift_guard.py` | 特质漂移守护 |
| `host_brain/persona/drift_detector.py` | 漂移检测 |
| `host_brain/persona/self_reflection.py` | 自我反思 |
| `host_brain/persona/proactive_voice.py` | 场景感知主动表达（LATE_NIGHT/LONG_ABSENT 等 6 种场景），24h per-type 冷却 |
| `host_brain/persona/identity.py` | 规范自我 ID 辅助函数 |
| `host_brain/persona/positive_style_bank.py` | 正向风格样本库 |
| `host_brain/persona/growth_feedback_collector.py` | 成长反馈收集 |
| `host_brain/persona/interaction_growth_analyzer.py` | 交互成长分析 |
| `host_brain/persona/interaction_pattern_analyzer.py` | 交互模式分析 |
| `host_brain/persona/organ_influence_contract.py` | 器官影响契约（23 器官到 Surface 的桥接接口） |
| `host_brain/persona/offline_continuity.py` | 离线连续性辅助 |
| `host_brain/persona/assistant_followup_surface.py` | 助手跟进 Surface |
| `host_brain/persona/soul_reply_surface.py` | Soul 回复 Surface |
| `host_brain/persona/surface_low_info_guidance.py` | 低信息量引导 |
| `host_brain/persona/image_social_surface.py` | 图像社交 Surface |
| `host_brain/persona/affect_policy.py` | 情感策略决策 |
| `host_brain/persona/fallback_policy.py` | 降级策略 |
| `host_brain/persona/runtime_labels.py` | 运行时标签（mood/bond/time context） |

### 3.2 回复规划和质量

| 模块 | 当前职责 |
|---|---|
| `host_brain/persona/reply_plan.py` | 回复规划 |
| `host_brain/persona/reply_tactics.py` | 回复战术选择 |
| `host_brain/persona/reply_validator.py` | 回复质量校验 |
| `host_brain/persona/text_quality_gate.py` | 文本质量门控 |
| `host_brain/persona/galgame_text_contract.py` | Visual Novel 风格文本契约 |
| `host_brain/persona/galgame_text_scorer.py` | Visual Novel 文本评分 |
| `host_brain/persona/galgame_taste_guard.py` | 风格守护 |
| `host_brain/persona/galgame_text_contract.py` | Visual Novel 风格文本契约 |
| `host_brain/persona/galgame_text_scorer.py` | Visual Novel 文本评分 |
| `host_brain/persona/sample_guided_text_scorer.py` | 样本引导文本评分 |
| `host_brain/persona/surface_original_quote_bank.py` | 原始引用库管理与选择 |
| `host_brain/persona/surface_scene_quality_pack.py` | 场景质量样本选择 |
| `host_brain/persona/motive_engine.py` | reach_out / continuity / withdraw / self_protect 动机计算 |
| `host_brain/persona/motive_runtime.py` | 运行时 motive bridge |

### 3.3 Surface 主链

| 模块 | 当前职责 |
|---|---|
| `host_brain/persona/surface_main_chain.py` | SurfaceMainChain 入口 |
| `host_brain/persona/surface_runtime.py` | 直接 LLM 生成、task mode、hard gate 检查 |
| `host_brain/persona/surface_memory_recall.py` | Surface 记忆召回计划、证据组织、answer readiness |
| `host_brain/persona/surface_packet_compiler.py` | `ReplyControlPacket` 编译 |
| `host_brain/persona/surface_original_quote_bank.py` | 原始引用库 |
| `host_brain/persona/surface_scene_quality_pack.py` | Surface 场景质量包 |
| `host_brain/persona/surface_organ_influence.py` | 器官影响到 Surface 的桥接 |
| `host_brain/persona/surface_influence_packet.py` | 7 种子影响类型的 SurfaceInfluencePacket 定义 |
| `host_brain/persona/surface_affect_control.py` | Surface 情感控制包 |
| `host_brain/persona/isolated_surface_continuity.py` | Surface 连续性上下文 |
| `host_brain/persona/continuity_surface_sample_bank.py` | 连续性重写样本库 |

### 3.4 `persona/surface/` 子包（17 文件，~7,972 行）

| 模块 | 行数 | 当前职责 |
|---|---|---|
| `candidate_scoring.py` | 1,912 | 核心候选评分和选择管线 |
| `scene_constants.py` | 1,587 | 场景标记常量（back/morning/low-energy 等） |
| `continuity_detectors.py` | 1,140 | 连续性/边界/场景相关性检测 |
| `prompt_construction.py` | 717 | Surface LLM 调用的 prompt 构造 |
| `relationship_affect.py` | 360 | 关系距离和情感基调评分 |
| `scoring_texture.py` | 251 | 纹理评分桥接（兼容旧接口） |
| `fact_handling.py` | 226 | Fact 处理与 must-not-do 合并 |
| `data_models.py` | 210 | ReplyControlPacket、IsolatedSurfaceInput 等 Pydantic v1/v2 双兼容模型 |
| `input_validation.py` | 112 | Surface 输入校验 |
| `util.py` | 56 | 纯文本/集合工具函数 |
| `scoring/parse.py` | 262 | 候选解析、问题预算计数、控制违规检测 |
| `scoring/stage_positions.py` | 406 | 场景位置评分（back/morning/low-energy/self-doubt/positive） |
| `scoring/control_affect.py` | 398 | 控制情感评分（情绪基调 + 关系距离双轴） |
| `scoring/texture.py` | 273 | 纹理评分（签名纹理命中 + 场景质量样本评分） |
| `scoring/constants.py` | 37 | 本地评分常量（问题预算、嫉妒标记、轴标记） |
| `scoring/__init__.py` | 19 | Re-export facade：parse、stage_positions、control_affect、texture、stage_constants |

## 4. Memory

### 4.1 `life_memory/` 子包（7 文件，8,718 行）

| 模块 | 行数 | 当前职责 |
|---|---|---|
| `store.py` | 3,275 | `LifeMemoryStore` — SQLite 持久化，记忆 SSOT |
| `semantic.py` | 2,411 | 语义记忆操作 |
| `consolidator.py` | 925 | `LifeMemoryConsolidator` |
| `sleep.py` | 183 | `MemorySleepReplayTask` |
| `utils.py` | 1,664 | 辅助工具函数 |
| `constants.py` | 172 | 真值状态（TRUTH_CANDIDATE/VERIFIED）、episode 类型（CHAT/PERCEPTION/ACTION/BACKGROUND/INNER）、保留层级（HOT/WARM/COLD/GONE） |
| `models.py` | 22 | 数据模型 |
| `__init__.py` | 44 | Re-export facade（向后兼容旧 `life_memory.py` 单文件） |

### 4.2 顶层 memory/ 模块

| 模块 | 当前职责 |
|---|---|
| `host_brain/memory/life_memory/store.py` | LifeMemoryStore SQLite + cache |
| `host_brain/memory/life_memory/semantic.py` | SemanticMemoryBuffer |
| `host_brain/memory/life_memory/sleep.py` | SleepCycle / 记忆巩固 |
| `host_brain/memory/life_memory/consolidator.py` | MemoryConsolidator |
| `host_brain/memory/retrieval/recall_fusion.py` | Recall fusion |
| `host_brain/memory/retrieval/full_text_searcher.py` | 全文检索 |
| `host_brain/memory/retrieval/hybrid_fusion.py` | 混合召回 |
| `host_brain/memory/chroma_client.py` | Chroma 集成 |
| `host_brain/memory/canon_world_store.py` | Canon 世界状态存储 |
| `host_brain/memory/context_compressor.py` | 上下文压缩 |
| `host_brain/memory/memory_worth_evaluator.py` | 记忆价值评估 |

## 5. Runtime / Digital Life

| 模块 | 行数 | 当前职责 |
|---|---|---|
| `digital_life_supervisor.py` | 1,235 | 数字生命后台 supervisor，核心 tick loop（inner_world_numeric_tick/monologue_tick/silence_tick/body_sense_tick/emotion_decay_tick） |
| `autonomous_self.py` | 1,009 | 自主自我：8 维 SELF_REWARD_WEIGHTS（continuity/coherence/competence/curiosity/restraint/presence/aesthetic/attachment）、7 维 ADAPTIVE_TRAITS、proactive 结果应用 |
| `life_continuity.py` | 1,920 | 跨会话连续性：SQLite schema（digital_life_state/life_state_event/dialogue_life_influence_log/embodiment_dispatch_log/work_environment_event/proactive_life_outbox）、async life-loop task 基础设施 |
| `life_organs.py` | 1,224 | 23 个器官状态合成与投票：ORGAN_IDS、ORGAN_PROFILES（drive+label）、organ_votes_from_runtime、synthesize_life_state_from_organs |
| `environment_state.py` | 147 | 环境状态：时区（默认 Asia/Shanghai）、day_phase_for_hour、meal_phase_for_time |
| `__init__.py` | 15 | runtime package |

## 6. Tribe

| 模块 | 行数 | 当前职责 |
|---|---|---|
| `registry.py` | 200 | 23 个 family spec、25 个默认 agent descriptor（实际 bootstrap 24）、family resolution |
| `base_agent.py` | 27 | `TribeAgentDescriptor`、BaseAgent、消息/工具能力结构 |
| `event_bus.py` | 374 | `InMemoryTribeEventBus` — pub/sub 事件总线，1,024 事件环形缓冲，async dispatch |
| `events.py` | 31 | `TribeEvent` 类型定义 |
| `handoff.py` | 145 | Agent 间移交协调 |
| `tribe_state.py` | 1,119 | `TribeStateRegistry` — TribeState 聚合 |
| `cortex_route_decider.py` | 131 | Cortex 路由决策 |
| `cortex_context_collector.py` | 441 | Cortex 上下文收集 |
| `cortex_intent_analyzer.py` | 391 | Cortex 意图分析 |
| `edge_command_pusher.py` | 343 | Edge 命令推送（独立运行能力，不在 registry 默认列表） |
| `*_agent.py` (23 个) | ~18,000 | 具体 family agent 实现（见 CURRENT_CODE_ARCHITECTURE §8 大小分布表） |

当前 registry family 列表以 `host_brain/tribe/registry.py::_DEFAULT_FAMILY_SPECS` 为准。

## 7. Governor

| 模块 | 当前职责 |
|---|---|
| `host_brain/governor/decision.py` | Decision、DecisionRequest、DecisionResult、AuditLogEntry |
| `host_brain/governor/rules.py` | GateRule、ResultCodes |
| `host_brain/governor/tier_policy.py` | TierPolicy 与默认 tier |
| `host_brain/governor/policy_cache.py` | PolicyCache |
| `host_brain/governor/rate_limit_store.py` | RateLimitStore |
| `host_brain/governor/rate_limit_checker.py` | RateLimitChecker |
| `host_brain/governor/route_token.py` | RouteToken 和 route detection |
| `host_brain/governor/schemas.py` | Pydantic schema |
| `host_brain/governor/tokens.py` | TokenManager、TokenBudget |

## 8. Skills / Tools

| 模块 | 当前职责 |
|---|---|
| `host_brain/skills/impl/` | 25 个普通 skill executor 文件 |
| `host_brain/skills/registry.py` | Skill registry |
| `host_brain/skills/registration.py` | Skill 注册 helper |
| `host_brain/skills/provider_catalog.py` | provider path 注册 |
| `host_brain/tools/catalog.py` | ToolMeta catalog |
| `host_brain/tools/broker.py` | tool handler broker |
| `host_brain/tools/selector.py` | 工具选择 |
| `host_brain/tools/mcp_adapter.py` | MCP adapter |

Pentest 的工具不在 `host_brain/skills/impl/`；它们位于 `host_brain/pentest/tools/`。

## 9. Pentest

| 模块 | 当前职责 |
|---|---|
| `host_brain/app/pentest_routes.py` | `/v1/pentest` API，run/event/report/approval/knowledge |
| `host_brain/app/handlers/pentest_agent_handler.py` | 授权 ReAct pentest handler |
| `host_brain/app/handlers/pentest_attack_agent_handler.py` | autonomous redteam handler |
| `host_brain/pentest/tools/tool_specs.py` | 88 个 ToolSpec 注册真源 |
| `host_brain/pentest/tools/tool_executors.py` | 92 个 executor，统一 guard 包装 |
| `host_brain/pentest/scope.py` | ScopeGuard |
| `host_brain/pentest/rate_limiter.py` | RateLimiter |
| `host_brain/pentest/executor_guard.py` | ExecutorGuard 与 `guarded()` |
| `host_brain/pentest/manifest.py` | EngagementManifest、HMAC approval token |
| `host_brain/pentest/kali_bridge.py` | 17 个 Kali wrapper class |
| `host_brain/pentest/tools/asset_discovery.py` | Shodan/Censys helper、K8s audit、CI/CD scan |
| `host_brain/pentest/tools/headless_crawler.py` | Playwright/HTTP crawler executor helper |
| `host_brain/pentest/agent/` | 自主 agent 内部框架，31 个 `.py`（8,906 行）：cognitive_engine、belief_model、goal_model、evolution_core、meta_cognition、situation_analyzer、tool_registry、attack_planner、exploit_engine、payload_factory、payload_variants、attack_chain、vuln_chain、opsec、knowledge、threat_intel、online_research、strategy_escalator、playbook、biz_logic_probe、blue_agent、reward、budget、compat、kernel、llm_brain、planner、schemas、action_generator、vector_store、__init__ |
| `host_brain/pentest/adversarial/` | 红蓝对抗，9 个 `.py` |
| `host_brain/pentest/swarm/` | Swarm 协调，8 个 `.py` |
| `host_brain/pentest/online/` | 在线授权和安全 gate，5 个 `.py` |
| `host_brain/pentest/report/` | 报告和合规映射 |
| `host_brain/pentest/data/` | CVE、默认凭证、LOLBAS、NVD client |

## 10. Edge / Gateway / Desktop

| 模块 | 当前职责 |
|---|---|
| `edge_client/__main__.py` | Edge RPC uvicorn 入口 |
| `edge_client/config.py` | EdgeConfig、GatewayConfig 相关 env 映射和默认值 |
| `edge_client/app/fastapi_app.py` | Edge RPC FastAPI app |
| `edge_client/gateway/bootstrap.py` | Unified Gateway 组装和平台 adapter 注册 |
| `edge_client/gateway/server.py` | Gateway FastAPI webhook/reply/health/stats |
| `edge_client/gateway/adapters/*` | wechat/qq/feishu/wecom/telegram/line adapter |
| `desktop_pet/main.cjs` | Electron 桌宠窗口和 HTTP 控制服务 |
| `host_brain/channels/mateengine.py` | MateEngine UDP/HTTP bridge client |

## 11. Monitor UI

| 模块 | 当前职责 |
|---|---|
| `host_brain/app/monitor_ui/src/app/routeManifest.jsx` | 37 条 route manifest、35 条导航 route |
| `host_brain/app/monitor_ui/src/App.jsx` | HashRouter + route manifest 装配 |
| `host_brain/app/monitor_ui/src/routes/routeLoaders.js` | route lazy imports |
| `host_brain/app/static/monitor/` | 构建产物 |
| `scripts/runtime/start_monitor_proxy.ps1` | Nginx `/monitor/` 静态代理和 Host/Obs upstream |

Host app 当前没有直接挂载 Monitor UI 静态目录；运行时通过 monitor proxy 暴露 `/monitor/`。
