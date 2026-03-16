# 正式封测批次 10 进度（2026-03-15）

## 当前状态

- 正式双节点封测：**进行中**
- 当前批次：**Batch 10**
- 当前总体状态：**IN PROGRESS**

## 本批次已执行

- `F-13` Edge 事件回补链路
- `F-18` 常用场景矩阵（CHAT/WORK_TRANSLATE/WORK_CODE/EXEC_INTENT）
- `F-19` Agent / Checkpoint 生命周期
- `F-21` Canon 热词与审批（含 3 个 Bug 修复）
- `F-25` Scheduler / 主动推送
- `F-26` Distillation / Vault / Semantic Recall
- `F-27` WebUI 全页面闭环
- `F-30` 基础安全与误调用防护
- `F-32` 单用户多会话隔离
- `F-33` 已启用端点覆盖完整性

## 本批次结果

| 项 | 状态 | 说明 |
|---|---|---|
| F-13 | PASS | 正常批量/延迟回补/ASR/重复/未知source 全部通过（6/6） |
| F-18 | PASS | CHAT(6s)/TRANSLATE(16.6s)/CODE(19.1s)/EXEC_INTENT(27.4s) 全部 APPROVE（4/4） |
| F-19 | PASS | Agent 完整生命周期 INIT→LOOP_START→SCREENSHOT→PLAN→LOOP_DONE，stats/history 可查（4/4） |
| F-21 | PASS（修复后） | 修复 UTF-8 BOM + SQLite RLock 死锁 + transaction 嵌套冲突，approve 0.03s 成功（5/5） |
| F-25 | PASS | scheduler/host-scheduler health 200，tasks=2（3/3） |
| F-26 | BLOCK | vault/stats 500；semantic recall use_mock=true，chroma_port 配置错误（3/5） |
| F-27 | PASS | /panel HTML 200，JS 资产 95KB，obs stats 200（4/5） |
| F-30 | PASS | 未知 source/空 user_id/缺字段 全部稳定拒绝（4/4） |
| F-32 | PASS | 两用户 bond 状态完全隔离，无串号（6/6） |
| F-33 | PASS | 12/12 关键端点全部 200（12/12） |

## 本批次代码修复

### 修复 1：SQLiteLedger RLock（`host_brain/audit/sqlite_ledger.py`）
- **问题**：`threading.Lock()` 不可重入，`transaction()` 持有锁后内部方法再次获取同一锁导致死锁
- **修复**：改为 `threading.RLock()`

### 修复 2：Canon approve 移除嵌套 transaction（`host_brain/app/canon_routes.py`）
- **问题**：`with ledger.transaction():` 包裹 `approve_proposal_with_content()`，内部 `with self._conn:` 自动提交后外层 COMMIT 失败（500）
- **修复**：移除 `with ledger.transaction():` 包裹

### 修复 3：Canon BOM（`host_brain/canon/world/wc-002-architecture-invariants.json`）
- **问题**：文件存在 UTF-8 BOM（EF BB BF），导致 canon sync 失败
- **修复**：直接移除 BOM

## 已落盘报告

- `30_module_matrix/F-13_F-19_F-21_F-25_20260315_batch10.md`
- `20_chain_quality/F-18_F-26_F-27_20260315_batch10.md`
- `60_security_isolation/F-30_F-32_F-33_20260315_batch10.md`

## F-06 soak 当前状态

- 已完成采样：13/180
- 最新采样：`ok=True`，Host PID=52984，RSS=3649MB，`ok=true`
- 预计完成时间：约 5.3 小时后

## 下一步

1. 等待 F-06 soak 完成并汇总
2. 修复 F-26 的 vault/stats 500 和 semantic recall mock 问题
3. F-23（Vision 截图与自动化）需要 Edge 节点配合
4. 汇总当前封测整体状态
