受众：MASTER
标题：M42 代码审查报告 — 分层治理架构（Tiered Governance）

# M42 代码审查报告 — 分层治理架构（Tiered Governance）

**里程碑**：M42
**日期**：2026-03-06
**审查人**：AI Coding Agent
**全量测试结果**：2427 passed, 10 skipped, 0 failed
**新增测试**：38 个（T-TG-TOKEN × 6 + T-TG-TIER × 10 + T-TG-CACHE × 7 + T-TG-ENGINE × 8 + T-TG-AUDIT × 7）

---

## 里程碑目标

实现 Governor 分层治理架构，利用 `SkillMeta.risk_level` 对 Skill 进行风险分层（L0~L4），为不同风险等级的 Skill 提供差异化审批通道，防止未来接入大量 Skills 时"强主权 + 全量细审 + 串行审批"导致体验崩溃。

**核心原则**：不削弱主权，只让主权更精准。高风险的地方更严，低风险的地方更快。

---

## 变更摘要

### TASK-0425 M42-A：RouteToken 新增分层令牌

**文件**：`host_brain/governor/route_token.py`

**变更内容**：
- `RouteToken` 枚举新增两个令牌：
  - `GOV_OK_CACHED = "GOV_OK_CACHED"`：Policy Cache 命中直接放行（risk_level=1，~0ms）
  - `GOV_OK_RULE = "GOV_OK_RULE"`：规则引擎审批通过（risk_level=2，~5ms，不调用 LLM）
- `compile_route_decision()` 新增 `tier: str = "full"` 参数，按通道选择令牌：
  - `tier="cache"` → `GOV_OK_CACHED`
  - `tier="rule"` → `GOV_OK_RULE`
  - `tier="full"`（默认）→ `GOV_OK_RENDER`（向后兼容）
  - `tier="airlock"` → `GOV_AIRLOCK_REVIEW`

**向后兼容**：未指定 `tier` 时默认 `"full"`，返回 `GOV_OK_RENDER`，现有调用方无需修改。

---

### TASK-0426 M42-B：GovernorTierPolicy — Skill 风险分层策略

**文件**：`host_brain/governor/tier_policy.py`（新增）

**变更内容**：
- 新增 `ApprovalTier(Enum)`：`CACHE / RULE / FULL / AIRLOCK`
- 新增 `AuditTier(Enum)`：`LIGHT / STANDARD / FULL_AUDIT`
- 新增 `GovernorTierPolicy` 类，实现分层映射规则：

| risk_level | reversible | ApprovalTier | AuditTier |
|-----------|-----------|-------------|----------|
| 1 | any | CACHE | LIGHT |
| 2 | any | RULE | STANDARD |
| 3 | True | FULL | STANDARD |
| 3 | **False** | **AIRLOCK** | **FULL_AUDIT** |
| 4 | True | FULL | STANDARD |
| 4 | **False** | **AIRLOCK** | **FULL_AUDIT** |
| 5 | any | AIRLOCK | FULL_AUDIT |

**关键设计**：不可逆（`reversible=False`）且 `risk_level>=3` 的 Skill 自动升级到 AIRLOCK 通道，防止不可逆操作绕过人工确认。

---

### TASK-0427 M42-C：GovernorPolicyCache — L0/L1 审批结果缓存

**文件**：`host_brain/governor/policy_cache.py`（新增）

**变更内容**：
- 新增 `GovernorPolicyCache` 类，为 CACHE 通道提供 TTL 审批结果缓存
- 缓存键：`(skill_name, trust_level)`，缓存值：`(approved: bool, cached_at_mono_ms: float)`
- 默认 TTL：60,000ms（1 分钟）
- 接口：`get() / put() / invalidate() / clear() / size()`
- TTL 过期后自动失效，返回 `None`（触发重新审批）
- 拒绝结果（`approved=False`）也可缓存，防止重复拒绝调用规则引擎

**性能收益**：L0 Skill 缓存命中时审批延迟从 ~300ms 降至 ~0ms。

---

### TASK-0428 M42-D：IntentDecisionEngine 集成分层路由

**文件**：`host_brain/governor/decision.py`（修改）；`host_brain/governor/schemas.py`（修改）；`host_brain/skills/registry.py`（修改）

**变更内容**：

**decision.py**：
- `IntentDecisionEngine.__init__` 新增可选参数：
  - `registry: SkillRegistry | None = None`
  - `policy_cache: GovernorPolicyCache | None = None`
- `evaluate_intent()` 在 R1-R7 门控通过后，若注入了 `registry`，则：
  1. 查询 `SkillMeta`，调用 `GovernorTierPolicy.get_approval_tier()` 获取通道
  2. `CACHE` 通道：查询 `policy_cache`，命中则直接返回 `GOV_OK_CACHED`；未命中则写入缓存，走 `RULE` 通道
  3. `RULE` 通道：R1-R7 已通过即可，返回 `GOV_OK_RULE`
  4. `FULL` 通道：保持现有逻辑，返回 `GOV_OK_RENDER`
  5. `AIRLOCK` 通道：强制返回 `GOV_AIRLOCK_REVIEW`
- `committed_route_token` 新增 `approval_tier` 字段
- 未注入 `registry` 时保持现有行为（`GOV_OK_RENDER`），完全向后兼容

**schemas.py**：
- `strict_validate_intent()` 新增 `extra_action_allowlist: set[str] | None = None` 参数
- 支持动态 Skill action 白名单（来自 SkillRegistry），解决硬编码 `ACTION_ALLOWLIST` 无法支持大量 Skills 的根本问题
- 动态 Skill 的 args 校验宽松（允许任意 dict），不要求特定字段

**registry.py**：
- `SkillRegistry` 新增 `list_skill_names() -> list[str]` 方法，供 `decision.py` 构建动态白名单

---

### TASK-0429 M42-E：SkillLoader 集成分层审计

**文件**：`host_brain/skills/loader.py`（修改）

**变更内容**：
- `_log_and_return()` 新增 `governor_output: dict | None = None` 参数
- 新增模块级辅助函数 `_extract_audit_tier(governor_output) -> AuditTier`，从 `committed_route_token.approval_tier` 读取审计级别
- 分级写入 `skill_exec_log`：
  - `LIGHT`（cache 通道）：只写基础字段（`intent_id / skill_name / result_status / result_code / ts_ms / approval_token_ref`），不写 `result_digest / evidence_ref / manifest_ref / undo_token_ref`
  - `STANDARD`（rule/full 通道）：现有完整字段
  - `FULL_AUDIT`（airlock 通道）：完整字段 + `audit_tier = "full_audit"` 标记
- 未提供 `approval_tier` 时降级为 `STANDARD`，完全向后兼容

---

## 架构影响分析

### 新增文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `host_brain/governor/tier_policy.py` | ~100 | ApprovalTier / AuditTier / GovernorTierPolicy |
| `host_brain/governor/policy_cache.py` | ~80 | GovernorPolicyCache TTL 缓存 |
| `tests/unit/test_tiered_governance_m42.py` | ~380 | 38 个 M42 测试 |

### 修改文件

| 文件 | 变更类型 | 向后兼容 |
|------|---------|---------|
| `host_brain/governor/route_token.py` | 新增枚举值 + 新增参数 | ✅ 完全兼容 |
| `host_brain/governor/schemas.py` | 新增可选参数 | ✅ 完全兼容 |
| `host_brain/governor/decision.py` | 新增可选注入参数 | ✅ 完全兼容 |
| `host_brain/skills/loader.py` | 新增可选参数 + 分级审计 | ✅ 完全兼容 |
| `host_brain/skills/registry.py` | 新增方法 | ✅ 完全兼容 |

### 性能收益（理论值）

| Skill 风险等级 | 审批通道 | 审批延迟（缓存命中） | 审批延迟（缓存未命中） |
|-------------|---------|-----------------|------------------|
| risk_level=1 | CACHE | ~0ms | ~5ms（规则引擎） |
| risk_level=2 | RULE | N/A | ~5ms（规则引擎） |
| risk_level=3/4 | FULL | N/A | ~300ms（现有逻辑） |
| risk_level=5 | AIRLOCK | N/A | ~300ms + 人工确认 |

---

## 测试覆盖

| 测试组 | 测试数 | 覆盖内容 |
|--------|--------|---------|
| T-TG-TOKEN | 6 | RouteToken 新令牌 + compile_route_decision tier 参数 |
| T-TG-TIER | 10 | GovernorTierPolicy 分层映射（含不可逆升级规则） |
| T-TG-CACHE | 7 | GovernorPolicyCache TTL / invalidate / clear / 独立性 |
| T-TG-ENGINE | 8 | IntentDecisionEngine 分层路由（缓存命中/未命中/各风险等级/向后兼容/门控优先） |
| T-TG-AUDIT | 7 | SkillLoader 分层审计（LIGHT/STANDARD/FULL_AUDIT/向后兼容） |
| **合计** | **38** | |

**全量测试**：2427 passed, 10 skipped, 0 failed（无回归）

---

## 风险评估

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| 现有调用方未传 `registry` | 低 | 所有新参数均为可选，默认行为与 M42 前完全一致 |
| `ACTION_ALLOWLIST` 扩展后安全性 | 低 | 动态白名单来自 `SkillRegistry`，注册时已通过 `SkillMeta.validate()` 校验 |
| Policy Cache 缓存污染 | 低 | TTL 默认 60s，`invalidate()` 可主动清除；拒绝结果也可缓存防止重复调用 |
| 不可逆 Skill 升级到 AIRLOCK 后用户体验 | 中 | 这是设计意图，不可逆操作本应要求人工确认 |

---

## 结论

M42 分层治理架构完整落地，所有 6 个任务（TASK-0425~0430）全部 DONE，38 个新增测试全部通过，全量测试无回归。

架构演进路径清晰：
- **现在**：注入 `registry` 即可启用分层治理，不注入则保持现有行为
- **未来**：接入大量 Skills 时，只需在 `SkillMeta` 中正确设置 `risk_level`，分层治理自动生效，无需修改 Governor 核心逻辑
