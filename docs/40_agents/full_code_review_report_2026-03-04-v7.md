受众：CODEX
标题：全面代码审阅报告（第七版，2026-03-04）

# 🔍 Nastume Ai 全面代码审阅报告

> **生成日期**：2026-03-04（第七版，M10-E 收口）
> **审阅范围**：M10-E（F-42 热词缓存 TTL）+ Edge 端优化成果整合（TTS/VTS/ASR 全链路）
> **审阅方法**：差异审阅 + 专项 TTL 测试 + 全量回归

---

## 一、M10-E 已完成改动（本版结论）

| 发现项 | 模块 | 修复状态 | 说明 |
|---|---|---|---|
| F-42 | `edge_client/senses/asr.py` | ✅ DONE | `_HotwordsCache` 类：线程安全 TTL 缓存（默认 300s），`inject_canon_hotwords()` 注入后更新缓存，`get_canon_hotwords()` 返回 TTL 内副本 |

### 1.1 核心改动详情

| 文件 | 改动 | 验证 |
|---|---|---|
| `edge_client/senses/asr.py` | 新增 `_HotwordsCache` 类（`set/get/clear/is_valid/age_s/_is_expired`）；`_CANON_HOTWORDS_CACHE = _HotwordsCache(ttl_s=300.0)` 模块级实例；`inject_canon_hotwords()` 注入后调用 `set()` 并记录 TTL 日志 | ✅ T-HW-TTL-01~05 全部通过 |
| `tests/unit/test_hotwords_cache_ttl.py` | 新增 5 个 TTL 专项测试（mock `time.monotonic` 模拟时间推进，无需真实等待） | ✅ 5 passed |

---

## 二、Edge 端优化成果整合（2026-03-04）

本版整合 Edge 端优化报告（TTS/VTS/ASR 全链路贯通）成果：

| 模块 | 优化内容 | 状态 |
|---|---|---|
| TTS 引擎 | 双后端自动切换（pyttsx3/edge-tts）、环境自适配、权重定位增强、参考音频选择策略修复 | ✅ DONE |
| VTS 驱动 | 独立事件循环线程、认证流程增强、接口兼容性修复 | ✅ DONE |
| ASR 感知层 | 热词注入（M10-B）+ TTL 自动失效（M10-E F-42） | ✅ DONE |
| 全链路 E2E | ASR/TTS/VTS 全链路贯通，口型联动和打断复位验证通过 | ✅ DONE |

---

## 三、测试结果

### 3.1 M10-E 专项测试

```text
tests/unit/test_hotwords_cache_ttl.py — 5 passed
  T-HW-TTL-01: TTL 未到期时 get() 返回完整热词列表
  T-HW-TTL-02: TTL 到期后 get() 返回空列表（mock time.monotonic）
  T-HW-TTL-03: is_valid() 在有效期内返回 True，到期后返回 False
  T-HW-TTL-04: clear() 立即使缓存失效
  T-HW-TTL-05: 并发写入安全（20 线程同时 set()，无竞态）
```

### 3.2 全量回归

```text
1090 passed, 2 skipped, 1 warning
```

> 2 skipped 为 `NASTUME_TEST_REDIS_URL` 未配置时的 Redis 集成测试（预期行为）。

---

## 四、剩余发现项（更新后）

### 4.1 P1（剩余 3 项，均为环境/设备依赖）

| # | 模块 | 发现 | 建议 |
|---|---|---|---|
| F-04 | `governor/rate_limit_store.py` | Redis 后端真实验证待执行（已补集成测试，`TEST_READY`） | 配置 `NASTUME_TEST_REDIS_URL` 后执行；单机部署可暂缓 |
| F-07 | Edge ASR | CPU 降级延迟（设备实测） | 真机性能验证 |
| F-08 | Edge TTS+ASR | GPU VRAM OOM（压力测试） | 真机压力与资源门闸验证 |

### 4.2 P2（剩余 10 项）

F-13、F-14、F-15、F-16、F-18、F-19、F-20、F-25、F-26、F-27。

---

## 五、发布清单

| 字段 | 值 |
|---|---|
| milestone | M10-E |
| test_passed_count | 1090 |
| asr_hash | sha256:fc1cf313c493b8b3783205f5d5f1a67186f8a81a5239df0f9dcadf1ac7f2b03c |
| hotwords_cache_ttl_test_hash | sha256:92a539ece27d30a6fb89a6196c14f5ab0a9aea8e5dab74f310ac4746f00495bd |
| migration_file_count | 18 |
| generated_at | 2026-03-04T12:43:26+00:00 |

---

## 六、本版结论

M10-E 收口完成。F-42（热词缓存 TTL）已落地，`_HotwordsCache` 类线程安全、TTL 自动失效行为经 5 个专项测试验证。

**代码级 P1 发现项已全部清空**（F-42 为最后一项代码级 P1）。剩余 P1 均为部署/设备验证类，不阻塞功能发布。

建议下一批次进入"设备验证包"：`F-07/F-08`（Edge 真机实测）；`F-04` 在多实例部署前执行 Redis 集成测试。
