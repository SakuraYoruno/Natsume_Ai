受众：CODEX
标题：Phase XIV 父子级 Agent 拟人化渲染 — 开发实测报告

# DEV_TEST_REPORT — Phase XIV 父子级 Agent 拟人化渲染

> **日期**: 2026-03-31
> **节点**: DEV (v_wenloliu workstation)
> **分支**: codex

---

## 1. 任务概述

实现父子级 Agent 拟人化渲染架构：让所有复杂场景（WORK_CODE / WORK_TRANSLATE / EXEC_INTENT / VISION_UI）的回复经过 SCENE_CHAT 完整 prompt 管线拟人化渲染，确保用户感知到的始终是夏目蓝在说话。

### 问题背景

| 场景 | 改造前 | 改造后 |
|------|--------|--------|
| SCENE_CHAT | ✅ 完整拟人化 | ✅ 不变 |
| SCENE_GATEWAY (CHAT) | ✅ 完整拟人化 | ✅ 不变 |
| WORK_CODE | ❌ "你是代码专家" | ✅ 子Agent做事 → 父Agent渲染 |
| WORK_TRANSLATE | ❌ "你是翻译专家" | ✅ 子Agent做事 → 父Agent渲染 |
| EXEC_INTENT | ❌ "你是执行意图专家" | ✅ 子Agent做事 → 父Agent渲染 |
| VISION_UI | ❌ 无角色人设 | ✅ 子Agent分析 → 父Agent渲染 |

---

## 2. 变更清单

### 新增文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `host_brain/app/handlers/persona_rendering_orchestrator.py` | ~330 | 核心模块：场景感知指令模板 + render_with_persona() + 降级策略 |

### 修改文件

| 文件 | 改动点 |
|------|--------|
| `host_brain/app/chat_handler_complex.py` | narrative 提取后插入 Phase XIV 拟人化渲染阶段（~45 行），COMMIT audit_refs 注入 persona_rendering_meta |
| `host_brain/app/prompt_builder.py` | 新增 `PERSONA_RENDERER_NODE_ID` 常量 + `_build_node_role_instruction` 渲染节点分支 |
| `.env.example` | 新增 4 个配置项（HOST_PERSONA_RENDERING_*） |
| `docs/00_index/00_DOCMAP.md` | 注册方案文档 |

### 不改动文件

| 文件 | 原因 |
|------|------|
| `chat_stream_handler.py` | SCENE_CHAT 已完整，不需改 |
| `gateway_orchestrator_handler.py` | 非 CHAT 子场景委托 _complex_scene，间接走渲染 |
| `code_agent_handler.py` / `translate_agent_handler.py` / `intent_agent_handler.py` | 子 Agent 保持专家角色不变 |
| `scene_router.py` | 路由逻辑不变 |

---

## 3. 开发节点已验证项

### 3.1 静态分析 ✅

- [x] `persona_rendering_orchestrator.py` — 0 lint errors
- [x] `chat_handler_complex.py` — 0 lint errors（改动后）
- [x] `prompt_builder.py` — 0 lint errors（改动后）
- [x] `.env.example` — 格式正确，注释完整

### 3.2 代码逻辑审查 ✅

- [x] `render_with_persona()` 复用 `_build_stream_chat_prompt()` — 与 SCENE_CHAT / GATEWAY 完全对齐
- [x] 场景感知指令模板 — 4 种场景 + 1 个兜底模板
- [x] 降级策略 — 渲染失败/LLM不可用/输出为空 均降级返回原始 narrative
- [x] 配置开关 — `HOST_PERSONA_RENDERING_ENABLED` 默认 true，可热关闭
- [x] 可观测性 — COMMIT audit_refs 中注入 `persona_rendering` 字段
- [x] 原始 narrative 截断 — `HOST_PERSONA_RENDERING_MAX_RAW_CHARS` 默认 4000 字符
- [x] LLM 运行时 — 以 `scene_type="CHAT"` 解析，复用 CHAT 的模型路由

### 3.3 架构一致性 ✅

- [x] 新模块位于 `host_brain/app/handlers/` — 与 `gateway_orchestrator_handler.py` 同级
- [x] 延迟导入模式 — 避免循环依赖
- [x] 降级不阻断 — 所有 try-except 保护，异常时返回原始 narrative
- [x] 环境变量命名规范 — `HOST_PERSONA_RENDERING_*` 前缀统一
- [x] .env.example 同步 — 4 个新配置项全部注册

---

## 4. 真机实测清单（RUN 节点）

### 4.1 基本功能测试

| # | 测试场景 | 操作 | 预期结果 | 通过 |
|---|---------|------|---------|------|
| 1 | WORK_CODE 拟人化 | 通过微信发"帮我写一个冒泡排序" | 回复像夏目蓝（不像技术客服），代码原样保留 | [ ] |
| 2 | WORK_TRANSLATE 拟人化 | 通过微信发"帮我翻译：I love you" | 回复像夏目蓝，译文原样保留 | [ ] |
| 3 | EXEC_INTENT 拟人化 | 通过微信发"帮我打开记事本" | 回复像夏目蓝在告知执行结果 | [ ] |
| 4 | SCENE_CHAT 不受影响 | 通过微信发"今天天气怎么样" | 回复正常（不经过渲染阶段） | [ ] |
| 5 | Gateway CHAT 不受影响 | 通过微信发"你好" | 回复正常 | [ ] |

### 4.2 降级测试

| # | 测试场景 | 操作 | 预期结果 | 通过 |
|---|---------|------|---------|------|
| 6 | 渲染开关关闭 | 设置 `HOST_PERSONA_RENDERING_ENABLED=false`，发代码请求 | 返回原始技术产出（不做渲染） | [ ] |
| 7 | LLM 超时降级 | 设置 `HOST_PERSONA_RENDERING_TIMEOUT_MS=100`（极短超时） | 降级返回原始产出，日志记录 DEGRADED | [ ] |

### 4.3 可观测性验证

| # | 测试场景 | 操作 | 预期结果 | 通过 |
|---|---------|------|---------|------|
| 8 | audit_refs 检查 | 发代码请求，查看 COMMIT 事件 | audit_refs 包含 `persona_rendering` 字段，status=OK | [ ] |
| 9 | 日志检查 | 查看 `Natsume.persona_rendering` logger 输出 | 日志包含 scene、latency_ms、narrative 长度等 | [ ] |

### 4.4 延迟评估

| # | 测试场景 | 操作 | 预期结果 | 通过 |
|---|---------|------|---------|------|
| 10 | 延迟开销 | 发代码请求，对比渲染前后总延迟 | 渲染阶段增加 1-3s，总延迟在可接受范围 | [ ] |

---

## 5. 配置参考

```bash
# 拟人化渲染总开关（默认 true）
HOST_PERSONA_RENDERING_ENABLED=true

# 启用渲染的场景（默认全开）
# HOST_PERSONA_RENDERING_SCENES=WORK_CODE,WORK_TRANSLATE,EXEC_INTENT,VISION_UI

# 子 Agent 产出注入 prompt 最大字符数（默认 4000）
# HOST_PERSONA_RENDERING_MAX_RAW_CHARS=4000

# 渲染 LLM 超时（默认 15000ms）
# HOST_PERSONA_RENDERING_TIMEOUT_MS=15000
```

---

## 6. 关联文档

- 架构方案：`docs/50_project/arch/PARENT_CHILD_AGENT_PERSONA_RENDERING.md`
- DOCMAP：已注册
