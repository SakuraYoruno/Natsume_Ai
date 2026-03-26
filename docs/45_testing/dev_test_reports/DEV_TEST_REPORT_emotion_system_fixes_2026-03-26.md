受众：CODEX
标题：情感系统 6 项 Bug 修复开发实测报告

# DEV_TEST_REPORT: 情感系统 6 项 Bug 修复

- **日期**: 2026-03-26
- **开发节点**: v_wenloliu workstation (DEV)
- **分支**: codex
- **状态**: ✅ DEV 完成，待 RUN 节点验证

---

## 一、修复清单

| # | 优先级 | 问题 | 修复文件 | 状态 |
|---|--------|------|----------|------|
| 1 | ⚠️ 高 | 全局单例线程安全 | `emotion_context.py` | ✅ |
| 2 | ⚠️ 中 | EmotionPersistence 内存字典（进程重启丢失） | `emotion_persistence.py` | ✅ |
| 3 | ⚠️ 中 | emotion_registry 标签集不一致 | `emotion_registry.py` | ✅ |
| 4 | ⚠️ 中 | VAD 映射重复定义（数值差异） | `emotion_constants.py`（新）+ 3 个消费方 | ✅ |
| 5 | 💡 低 | EmotionTimeline/compute_emotion_echo 代码重复 | `metabolism.py` | ✅ |
| 6 | 💡 低 | 衰减参数硬编码 | `metabolism.py` + `emotion_context.py` + `emotion_persistence.py` | ✅ |

---

## 二、修复详情

### Fix #1: 全局单例线程安全（高优先级）

**问题**: `emotion_context.py` 中 `_global_emotion_vector`、`_global_prev_emotion_tag`、`_last_diary_flush_ts`、`_user_emotion_profile` 等模块级全局变量在 FastAPI 异步环境中存在竞态条件。多个并发请求可能同时读写这些变量。

**修复**:
- 引入 `threading.Lock`（`_emotion_lock`）
- `get_persistent_emotion_vector()` 初始化 + 读取加锁
- `_update_persistent_emotion()` 读-衰减-混合-写序列加锁
- `_global_prev_emotion_tag` 更新加锁
- `flush_emotion_diary()` 中 `_last_diary_flush_ts` 读写加锁
- `get_user_emotion_profile()` 初始化加锁

**锁设计原则**:
- 锁粒度尽量小：只保护关键的读-改-写序列
- IO 操作（日志、外部写入）放在锁外
- 使用 `with` 语句确保异常安全

### Fix #2: EmotionPersistence JSON 文件持久化

**问题**: `EmotionPersistence` 使用纯内存字典存储，进程重启后所有跨会话情感状态丢失。

**修复**:
- 内存字典 + JSON 文件双写策略
- 文件路径: `{NATSUME_EMOTION_PERSISTENCE_DIR}/{user_id}.json`，默认 `~/.natsume/emotion/`
- 写入使用原子替换（先写 `.tmp` 再 `rename`）
- 恢复查找顺序：内存缓存 → JSON 文件 → 中性基线
- 添加 `threading.Lock` 保护内存字典
- 文件名安全处理（过滤非法字符防止路径注入）

### Fix #3: emotion_registry 标签集统一

**问题**: `emotion_registry.py` 的 `_VALID_EMOTION_TAGS` 包含 `SLEEPY`/`LONELY` 但缺少 `LOVING`/`FRUSTRATED`/`BORED`/`EMBARRASSED`，与系统其他模块的 16 种标签集不一致。

**修复**: 改为从 `emotion_constants.py` 导入 `VALID_EMOTION_TAGS`，确保全系统标签集一致。

### Fix #4: VAD 映射统一

**问题**: `_EMOTION_TAG_TO_VAD`（emotion_context.py）和 `_USER_EMOTION_MAP`（metabolism.py / emotion_timeline.py）两处独立定义 VAD 映射，数值存在微妙差异。

**修复**:
- 新建 `host_brain/core/emotion_constants.py` 作为唯一权威来源
- 导出: `EMOTION_TAG_TO_VAD`、`VALID_EMOTION_TAGS`、`EMOTION_TAG_ZH`、`get_vad()`
- 所有消费方改为从此处导入

### Fix #5: 消除 ~250 行重复代码

**问题**: `metabolism.py` 和 `emotion_timeline.py` 都有完整的 `EmotionTimeline`、`EmotionTimelineEntry`、`compute_emotion_echo` 实现。

**修复**:
- `emotion_timeline.py` 保留为权威实现
- `metabolism.py` 改为代理导出（`from emotion_timeline import ...`）
- 向后兼容：`from host_brain.core.metabolism import EmotionTimeline` 仍然有效
- 减少约 250 行重复代码

### Fix #6: 衰减参数可配置化

**问题**: 衰减参数全部硬编码，调优需要改代码。

**修复**: 以下参数均可通过环境变量覆盖：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `HOST_EMOTION_DECAY_RATE` | 0.05 | 单次 decay() 衰减率 |
| `HOST_EMOTION_DECAY_GRACE_MS` | 30000 | 会话内不衰减的宽限期（ms） |
| `HOST_EMOTION_DECAY_MAX_RATE` | 0.85 | 会话内最大衰减率 |
| `HOST_EMOTION_DECAY_WINDOW_MIN` | 120 | 达到最大衰减率的窗口（分钟） |
| `HOST_EMOTION_PERSISTENCE_DECAY_RATE` | 0.1 | 跨会话衰减率（~7h 半衰期） |
| `HOST_EMOTION_BASELINE_VALENCE` | 0.0 | 跨会话基线效价 |
| `HOST_EMOTION_BASELINE_AROUSAL` | 0.3 | 跨会话基线唤醒度 |
| `HOST_EMOTION_BASELINE_DOMINANCE` | 0.0 | 跨会话基线支配感 |
| `NATSUME_EMOTION_PERSISTENCE_DIR` | `~/.natsume/emotion/` | 持久化文件目录 |

---

## 三、验证

### DEV 节点静态验证

| 检查项 | 结果 |
|--------|------|
| `emotion_context.py` lint | ✅ 0 errors |
| `emotion_persistence.py` lint | ✅ 0 errors |
| `emotion_registry.py` lint | ✅ 0 errors |
| `emotion_constants.py` lint | ✅ 0 errors |
| `metabolism.py` lint | ✅ 0 errors |
| `emotion_timeline.py` lint | ✅ 0 errors |
| 向后兼容（导入路径） | ✅ 所有现有导入路径不受影响 |

### 待 RUN 节点验证

- [ ] `pytest tests/unit/test_emotional_timeline_m29.py` — 情感时间线单元测试
- [ ] `pytest tests/unit/ -k emotion` — 所有情感相关单元测试
- [ ] 线程安全压测（多并发请求）
- [ ] EmotionPersistence 进程重启恢复测试
- [ ] VTS 表情联动端到端测试

---

## 四、变更文件列表

| 文件 | 操作 | 行数变化 |
|------|------|---------|
| `host_brain/core/emotion_constants.py` | **新增** | +78 |
| `host_brain/app/emotion_context.py` | 修改 | ~+30 / -30 |
| `host_brain/persona/emotion_persistence.py` | 修改 | ~+80 / -15 |
| `host_brain/skills/emotion_registry.py` | 修改 | ~+2 / -5 |
| `host_brain/core/metabolism.py` | 修改 | ~+10 / -250 |
| `host_brain/core/emotion_timeline.py` | 修改 | ~+5 / -8 |

**净效果**: 减少约 200 行重复代码，新增 78 行共享常量模块。

---

## 五、风险评估

- **低风险**: Fix #3/#4/#6 为纯数据/配置变更，不影响逻辑
- **中风险**: Fix #1 线程锁引入可能影响性能（锁粒度已最小化），需要压测验证
- **中风险**: Fix #2 文件 IO 引入可能在高并发下有性能影响（写入在锁外执行）
- **中风险**: Fix #5 代理导出模式可能在边缘场景下出现导入顺序问题（已验证 TYPE_CHECKING 模式无循环依赖）
