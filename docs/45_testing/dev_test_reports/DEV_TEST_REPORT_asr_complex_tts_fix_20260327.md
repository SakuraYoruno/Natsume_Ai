受众：MASTER
标题：ASR 复杂场景 TTS 语音播报缺失修复

# DEV_TEST_REPORT — ASR 复杂场景 TTS 语音播报缺失修复

**日期**: 2026-03-27
**开发节点**: DEV (v_wenloliu workstation)
**修改文件**:
- `host_brain/app/chat_handler_complex.py` — 方案A：补上 TTS 调用
- `host_brain/app/graph/input_source_strategy.py` — 方案B：ASR post_chain 兜底
- `host_brain/app/graph/main_chain.py` — 提取 TTS 执行状态到 CommitResult
- `.env.example` — 新增 2 个配置项

---

## 1. 问题描述

### 现象
ASR 语音输入被 `pre_intent_classifier` 分类为非 CHAT 场景（WORK_CODE / EXEC_INTENT / WORK_TRANSLATE）时，LLM 生成的 narrative 正常产出，但**用户听不到任何语音回复**。

### 根因
`_complex_scene_sse_generator`（chat_handler_complex.py）在 COMMIT 阶段有 VTS 表情驱动，但**完全没有 TTS 语音播报逻辑**。而 `_sse_event_generator`（chat_stream_handler.py，SCENE_CHAT 专用）在 COMMIT 阶段有完整的 `_run_tts() → post_speak()` 调用。

### 影响链路
```
用户语音 "帮我写一段Python代码"
  → ASR 转写 → POST /v1/input (source=ASR)
  → pre_intent_classifier → SCENE_WORK_CODE
  → SceneRouter → _complex_scene_sse_generator
  → narrative 生成 ✅
  → VTS 表情 ✅
  → TTS 语音 ❌ ← 这里断了
  → AsrInputSourceStrategy.post_chain() → 空实现
  → narrative 被丢弃，用户听不到回复
```

---

## 2. 修复方案

### 方案 A（根因修复）：_complex_scene_sse_generator 补上 TTS

**位置**: `chat_handler_complex.py` L759-810（VTS 之后、COMMIT 之前）

**逻辑**:
1. 读取 `suppress_edge_tts` 标志（尊重输入策略）
2. 检查 `HOST_STREAM_TTS_ENABLED` 环境变量（全局开关）
3. 检查 narrative 是否非空
4. 调用 `_infer_emotion_tag_for_tts()` 推断情感标签
5. 调用 `post_speak()` 发送 TTS 请求
6. 将 `tts_summary` 写入 `audit_refs`（审计追踪）
7. 异常时降级（DEGRADED），不阻断主链路

### 方案 B（安全网）：AsrInputSourceStrategy.post_chain() 兜底 TTS

**位置**: `input_source_strategy.py` AsrInputSourceStrategy.post_chain()

**逻辑**:
1. `CommitResult` 新增 `tts_already_fired: bool` 字段
2. `run_main_chain_from_input` 从 COMMIT 事件的 `audit_refs.tts` 提取 TTS 状态
3. `post_chain()` 检测：
   - `tts_already_fired == True` → 跳过（避免双重播报）
   - `suppress_edge_tts == True` → 跳过
   - narrative 为空 → 跳过
   - `HOST_ASR_POST_CHAIN_TTS_FALLBACK=false` → 手动禁用
   - `HOST_STREAM_TTS_ENABLED=false` → 全局禁用
4. 以上都不满足时，调用 `post_speak()` 兜底播报
5. 异常时仅 warning 日志，不影响链路

### 双保险机制
```
正常路径（方案A生效）：
  _complex_scene_sse_generator → TTS ✅ → audit_refs.tts.status="OK"
  → CommitResult.tts_already_fired=True
  → AsrInputSourceStrategy.post_chain → 检测到已播报 → 跳过

异常路径（方案A失败/未触发）：
  _complex_scene_sse_generator → TTS 降级/跳过
  → CommitResult.tts_already_fired=False
  → AsrInputSourceStrategy.post_chain → 检测到未播报 → 兜底执行 TTS ✅
```

---

## 3. 新增配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `HOST_STREAM_TTS_ENABLED` | `true` | 全局 TTS 开关（已存在但未在 .env.example 中注册） |
| `HOST_ASR_POST_CHAIN_TTS_FALLBACK` | `true` | ASR 兜底 TTS 安全网开关 |

---

## 4. 待真机验证测试用例

### T-ASR-TTS-01：ASR + SCENE_CHAT（基线确认，应无影响）
- **操作**: 语音说"今天天气怎么样"
- **预期**: pre_intent → SCENE_CHAT → _sse_event_generator → TTS ✅
- **验证点**: 修复前后行为一致，无双重播报

### T-ASR-TTS-02：ASR + SCENE_WORK_CODE（核心修复验证）
- **操作**: 语音说"帮我写一段Python排序代码"
- **预期**: pre_intent → SCENE_WORK_CODE → _complex_scene_sse_generator → TTS ✅（方案A）
- **验证点**: 用户能听到语音回复；日志中有 "complex scene TTS" 相关记录

### T-ASR-TTS-03：ASR + SCENE_EXEC_INTENT
- **操作**: 语音说"帮我打开浏览器"
- **预期**: pre_intent → SCENE_EXEC_INTENT → _complex_scene_sse_generator → TTS ✅
- **验证点**: TTS 正常播报

### T-ASR-TTS-04：ASR + SCENE_WORK_TRANSLATE
- **操作**: 语音说"把这段话翻译成英文"
- **预期**: pre_intent → SCENE_WORK_TRANSLATE → _complex_scene_sse_generator → TTS ✅
- **验证点**: TTS 正常播报

### T-ASR-TTS-05：兜底安全网验证
- **操作**: 临时在 _complex_scene_sse_generator 中注释掉方案A的 TTS 代码，发送语音
- **预期**: 方案A未执行 → tts_already_fired=False → post_chain 兜底 TTS ✅
- **验证点**: 日志中有 "asr post_chain TTS fallback firing" 记录

### T-ASR-TTS-06：Gateway 场景无影响确认
- **操作**: 微信发送"帮我写代码"
- **预期**: source=GATEWAY → GatewayInputSourceStrategy → suppress_tts=True → TTS 不触发
- **验证点**: 方案A的 suppress_edge_tts 检查正确跳过；方案B不触发（非 ASR 策略）

### T-ASR-TTS-07：环境变量控制
- **操作**: 设置 `HOST_ASR_POST_CHAIN_TTS_FALLBACK=false`，重复 T-ASR-TTS-02
- **预期**: 方案A正常执行 TTS；方案B被环境变量禁用（日志 "fallback disabled"）
- **验证点**: 仅方案A播报，无双重播报

---

## 5. 降级与安全

| 场景 | 降级行为 |
|------|---------|
| post_speak() 连接失败 | 方案A: TTS DEGRADED，COMMIT 正常输出；方案B: warning 日志 |
| 方案A + 方案B 都失败 | narrative 仍在 COMMIT 事件中，但用户听不到语音 |
| _infer_emotion_tag_for_tts 异常 | 降级为 NEUTRAL |
| HOST_STREAM_TTS_ENABLED=false | 两方案均跳过，符合预期 |
| suppress_edge_tts=True（Gateway） | 两方案均跳过，符合预期 |

---

## 6. 状态

| 修改 | 状态 | lint |
|------|------|------|
| chat_handler_complex.py — 方案A | ✅ 完成 | 0 errors |
| input_source_strategy.py — 方案B | ✅ 完成 | 0 errors |
| main_chain.py — CommitResult 提取 | ✅ 完成 | 0 errors |
| .env.example — 配置同步 | ✅ 完成 | — |
| 真机验证 | ⏳ 待运行节点 | — |
