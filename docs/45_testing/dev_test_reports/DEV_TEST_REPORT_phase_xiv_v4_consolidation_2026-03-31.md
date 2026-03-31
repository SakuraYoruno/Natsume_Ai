受众：CODEX
标题：Phase XIV v4 归并修复实测报告

# DEV_TEST_REPORT — Phase XIV v4 旧链路能力归并

- **日期**：2026-03-31
- **开发节点**：DEV (v_wenloliu workstation)
- **触发**：哥哥指出新链路缺少旧链路多个关键能力，要求全面排查并归并
- **Commit**：待填写

## 问题诊断

全面对比 `chat_stream_handler._sse_event_generator`（旧链路）与 `top_agent.top_agent_sse_generator`（新链路），发现以下缺失：

### P0（严重）
| 缺失 | 影响 | 修复方式 |
|-------|------|---------|
| `build_prompt_intelligence()` 未被调用 | predictive_state / self_reflection / bond_narrative / persona_expression_hint 等 6+ 个 prompt 槽位为空 | 改为走 `build_chat_prompt_runtime_payload()` → `render_node_generation_prompt()` 完整管线 |
| `apply_response_intelligence` 缺失 | 高风险对话不经反思链审查改写，AI 话术痕迹不清理，无多模态后缀 | 在 LLM 输出后插入完整反思链 |

### P1（重要）
| 缺失 | 影响 | 修复方式 |
|-------|------|---------|
| SentenceSplitter + SENTENCE 事件 | 前端无法做句子级 TTS 实时跟读 | 归并 SentenceSplitter |
| 反思缓冲 `_should_buffer_stream_output` | 高风险对话（情感敏感）直接流式推送 | 归并缓冲判断逻辑 |
| Airlock 出口安全分类 `classify_ingress` | AI 回复不经过内容安全分类 | 移入异步后处理 |
| TTS bond_summary 未传入 | TTS 语调不受 Bond 阶段影响 | 传入 bond_summary |
| TTS emotion_intensity 缺失 | TTS 缺少情感强度调制 | 传入完整 emotion_intensity |
| _infer_emotion_tag_for_tts 缺失 | NEUTRAL 时不做回复文案情感推断 | 归并推断逻辑 |

### P2（次要）
| 缺失 | 影响 | 修复方式 |
|-------|------|---------|
| thread_id=None 硬编码 | 多轮对话线程跟踪断裂 | 从 request_body 传入 |
| gen_params 未走 adjust_gen_params_for_emotion | 情感调制不完整 | 在 prompt 构建中调用 |
| THINKING SSE 事件缺失 | 前端不知道 Agent 在思考 | 加入 PRESENCE/THINKING 事件 |
| COMMIT 缺少 response_intelligence 摘要 | 运维无法看到反思链结果 | 加入精简版摘要 |

## 修改文件

| 文件 | 改动 |
|------|------|
| `top_agent_prompt.py` | **重写**：改为走 `build_chat_prompt_runtime_payload()` 完整管线，确保 `build_prompt_intelligence()` 被调用；增加 thread_id 参数；gen_params 走 `adjust_gen_params_for_emotion` |
| `top_agent.py` | **重写**：归并 SentenceSplitter / SENTENCE 事件 / apply_response_intelligence / 反思缓冲 / Airlock 安全分类 / _infer_emotion_tag_for_tts / THINKING 事件 / thread_id / bond_summary→TTS / emotion_intensity→TTS |

## 与旧链路完整对等性验证

| 能力 | 旧链路 | 新链路（归并后） |
|------|--------|------------------|
| 角色人设 character_profile | ✅ | ✅ |
| 说话风格 style_canon v5 | ✅ | ✅ |
| 台词参考 persona_examples | ✅ | ✅ |
| Bond 感知 bond_aware_style | ✅ | ✅ |
| 情感绑定（完整 VAD + echo） | ✅ | ✅ |
| 内心独白 inner_monologue | ✅ | ✅（via build_prompt_intelligence） |
| 自省脉冲 self_reflection | ✅ | ✅（via build_prompt_intelligence） |
| 预测状态 predictive_state | ✅ | ✅（via build_prompt_intelligence） |
| Bond 叙事 bond_narrative | ✅ | ✅（via build_prompt_intelligence） |
| 人格表达策略 persona_expression | ✅ | ✅（via build_prompt_intelligence） |
| 世界观 world_canon | ✅ | ✅（via StateArbiter） |
| episodic_vector | ✅ | ✅（via StateArbiter） |
| RecallPack 槽位预算 | ✅ | ✅ |
| SentenceSplitter + SENTENCE | ✅ | ✅（归并） |
| apply_response_intelligence | ✅ | ✅（归并） |
| 反思缓冲 | ✅ | ✅（归并） |
| Airlock 出口安全分类 | ✅ | ✅（异步后处理） |
| TTS bond_summary 调制 | ✅ | ✅（归并） |
| TTS emotion_intensity | ✅ | ✅（归并） |
| 情感推断 _infer_emotion | ✅ | ✅（归并） |
| thread_id | ✅ | ✅（归并） |
| THINKING 事件 | ✅ | ✅（归并） |
| Episodic 写入 | ✅ | ✅ |
| Bond 更新 | ✅ | ✅ |
| VTS 情感驱动 | ✅ | ✅ |
| TTS 语音播报 | ✅ | ✅ |

### 有意移除（不归并）

| 能力 | 旧链路 | 理由 |
|------|--------|------|
| 完整 Governor 鉴权 | R1-R7 | 闲聊不需要（v4 设计意图） |
| persist_chat_observability_rows | 25+ 表 | 过度审计（v4 设计意图） |
| COMMIT 16+ 字段 audit_refs | 3-8KB | 调试数据不发前端（v4 设计意图） |
| 53 个 audit_tags | 全链路 | 过度审计 |
| prompt_context_validation | COMMIT | 调试数据 |
| prompt_context_observability | COMMIT | 调试数据 |

## RUN 节点测试用例

| # | 场景 | 验证点 | 预期 |
|---|------|--------|------|
| 1 | 开关关闭 | `HOST_TOP_AGENT_ENABLED=false` | 走旧链路，行为不变 |
| 2 | 开关打开 + 简单闲聊 | "今天天气不错" | THINKING → RENDER(chunk) + SENTENCE → COMMIT |
| 3 | 开关打开 + 情感对话 | "今天好难过" | 反思缓冲生效 → 反思链改写 → 一次性推送 |
| 4 | 开关打开 + 代码请求 | "写一个快排" | DELEGATE → code_agent → 结果回填 |
| 5 | 验证 SENTENCE 事件 | 任意闲聊 | SSE 中有 phase=SENTENCE 事件 |
| 6 | 验证反思链 | 检查 COMMIT.audit_refs.response_intelligence | 有 reflection 状态 |
| 7 | 验证 TTS 情感调制 | 用 sad 情感标签 | TTS 语速/音调有变化 |
| 8 | 验证 prompt 槽位 | 开启 DEBUG 日志，检查 prompt 文本 | 包含 PREDICTIVE_STATE / SELF_REFLECTION / BOND_NARRATIVE 等 |
| 9 | 验证 thread_id | 多轮对话 | request_body.thread_id 正确传入 |
| 10 | 验证安全分类 | 检查 DEBUG 日志 | classify_ingress 被调用 |
