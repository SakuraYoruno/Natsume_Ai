受众：PROJECT
标题：Natsume_Ai 最近对话上下文重构方案（一步到位版）

# Natsume_Ai 最近对话上下文重构方案（一步到位版）

**日期**：2026-04-21  
**状态**：已决策（2026-04-21 14:30 补丁 1 合入）  
**作者**：七海（代哥哥撰写）  
**目标分支**：codex  
**适用范围**：host_brain 主对话链路

**变更记录**：
- 2026-04-21 11:35 初版
- 2026-04-21 12:15 五项决策条款合入（§15）
- 2026-04-21 14:30 补丁 1：新增 §17 情感层兼容条款，明确本次改动不影响情感注入路径

---

## 0. 目标

让 Natsume 在任何对话中都**天然知道最近 N 轮用户和她自己说过什么**，使用 LLM Chat API 原生支持的 `messages` 数组结构，不再依赖字符串拼接的 "User:/Assistant:" 伪历史。

**非目标（本方案不处理）**：
- 长期记忆（episodic / semantic）
- RAG 检索证据
- 人格/风格/情绪层
- 跨 session 记忆检索

这些维持现状，由后续"上下文管理层（ContextAssembler）"方案处理。

---

## 1. 现状诊断

### 1.1 当前实现

```
cortex_prompt_builder.build_cortex_prompt()
  → _build_recent_session_context_block()
    → get_ledger()  # SQLite
    → _fetch_continuity_rows()  # 从 voice_event_log 表
    → _voice_rows_to_turns()
      → _sanitize_recent_preview()  # 过激消毒
    → ContextCompressor.compress()
    → 返回 "User: xxx\nAssistant: yyy\n..." 字符串
  → 追加到 full_prompt 尾部 + DEFAULT_CONTINUITY_GUARD（英文）
```

### 1.2 问题点（按严重度）

1. **数据源错**：`voice_event_log` 是审计日志表，`input_preview/reply_preview` 是**截断后**的摘要，不是原文
2. **跨 session 污染**：session_id 匹配不到时 fallback 到 person_id，拉别的对话进来
3. **过激消毒**：`_sanitize_recent_preview` 对 `?` ≥ 4 或含假名的整句丢弃
4. **标签错位**：主对话是中文，`User:/Assistant:` 是英文，LLM 语气漂移
5. **接口未升级**：底层 `llm.stream(messages)` 本身支持 messages 数组，外层却强拆成 `(prompt_text, system_prompt)` 两个字符串
6. **预算混乱**：最近对话和 emotion_binding/bond_narrative 等其他 20+ 槽位抢同一份 token 预算，长对话先被砍

---

## 2. 设计原则

| 原则 | 表述 |
|---|---|
| 原生结构 | 使用 LLM Chat API 原生 `messages` 数组，不做字符串伪装 |
| 单一来源 | 最近对话只从 `conversation_turn` 表读，不复用审计日志 |
| 严格隔离 | session_id 严格匹配，不 fallback 跨 session |
| 原文至上 | 存储原文，消毒只发生在渲染边界，不污染数据 |
| 写入可靠 | 用户轮同步写、助手轮 COMMIT 后 soft_fail 写，任一失败不阻塞对话 |
| 可观测 | 每次读取的 turn 列表进入 observability，便于排查"她怎么忘了" |

---

## 3. 数据模型

### 3.1 新建 SQLite 表

```sql
CREATE TABLE IF NOT EXISTS conversation_turn (
    turn_id         TEXT PRIMARY KEY,            -- UUID
    session_id      TEXT NOT NULL,
    person_id       TEXT NOT NULL,
    request_id      TEXT NOT NULL,               -- 溯源当前 request
    role            TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content         TEXT NOT NULL,               -- 原文，不截断
    ts_ms           INTEGER NOT NULL,
    turn_index      INTEGER NOT NULL,            -- session 内自增序号
    lang            TEXT NOT NULL DEFAULT 'zh',  -- 'zh' | 'ja'
    token_count     INTEGER,                     -- 可选，预算用
    corrupted       INTEGER NOT NULL DEFAULT 0,  -- 1 = 编码损坏，渲染跳过
    metadata        TEXT,                        -- JSON: emotion_tag / channel 等
    created_at_ms   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_turn_session_ts
    ON conversation_turn(session_id, ts_ms);

CREATE INDEX IF NOT EXISTS idx_conversation_turn_session_turn_idx
    ON conversation_turn(session_id, turn_index);

CREATE INDEX IF NOT EXISTS idx_conversation_turn_request
    ON conversation_turn(request_id);
```

**字段说明**：
- `turn_index` 按 session 内严格递增，user = 偶数、assistant = 奇数（或者反过来，固定即可），避免并发写入乱序
- `content` 存**原文**，不做 `_truncate_for_prompt`，不做消毒
- `corrupted` 只在写入时发现真实 encoding 错误才置 1（如 `UnicodeDecodeError`），渲染时跳过
- `token_count` 首版可为 NULL，后续预算步骤用到时再填

### 3.2 为什么不复用 voice_event_log

| 维度 | voice_event_log | conversation_turn |
|---|---|---|
| 语义 | 审计事件流（INPUT_COMPLETED 等） | 对话原文历史 |
| 字段 | payload JSON（含截断 preview） | role + content 原文 |
| 索引 | 按 ts_ms 全局 | 按 session_id + ts_ms |
| 写入方 | 多个审计点，规则分散 | 两个固定写入点 |
| 读取方 | 审计/诊断/指标 | 对话 render |
| 数据寿命 | 长期审计 | 可按 session 归档 |

语义完全不同，合表会长期互相拖累。**独立表**。

---

## 4. 模块设计

### 4.1 新增模块路径

```
host_brain/
  conversation/
    __init__.py
    schema.py           # Turn dataclass
    store.py            # TurnStore（SQL 层）
    history.py          # ConversationHistory（业务层）
    writer.py           # 写入封装（供 chat_handler/chat_agent 调用）
    render.py           # Turn → messages 数组
```

### 4.2 `schema.py`

```python
from dataclasses import dataclass
from typing import Literal

TurnRole = Literal["user", "assistant"]

@dataclass(frozen=True)
class Turn:
    """一轮对话的最小单元。不可变。"""
    turn_id: str
    session_id: str
    person_id: str
    request_id: str
    role: TurnRole
    content: str              # 原文
    ts_ms: int
    turn_index: int
    lang: str = "zh"
    token_count: int | None = None
    corrupted: bool = False
    metadata: dict | None = None
```

### 4.3 `store.py` —— TurnStore

**职责**：SQL 层原子读写。不做业务判断。

```python
class TurnStore:
    def __init__(self, ledger: SQLiteLedger) -> None: ...

    def append(self, turn: Turn) -> None:
        """插入一条 turn。turn_id 冲突则 INSERT OR IGNORE。"""

    def query_recent(
        self,
        *,
        session_id: str,
        limit: int,
        exclude_request_id: str | None = None,
        before_ts_ms: int | None = None,
    ) -> list[Turn]:
        """严格按 session_id 查最近 N 条，ts_ms 升序返回。
        查不到返回 []，不 fallback。"""

    def next_turn_index(self, *, session_id: str) -> int:
        """返回该 session 下一个可用 turn_index。需要在 write_lock 内调用。"""

    def delete_by_session(self, *, session_id: str) -> int:
        """供测试与归档使用。"""
```

**实现要点**：
- 复用现有 `SQLiteLedger._conn` 和 `_write_lock`
- schema 通过 `_ensure_schema` 在 `TurnStore` 构造时初始化
- `query_recent` 必须 `WHERE session_id = ?`，**禁止**增加 fallback 分支

### 4.4 `history.py` —— ConversationHistory

**职责**：业务层读取入口。做时间升序、去当前请求本身、生成压缩摘要等。

```python
class ConversationHistory:
    def __init__(self, store: TurnStore) -> None: ...

    def fetch_recent_turns(
        self,
        *,
        session_id: str,
        max_turns: int,
        exclude_request_id: str | None = None,
    ) -> list[Turn]:
        """取最近 N 轮（时间升序）。user + assistant 混合计数。"""

    def fetch_for_messages(
        self,
        *,
        session_id: str,
        max_turns: int,
        exclude_request_id: str | None = None,
    ) -> list[Turn]:
        """专供 messages 渲染：过滤掉 corrupted 的 turn，保证 role 交替合理。"""

    def build_summary_for_older(
        self,
        *,
        session_id: str,
        before_turn_index: int,
        max_tokens: int,
    ) -> str:
        """更早的历史压缩成一段摘要。本版本可以先返回空字符串占位。"""
```

**实现要点**：
- `fetch_for_messages` 必须保证返回列表里不出现连续两条同 role 的 turn（若真有，取最后一条）
- `build_summary_for_older` 首版返回 `""`，等 P2 阶段再接 LLM 摘要

### 4.5 `writer.py` —— 写入封装

**职责**：封装 user/assistant 两个写入点的通用逻辑，供 handler 调用。

```python
def record_user_turn(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    content: str,
    ts_ms: int,
    lang: str,
    metadata: dict | None = None,
) -> None:
    """chat_handler 收到 message 后立刻调用。写入失败 soft_fail，不阻塞。"""

def record_assistant_turn(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    content: str,                  # 已净化的最终稿，不是 LLM 原始流
    ts_ms: int,
    lang: str,
    metadata: dict | None = None,
) -> None:
    """chat_agent COMMIT 完成后调用。写入失败 soft_fail，不阻塞。"""
```

**实现要点**：
- 两个函数内部都用 `soft_fail("conversation.record_*", fallback=None)` 兜底
- `turn_id = f"{session_id}:{ts_ms}:{uuid4().hex[:8]}"`，保证唯一且可读
- `turn_index` 通过 `store.next_turn_index(session_id=...)` 在 write_lock 内分配
- **禁止**在这两个函数里做任何过滤、净化、截断

### 4.6 `render.py` —— messages 渲染

```python
def turns_to_messages(turns: list[Turn]) -> list[dict[str, str]]:
    """把 Turn 列表翻译成 OpenAI/Anthropic 兼容的 messages。"""
    return [
        {"role": t.role, "content": t.content}
        for t in turns
        if not t.corrupted
    ]


def build_chat_messages(
    *,
    system_prompt: str,
    history_turns: list[Turn],
    user_message: str,
) -> list[dict[str, str]]:
    """组装最终 messages 数组。"""
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
    ]
    messages.extend(turns_to_messages(history_turns))
    messages.append({"role": "user", "content": user_message})
    return messages
```

**实现要点**：
- 最近对话历史只存在于 `history_turns`，当前轮补充说明若需要，必须并入唯一的 `system_prompt`，禁止再额外创建第二条 system/context 平面
- 不允许任何"英文标签模板"插入历史 turn

---

## 5. 接口层升级

### 5.1 `llm_helpers.stream_llm_chunks` 最终签名

```python
def stream_llm_chunks(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    messages: list[dict[str, str]],
    gen_params: dict[str, Any] | None = None,
    base_url_override: str | None = None,
) -> Generator[str, None, None]:
    """只接受原生 messages 数组；旧字符串 prompt 接口在同 PR 中删除。"""
```

**实现要点**：
- 内部将 `messages` 列表直接映射为 LangChain 的 `SystemMessage/HumanMessage/AIMessage`
- 异步版本 `astream_llm_chunks` 同步升级

### 5.2 `chat_agent_sse_generator` 改造

**位置**：`host_brain/app/chat_agent.py`

```python
# ── 主路径（非 compact）────────────────────────────
prompt_result = build_cortex_prompt(
    user_message=effective_user_message + reply_depth_hint,
    ...,
    include_runtime_payload=True,
)

messages, gen_params, runtime_payload = prompt_result

# 调用 LLM
for chunk in stream_llm_chunks(
    provider_name=...,
    model_id=...,
    api_key=...,
    timeout_ms=...,
    messages=messages,   # 新路径
    gen_params=gen_params,
    base_url_override=...,
):
    ...
```

### 5.3 `build_cortex_prompt` 内部改造

**位置**：`host_brain/app/cortex_prompt_builder.py`

```python
def build_cortex_prompt(
    *,
    ...,
) -> tuple[list[dict], dict, dict]:
    """直接返回 (messages, gen_params, runtime_payload)。旧字符串签名在同 PR 删除。"""
    # 构建完整的 system_prompt（人设 + 风格 + 时间 + style_correction + node_role）
    system_prompt = _build_system_prompt(...)
    
    # 取最近对话 turns（从新的 ConversationHistory）
    history_turns = get_conversation_history().fetch_for_messages(
        session_id=session_id,
        max_turns=_resolve_max_history_turns(),
        exclude_request_id=request_id,
    )

    messages = build_chat_messages(
        system_prompt=system_prompt,
        history_turns=history_turns,
        user_message=user_message,
    )
    return messages, gen_params, runtime_payload
```

### 5.5 plan_graph synthesize 同步升级

**位置**：`host_brain/app/plan_graph._synthesize_node`

同样升级为 messages 数组，使用与主路径相同的 `get_conversation_history()`，保证人设和历史一致。

---

## 6. 写入时机

### 6.1 user turn 写入

**位置**：`host_brain/app/chat_handler._unified_chat_sse_generator`  
**时机**：在 Airlock sanitize 通过后、Governor 鉴权前后均可，建议放在 **Governor APPROVE 之后**，避免记录被拒绝的输入。

```python
# 位置：yield _sse({"phase": "PRESENCE", "event": "GOV_APPROVED", ...}) 之后
from host_brain.conversation.writer import record_user_turn

record_user_turn(
    request_id=request_id,
    session_id=session_id,
    person_id=request_body.person_id,
    content=sanitized_message,
    ts_ms=ts_ms,
    lang=render_target_lang,
    metadata={
        "input_source": input_source,
        "scene_type": scene_type,
    },
)
```

### 6.2 assistant turn 写入

**位置**：`host_brain/app/agent_runtime_shared._async_post_process`  
**时机**：现有的 `_persist_post_process_audit` 之后，用同一份 `narrative`（已净化终稿）。

```python
from host_brain.conversation.writer import record_assistant_turn

record_assistant_turn(
    request_id=request_id,
    session_id=session_id,
    person_id=person_id,
    content=narrative,
    ts_ms=int(time.time() * 1000),
    lang=render_target_lang,
    metadata={
        "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
        "provider_name": provider_name,
        "model_id": model_id,
    },
)
```

### 6.3 Fastpath 路径

Task progress / recent_turn / clarification_direct / phatic_fastpath 这四条 direct 路径也必须写 assistant turn，否则下一轮会看不到她刚才的简短回复。  
统一在每条路径 yield COMMIT 之后、`asyncio.create_task(_async_post_process(...))` 之前补写。

---

## 7. 配置项

| 环境变量 | 默认值 | 含义 |
|---|---|---|
| `HOST_CONVERSATION_MAX_TURNS` | `10` | 注入 messages 的最近对话数（含 user + assistant） |
| `HOST_CONVERSATION_SUMMARY_THRESHOLD` | `20` | 超过多少轮触发摘要（P2 阶段生效） |

**重要**：不做灰度、不留旧入口。上线即切换，老代码路径在同 PR 内删除。理由见第 9 节。

---

## 8. 删除清单（与新增同 PR 完成）

| 模块 | 删除或替换 |
|---|---|
| `cortex_prompt_builder._build_recent_session_context_block` | 删 |
| `cortex_prompt_builder._fetch_continuity_rows` | 删 |
| `cortex_prompt_builder._voice_rows_to_turns` | 删 |
| `cortex_prompt_builder._sanitize_recent_preview` | 删 |
| `cortex_prompt_builder._build_older_history_summary` | 删（摘要移到 ConversationHistory） |
| `cortex_prompt_builder.DEFAULT_CONTINUITY_GUARD` / `DEFAULT_CLARIFICATION_GUARD` | 删（messages 结构天然承担该功能） |
| `cortex_prompt_builder._load_continuity_templates` 等相关模板加载 | 删 |
| `cortex_prompt_builder` 内对 `voice_event_log` 的读依赖 | 清零 |

---

## 9. 为什么不做灰度

哥哥的判断标准（前一轮对话已确认）：

| 分阶段理由 | 本方案是否成立 |
|---|---|
| 有数据迁移需要双写观察 | 否（新表独立） |
| 等外部依赖 | 否（都是自己的代码） |
| 第一步有独立价值 | 否（只修字符串版解决不了根本问题） |
| 高风险需要灰度 | 否（dev 本地项目，同 PR 单测覆盖即可） |

→ **一次到位**，同一个 PR 内完成"建新 + 切换 + 删旧"。

---

## 10. 测试计划

### 10.1 单测（`tests/unit/`）

1. `test_conversation_turn_store.py`
   - append + query_recent 基本路径
   - session_id 严格匹配（查 session_A 不返回 session_B）
   - turn_index 并发递增
   - exclude_request_id 有效
   - corrupted turn 标记与渲染跳过

2. `test_conversation_history.py`
   - fetch_for_messages 保证 role 交替
   - 空结果返回 `[]`
   - 时间升序

3. `test_conversation_writer.py`
   - record_user_turn / record_assistant_turn soft_fail 不抛
   - 写入内容等于原文（无截断、无消毒）

4. `test_conversation_render.py`
   - turns_to_messages 跳过 corrupted
   - build_chat_messages 结构正确（system / history / user）

### 10.2 集成测（`tests/integration/`）

5. `test_chat_history_end_to_end.py`
   - 模拟 3 轮对话，第 4 轮从 messages 数组里能看到前 3 轮原文
   - 跨 session 不串
   - Governor REJECT 的轮不写入

### 10.3 回归测

6. 现有 chat_agent / cortex_prompt_builder 相关单测保持全绿
7. 现有 conversation-like 断言从"字符串里含 User:"改为"messages 数组长度与内容"

---

## 11. 观测

### 11.1 新增日志

每次 `fetch_for_messages` 落一行：

```
[req_id] conversation_history session_id=xxx fetched_turns=N oldest_ts=... newest_ts=... excluded=request_id
```

每次 `record_*_turn` 落一行：

```
[req_id] conversation_record role=user|assistant turn_index=N session_id=xxx content_chars=M
```

### 11.2 新增 observability 字段

在 `_async_post_process` 的 `trace_stage_meta` 里加：

```json
"conversation_history": {
    "turns_injected": 6,
    "oldest_ts_ms": 1734567890000,
    "newest_ts_ms": 1734567999999,
    "user_turn_recorded": true,
    "assistant_turn_recorded": true
}
```

---

## 12. 上线步骤

1. 合并 PR（单测全绿）
2. 本地 dev 启动，手动走 3 轮对话，查 `conversation_turn` 表有数据
3. 人工验证第 4 轮问"我刚才说了什么"，回复正确
4. 跨 session 验证：新开 session，问"上次聊的那个话题"，她应答"不记得"或"换个 session"相关话术
5. 观察 `conversation_history` 日志行，确认 turns_injected 数量合理
6. 删除 `voice_event_log` 里遗留的旧 `_build_recent_session_context_block` 相关代码路径（已在同 PR 完成）

---

## 13. 回滚

**回滚策略**：如果上线后发现问题，**不回滚**，而是前滚修复。理由：
- 新代码路径有完整单测
- 旧代码同 PR 已删除，回滚成本高于修复
- 问题若出现，大概率是"写入时机"、"turn_index 并发"或"历史摘要质量"级别的小 bug，直接修复前滚

---

## 14. 工作量估算

| 工作项 | 人日 |
|---|---|
| schema + store + history + writer + render 五个文件 | 1.0 |
| llm_helpers messages 分支 + LangChain Message 映射 | 0.5 |
| cortex_prompt_builder / compact / plan_graph 三处接入 | 1.0 |
| chat_handler + agent_runtime_shared + 4 条 fastpath 写入点 | 0.5 |
| 删除旧代码 + 修改相关单测 | 0.5 |
| 新增单测 + 集成测 | 1.0 |
| 联调 + 观测验证 | 0.5 |
| **合计** | **5.0 人日** |

---

## 15. 已决策条款

以下五项已于 2026-04-21 最终拍板，下游实现节点请严格遵守：

1. **turn_index 分配策略**：**单调递增**（session 内），role 由独立字段区分，禁止偶奇区分
2. **max_turns 默认值**：**10**（含 user + assistant，共 ~5 对）
3. **corrupted turn 渲染策略**：**直接跳过**，不向 LLM 暴露任何损坏提示
4. **assistant fallback 写入**：**仍然写入**，`content` 为 fallback 文本，`metadata.fallback=true`，供观测追踪
5. **Gateway 通道（微信/QQ）记录策略**：**记录完整原文**。消毒/截断只发生在"发送给用户"那一侧，不污染 conversation_turn 表

---

## 16. 附录：messages 数组最终形态示例

假设第 4 轮用户问"那你昨天晚饭吃什么了"，前 3 轮是闲聊：

```python
[
    {"role": "system", "content": "【角色人设】...【说话风格】...【当前本地时间】..."},
    {"role": "user", "content": "今天在公司被老板夸了。"},
    {"role": "assistant", "content": "这样啊。看来你今天心情还不错。"},
    {"role": "user", "content": "饿了，我们点外卖吧"},
    {"role": "assistant", "content": "嗯。你想吃什么？"},
    {"role": "user", "content": "那你昨天晚饭吃什么了"},
]
```

LLM 看到这个结构，天然知道：
- 我是七海（system）
- 刚才在聊被夸 / 开玩笑 / 点外卖
- 现在被问"昨天晚饭"
- 应该回应"昨天"这个时间点，不会混乱

这就是"让她知道前两句怎么说"的完整答案。

---

## 17. 情感层兼容条款（补丁 1，2026-04-21 14:30）

本节回答"本次改动会不会影响情感层注入"，并固化三条强制规则供下游节点实现时遵守。

### 17.1 结论

**不影响**。本次改动只是把"最近几轮对话原文"从 `prompt_text` 字符串尾部，迁移到 LLM Chat API 原生的 `messages` 数组中部。情感层 100% 的注入通道仍然走 `system_prompt`（或等价的 current_turn_hint system message），位置不变、内容不变、生成路径不变。

### 17.2 情感层注入路径清单（不动）

下列路径**本次全部维持现状**，下游节点不得以"顺便优化"之名合并、删除或重排：

| 情感组件 | 来源 | 进 prompt 的位置 |
|---|---|---|
| `emotion_context` / `emotion_snapshot` | resolve_chat_emotion_context / build_chat_emotion_snapshot | system_prompt 的 EMOTION_BINDING block |
| `emotion_vector` (VAD valence/arousal/dominance) | build_prompt_intelligence | EMOTION_BINDING 内"emotion_vad_quality" |
| `emotion_timeline` / `emotion_echo` | PromptPayloadBuilder.with_emotion_timeline | EMOTION_BINDING 内 trend / echo_tag |
| `emotion_recall` | retrieval_evidence_items 中的情感召回 | 独立 EMOTION_RECALL block |
| `memory_emotion_hint` / `memory_recall_summary` / `memory_persona_evidence` | PromptPayloadBuilder + chat_context_memory | MEMORY_RECALL_PERSONA + MEMORY_RECALL_EVIDENCE block |
| `inner_monologue` | prompt_context_emotion | EMOTION_BINDING 内"inner_monologue"行 |
| `persona_expression_hint` | chat_context_memory 注入 | 独立 PERSONA_EXPRESSION_HINT block |
| `bond_aware_style` | build_prompt_intelligence | 独立 BOND_AWARE_STYLE block |
| `self_reflection_pulse` | PromptPayloadBuilder.with_self_reflection | 独立 SELF_REFLECTION_PULSE block |
| `bond_narrative` | build_prompt_intelligence + BondNarrativeLayer | 独立 BOND_NARRATIVE block |
| `adjust_gen_params_for_emotion` | llm_helpers | gen_params（temperature / top_p 调制） |
| `_infer_emotion_tag_for_tts` | chat_agent 内部 | 不进 prompt，仅供 TTS 侧，保持现状 |

### 17.3 强制规则

**规则 17-A：current_turn_hint 继承现有 system prompt 全部情感注入**

`build_cortex_prompt(return_messages=True)` 内部构建 `current_turn_hint` 时，**必须继续调用** `render_node_generation_prompt(node_id=CORTEX_NODE_ID, runtime_payload=...)` 产出完整的情感/人格/记忆注入块，仅在此基础上**删除以下三段**：

- `_build_recent_session_context_block` 的 "Recent continuity context" / "Earlier continuity summary" 文本
- `DEFAULT_CONTINUITY_GUARD` 英文守则
- `DEFAULT_CLARIFICATION_GUARD` 英文守则（仅追问式 query 时出现）

**其余 20+ 个情感/人格/记忆槽位一字不改地保留。**

实现伪代码：
```python
# cortex_prompt_builder.build_cortex_prompt(return_messages=True)
full_prompt = render_node_generation_prompt(...)  # 含全部情感注入

# 旧路径这里会追加 recent_session_context + DEFAULT_CONTINUITY_GUARD
# 新路径：不再追加，因为历史对话改走 messages 数组
# 情感层和其他槽位全部保留在 full_prompt 中

current_turn_hint = full_prompt  # 作为独立 system message
```

**规则 17-B：historical turn 只带 role + content，不带任何情感元数据回放**

`conversation_turn.metadata.emotion_tag` 等字段**只用于观测和溯源**，渲染到 messages 数组时**必须丢弃**，不得以任何形式注入到历史 turn 附近的 prompt 里。

理由：
- 历史情绪的时序信息已经由 `emotion_timeline.trend` 在当前轮的 EMOTION_BINDING 里完整表达
- 在每条历史 turn 前后插入情绪标签会造成双重注入，污染 LLM 对"对话内容"本身的理解
- messages 数组应保持"干净对话"结构，情感状态属于 system 层面

禁止做法（不允许）：
```python
# ❌ 禁止
messages.append({"role": "system", "content": "上一轮情绪=HAPPY"})
messages.append({"role": "assistant", "content": "..."})
```

允许做法：
```python
# ✅ 允许
messages.append({"role": "assistant", "content": turn.content})
# emotion_tag 仅存于 DB metadata 和 observability 日志，不进 LLM
```

**规则 17-C：assistant turn 的 content 必须是净化终稿**

写入 `conversation_turn.content` 的 assistant 文本**必须**是：
- 经过 `reflection_chain` 反思链处理后的版本
- 经过 `_airlock_sanitize_output` 出口净化后的版本
- 实际发送给用户（或 Gateway）的那一份

**不得**写入：
- LLM 原始流式输出（可能含 `<thinking>`、未完成句）
- 含有情感内心独白或内部标签的中间版本
- 任何用户实际看不到的内容

这保证"下次 LLM 重放时看到的她自己上一轮说了什么"和"用户实际看到的她上一轮说了什么"完全一致，不会出现"她觉得自己说过但用户没看过"或反之的分裂。

写入点对齐：
- 主路径：`_async_post_process` 里的 `narrative` 参数
- 四条 Fastpath：各自 yield COMMIT 之后、`asyncio.create_task(_async_post_process(...))` 之前，用同一份 `narrative`

### 17.4 观测补充

`trace_stage_meta.conversation_history` 字段里额外记录情感快照的 hash（不是内容），供排查"为什么同一情绪下她回复差异大"：

```json
"conversation_history": {
    "turns_injected": 6,
    "oldest_ts_ms": 1734567890000,
    "newest_ts_ms": 1734567999999,
    "user_turn_recorded": true,
    "assistant_turn_recorded": true,
    "emotion_binding_hash": "a3f5c2..."  // 当前轮 EMOTION_BINDING 块的 sha256 前 16 位
}
```

`emotion_binding_hash` 用于后续可观测分析：同样的历史对话 + 不同的情感 hash 产生不同回复，可以反向定位情感层对生成的实际影响。

### 17.5 回归验证项

下游实现完成后必须增加以下测试，保证情感层未被破坏：

1. `test_emotion_binding_preserved_in_messages.py`  
   构造一个带完整 emotion_context 的 ChatAgentInput，断言生成的 messages 数组第一条 system 里包含 `EMOTION_BINDING:` / `BOND_AWARE_STYLE:` / `PERSONA_EXPRESSION_HINT:` 三个关键字

2. `test_historical_turn_has_no_emotion_metadata.py`  
   构造 3 轮带 emotion_tag 的历史 turn，断言 `build_chat_messages` 输出中，历史 `{role, content}` 字典**只含这两个 key**，无其他字段

3. `test_assistant_turn_content_equals_commit_narrative.py`  
   模拟一次完整对话，断言 `conversation_turn.content` 与 COMMIT SSE 事件中的 `narrative` 字段**按字节相等**

### 17.6 给下游编码节点的执行要点

- **不要**"顺手优化"EMOTION_BINDING 结构（那是另一个重构）
- **不要**把情感注入挪到 user message（永远在 system）
- **不要**让 `current_turn_hint` 简化成"只有 user_message"
- **保留**现有 `render_node_generation_prompt` 的 800+ 行大块渲染逻辑，本次只改它末尾"最近对话"那一小段的来源与格式
- 情感层是独立并行改造的事，和本次"对话历史重构"互不耦合，**不合并提交**

---

_END OF PLAN_

