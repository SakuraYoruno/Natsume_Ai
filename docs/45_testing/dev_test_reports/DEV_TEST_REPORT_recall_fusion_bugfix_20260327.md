受众：MASTER|CODEX|PROJECT
标题：灵魂层召回逻辑 4 项 Bug 修复报告

# DEV TEST REPORT — 灵魂层召回逻辑 Bug 修复

**日期**：2026-03-27  
**作者**：Codex（开发节点）  
**状态**：OPEN（待运行节点真机验证）

---

## 1. 变更摘要

通过静态代码审查发现并修复灵魂层召回逻辑（`recall_fusion` 模块）中的 4 项问题。

### 修改文件清单

| 文件 | 变更内容 |
|------|---------|
| `host_brain/retrieval/recall_fusion_config.py` | Fix 1：`_render_target_lang_selector_match` 空值 bug |
| `host_brain/retrieval/recall_fusion.py` | Fix 2：fusion key 碰撞；Fix 3：emotion_filter 分组排序 |
| `host_brain/retrieval/recall_fusion_stores.py` | Fix 4：memory_ops_log 搜索文本质量 |

---

## 2. 修复详情

### Fix 1 🔴 `_render_target_lang_selector_match` 空值返回 False（高优先级）

**文件**：`recall_fusion_config.py` L279-289

**问题**：当调用方未传 `render_target_lang`（`None` 或空字符串）时，函数直接返回 `False`，导致所有带语言限定 selector（`render_target_lang_selector="zh"` 等）的路由被全部裁掉，最终只保留 `default_route` 一条路由退化运行。

**影响**：日常对话几乎不传 `render_target_lang`，导致 `style_canon`、`persona_utterance`、`world_canon` 等灵魂层路由实际上永远不会激活。

**修复**：`render_target_lang` 为空时返回 `True`（不过滤），保持多路由正常激活。

```python
# 修复前
if not lang:
    return False

# 修复后
if not lang:
    return True  # 未指定语言时不过滤，保持路由激活
```

---

### Fix 2 🟡 fusion key 跨路由碰撞（中优先级）

**文件**：`recall_fusion.py` L335

**问题**：fusion 去重 key 仅用 `source_ref` 或 `doc_id`，不同路由（如 `episodic_vector` 和 `sqlite_ledger`）可能产生相同格式的 source_ref，导致跨路由候选被错误合并，丢失较低分数路由的语义信息。

**修复**：改为 `{store_id}::{source_ref}` 格式，确保不同 store 间的候选互不干扰。

```python
# 修复前
key = str(candidate["source_ref"] or candidate["doc_id"])

# 修复后
store_id_for_key = str(candidate["metadata"].get("store_id") or route.store_id or "")
doc_id_for_key = str(candidate["source_ref"] or candidate["doc_id"])
key = f"{store_id_for_key}::{doc_id_for_key}" if store_id_for_key else doc_id_for_key
```

---

### Fix 3 🟡 `emotion_filter` 分组内排序丢失（中优先级）

**文件**：`recall_fusion.py` L362-365

**问题**：
1. `matched + unmatched` 拼接后，两个分组内部的原始分数排序丢失
2. 仅支持严格等于（`== emotion_filter`），不支持前缀匹配（如 `emotion_filter="HAPPY"` 匹配 `"HAPPY_HIGH"`）
3. 大多数候选无 `emotion_tag` 字段，过滤形同虚设

**修复**：分组前先对各组内按 `_candidate_sort_key` 排序；增加前缀匹配逻辑。

```python
# 修复后
def _has_emotion_tag(c: dict) -> bool:
    tag = str(c.get("metadata", {}).get("emotion_tag") or "").strip().lower()
    return bool(tag and (tag == ef or tag.startswith(ef)))

matched = sorted([c for c in candidates if _has_emotion_tag(c)], key=_candidate_sort_key)
unmatched = sorted([c for c in candidates if not _has_emotion_tag(c)], key=_candidate_sort_key)
candidates = matched + unmatched
```

---

### Fix 4 🟡 `memory_ops_log` 搜索文本质量差（中优先级）

**文件**：`recall_fusion_stores.py` L247-249

**问题**：搜索文本仅拼接 `detail`（操作日志，如 `"WRITE operation success"`）、`memory_id`（UUID）、`evidence_refs`（URI），三者均无实质语义，导致 `_text_similarity_score` 几乎全部趋近 0，该路由实际上命中率极低。

**修复**：额外加入语义价值更高的字段：
- `intent_id`：意图标识，直接对应用户意图
- `target_memory_id`：目标记忆 ID，携带写入对象信息
- `source_memory_ids`：来源记忆集合
- `decay_profile_id`：衰减策略标识
- `visibility`：可见性标签

同时在 metadata 中保存这些新字段，供下游使用。

---

## 3. 静态验证

| 文件 | Lint 报错数 | 状态 |
|------|------------|------|
| `recall_fusion_config.py` | 0 | ✅ PASS |
| `recall_fusion.py` | 0 | ✅ PASS |
| `recall_fusion_stores.py` | 0 | ✅ PASS |

注：开发节点无 Python 运行时，无法执行 pytest。运行节点需执行：

```bash
python -m pytest tests/unit/test_recall_fusion_emotion_m27.py \
                 tests/unit/test_recall_fusion_six_store_routes.py \
                 tests/unit/test_recall_config_store_id_required.py \
                 tests/unit/test_recall_fusion_sqlite_ranking_boost_m45e.py \
                 -v
```

---

## 4. 待真机验证测试用例（T-RF-FIX-xx）

| 用例 ID | 测试内容 | 预期结果 |
|---------|---------|---------|
| T-RF-FIX-01 | 不传 render_target_lang 时，style_canon 路由是否激活 | route_rows 中包含 store_id=style_canon |
| T-RF-FIX-02 | 不传 render_target_lang 时，persona_utterance 路由是否激活 | route_rows 中包含 store_id=persona_utterance |
| T-RF-FIX-03 | 同 doc_id 不同 store_id 的候选，fusion 后各自保留 | candidates 中两个路由的结果均存在 |
| T-RF-FIX-04 | emotion_filter="HAPPY" 时，matched 组内按分数降序 | matched 列表分数单调不增 |
| T-RF-FIX-05 | 现有单元测试全部通过 | pytest 全绿 |

---

## 5. 问题 5（观察，未修复）：混合检索索引覆盖范围

混合检索（`_apply_hybrid_fusion`）在全路由 fusion 后执行，FTS5 索引若只含 episodic 记忆，会将无关 episodic 文档重新压进来，干扰 canon 路由的结果。

**建议**：待运行节点确认 FTS5 索引的构建范围后，决定是否按 store_id 分组进行 FTS 融合或完全禁用 canon 路由的混合检索。暂不修复，留观察。
