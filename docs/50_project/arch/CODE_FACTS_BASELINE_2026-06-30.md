=== CODE FACTS BASELINE 2026-06-30 ===

## Host Brain 总览
- Total .py files: 788

## 子目录规模
- app: 141 files, 80303 lines
- persona: 58 files, 44910 lines
- persona/surface: 11 files, 6577 lines
- persona/surface/scoring: 6 files, 1395 lines
- pentest: 50 files, 29764 lines
- pentest/agent: 31 files, 8856 lines
- pentest/adversarial: 9 files, 1374 lines
- pentest/swarm: 8 files, 1164 lines
- pentest/online: 5 files, 689 lines
- pentest/tools: 6 files, 8023 lines
- pentest/data: 5 files, 1942 lines
- pentest/report: 4 files, 173 lines
- pentest/utils: 6 files, 1168 lines
- runtime: 6 files, 5550 lines
- tribe: 34 files, 23534 lines
- memory: 16 files, 7420 lines
- memory/life_memory: 8 files, 8696 lines
- governor: 10 files, 2617 lines
- skills/impl: 26 files, 10023 lines

## Pentest agent/ 完整文件清单 (按行数降序)
```
  8856 total
   796 host_brain/pentest/agent/tool_registry.py
   784 host_brain/pentest/agent/cognitive_engine.py
   693 host_brain/pentest/agent/evolution_core.py
   534 host_brain/pentest/agent/online_research.py
   433 host_brain/pentest/agent/opsec.py
   416 host_brain/pentest/agent/goal_model.py
   394 host_brain/pentest/agent/belief_model.py
   362 host_brain/pentest/agent/planner.py
   352 host_brain/pentest/agent/situation_analyzer.py
   333 host_brain/pentest/agent/attack_planner.py
   323 host_brain/pentest/agent/exploit_engine.py
   319 host_brain/pentest/agent/threat_intel.py
   316 host_brain/pentest/agent/knowledge.py
   290 host_brain/pentest/agent/payload_factory.py
   288 host_brain/pentest/agent/meta_cognition.py
   256 host_brain/pentest/agent/strategy_escalator.py
   225 host_brain/pentest/agent/playbook.py
   215 host_brain/pentest/agent/biz_logic_probe.py
   213 host_brain/pentest/agent/attack_chain.py
   199 host_brain/pentest/agent/blue_agent.py
   190 host_brain/pentest/agent/schemas.py
   189 host_brain/pentest/agent/vuln_chain.py
   122 host_brain/pentest/agent/action_generator.py
   118 host_brain/pentest/agent/vector_store.py
   116 host_brain/pentest/agent/payload_variants.py
   102 host_brain/pentest/agent/reward.py
    77 host_brain/pentest/agent/compat.py
    75 host_brain/pentest/agent/budget.py
    48 host_brain/pentest/agent/llm_brain.py
    47 host_brain/pentest/agent/kernel.py
    31 host_brain/pentest/agent/__init__.py
```

## Persona surface/ + scoring/ 完整文件清单 (按行数降序)
```
  7972 total
  1912 host_brain/persona/surface/candidate_scoring.py
  1587 host_brain/persona/surface/scene_constants.py
  1140 host_brain/persona/surface/continuity_detectors.py
   717 host_brain/persona/surface/prompt_construction.py
   406 host_brain/persona/surface/scoring/stage_positions.py
   398 host_brain/persona/surface/scoring/control_affect.py
   360 host_brain/persona/surface/relationship_affect.py
   273 host_brain/persona/surface/scoring/texture.py
   262 host_brain/persona/surface/scoring/parse.py
   251 host_brain/persona/surface/scoring_texture.py
   226 host_brain/persona/surface/fact_handling.py
   210 host_brain/persona/surface/data_models.py
   112 host_brain/persona/surface/input_validation.py
    56 host_brain/persona/surface/util.py
    37 host_brain/persona/surface/scoring/constants.py
    19 host_brain/persona/surface/scoring/__init__.py
     6 host_brain/persona/surface/__init__.py
```

## Pentest 根目录关键大文件 (top 20 by lines)
```
  29764 total
   4269 host_brain/pentest/action_policy.py
   3280 host_brain/pentest/autonomous_kernel.py
   2273 host_brain/pentest/kali_bridge.py
   1409 host_brain/pentest/phase3_auth.py
   1164 host_brain/pentest/phase4_executor.py
   1128 host_brain/pentest/phase3_injection.py
   1111 host_brain/pentest/mcts_planner.py
    983 host_brain/pentest/phase3_access_control.py
    815 host_brain/pentest/phase3_business_logic.py
    753 host_brain/pentest/phase5_executor.py
    710 host_brain/pentest/llm_react_planner.py
    651 host_brain/pentest/phase3_data_exposure.py
    579 host_brain/pentest/phase3_xss.py
    576 host_brain/pentest/reverse_shell.py
    564 host_brain/pentest/phase3_misconfig.py
    547 host_brain/pentest/autonomous_evidence.py
    507 host_brain/pentest/kali_parser.py
    492 host_brain/pentest/phase6_report.py
    482 host_brain/pentest/phase3_websocket_graphql.py
```

## Tribe agent 文件规模 (按行数降序)
```
 21349 total
  4014 host_brain/tribe/thalamus_agent.py
  1922 host_brain/tribe/limbic_agent.py
  1584 host_brain/tribe/prefrontal_agent.py
  1469 host_brain/tribe/immune_agent.py
  1119 host_brain/tribe/tribe_state.py
  1116 host_brain/tribe/social_brain_agent.py
  1051 host_brain/tribe/ans_agent.py
   817 host_brain/tribe/pain_reward_agent.py
   781 host_brain/tribe/hippocampus_agent.py
   780 host_brain/tribe/cortex_agent.py
   664 host_brain/tribe/motor_cortex_agent.py
   619 host_brain/tribe/language_system_agent.py
   515 host_brain/tribe/default_mode_agent.py
   506 host_brain/tribe/peripheral_agent.py
   481 host_brain/tribe/endocrine_agent.py
   469 host_brain/tribe/circadian_agent.py
   462 host_brain/tribe/mirror_neuron_agent.py
   453 host_brain/tribe/glymphatic_agent.py
   446 host_brain/tribe/cerebellum_agent.py
   432 host_brain/tribe/proprioception_agent.py
   432 host_brain/tribe/interoception_agent.py
   422 host_brain/tribe/somatic_agent.py
   420 host_brain/tribe/initiative_governor_agent.py
   348 host_brain/tribe/vestibular_agent.py
    27 host_brain/tribe/base_agent.py
```

## 路由注册 (router_registry.py include_router 调用)
```
57:    app.include_router(canon_router)
58:    app.include_router(scheduler_router)
59:    app.include_router(host_scheduler_router, prefix="/host-scheduler")
60:    app.include_router(tool_router)
61:    app.include_router(tool_kits_alias_router)
62:    app.include_router(prompt_config_router)
63:    app.include_router(bond_router)
64:    app.include_router(agent_router)
65:    app.include_router(input_router)
66:    app.include_router(internal_tribe_router)
67:    app.include_router(internal_codex_router)
68:    app.include_router(internal_security_immune_router)
69:    app.include_router(digital_life_router)
70:    app.include_router(memory_router)
71:    app.include_router(mateengine_router)
72:    app.include_router(pentest_router)
73:    app.include_router(health_router)
```

## Digital Life 端点
```
32:@router.get("/state")
33:async def digital_life_state(person_id: str = Query("default")) -> dict[str, Any]:
37:@router.get("/runtime/health")
38:async def digital_life_runtime_health(person_id: str = Query("default")) -> dict[str, Any]:
42:@router.get("/environment/current")
43:async def digital_life_environment_current(person_id: str = Query("default")) -> dict[str, Any]:
47:@router.get("/causality/recent")
48:async def digital_life_causality_recent(person_id: str = Query("default"), limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
52:@router.get("/growth/state")
53:async def digital_life_growth_state(person_id: str = Query("default")) -> dict[str, Any]:
57:@router.get("/learning/recent")
58:async def digital_life_learning_recent(person_id: str = Query("default"), limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
62:@router.post("/work-event")
63:async def digital_life_work_event(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
80:@router.post("/tick")
81:async def digital_life_tick(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
90:@router.post("/advance")
91:async def digital_life_advance(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
102:@router.get("/inner-drive/current")
103:async def digital_life_inner_drive_current(person_id: str = Query("default"), force: bool = Query(False)) -> dict[str, Any]:
113:@router.get("/embodiment/current")
114:async def digital_life_embodiment_current(person_id: str = Query("default")) -> dict[str, Any]:
118:@router.get("/action/current")
119:async def digital_life_action_current(person_id: str = Query("default")) -> dict[str, Any]:
123:@router.post("/proactive/attempt")
124:async def digital_life_proactive_attempt(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
137:@router.get("/proactive/recent")
138:async def digital_life_proactive_recent(person_id: str = Query("default"), limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
142:@router.get("/organs/state")
143:async def digital_life_organs_state(person_id: str = Query("default")) -> dict[str, Any]:
147:@router.get("/experience/recent")
148:async def digital_life_experience_recent(person_id: str = Query("default"), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
152:@router.post("/experience/ingest")
153:async def digital_life_experience_ingest(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
170:@router.get("/proactive/episodes/recent")
171:async def digital_life_proactive_episodes_recent(person_id: str = Query("default"), limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
```

## Memory life_memory/ 子包文件
```
  8696 total
  3275 host_brain/memory/life_memory/store.py
  2411 host_brain/memory/life_memory/semantic.py
  1664 host_brain/memory/life_memory/utils.py
   925 host_brain/memory/life_memory/consolidator.py
   183 host_brain/memory/life_memory/sleep.py
   172 host_brain/memory/life_memory/constants.py
    44 host_brain/memory/life_memory/__init__.py
    22 host_brain/memory/life_memory/models.py
```

## Runtime 文件
```
  5550 total
  1920 host_brain/runtime/life_continuity.py
  1235 host_brain/runtime/digital_life_supervisor.py
  1224 host_brain/runtime/life_organs.py
  1009 host_brain/runtime/autonomous_self.py
   147 host_brain/runtime/environment_state.py
    15 host_brain/runtime/__init__.py
```

## Governor 文件
```
 2617 total
  661 host_brain/governor/rate_limit_store.py
  583 host_brain/governor/decision.py
  341 host_brain/governor/schemas.py
  315 host_brain/governor/tokens.py
  265 host_brain/governor/rules.py
  132 host_brain/governor/route_token.py
  120 host_brain/governor/rate_limit_checker.py
  111 host_brain/governor/tier_policy.py
   88 host_brain/governor/policy_cache.py
    1 host_brain/governor/__init__.py
```

=== END BASELINE ===
