受众：CODEX
标题：开发实测报告 — 微信 4.x UIA 树恢复验证脚本修正（v2）

# 开发实测报告：微信 4.x UIA 树恢复验证脚本修正（v2）

| 字段 | 值 |
|------|-----|
| **功能名称** | 微信 4.x UIA 树恢复验证脚本修正（窗口发现 + 编码 + 成功判定） |
| **开发节点** | v_wenloliu workstation |
| **报告日期** | 2026-03-26 |
| **报告版本** | v2（基于 RUNTIME_FEEDBACK 反馈的修正版） |
| **关联 Commit** | 待提交 |
| **关联分支** | codex |
| **状态** | 🔵 OPEN（待真机验证） |

---

## 1. 变更摘要

本次修正基于 RUN 节点运行反馈报告（RUNTIME_FEEDBACK_wechat_uia_tree_recovery_20260326.md）中发现的 4 个问题：

1. **P0 窗口类名硬编码**：原脚本只搜 `WeChatMainWndForPC`，但真机窗口类名为 `Qt51514QWindowIcon` → 改为多类名遍历 + 标题兜底
2. **P0 文档与代码不一致**：原文档声称"搜索多种已知类名"但代码只搜一种 → 代码已修正为真正的多类名搜索，与文档描述一致
3. **P1 控制台编码错误**：emoji 输出在 RUN 节点默认编码下触发 `UnicodeEncodeError` → 全部去掉 emoji 改用纯文本标记 `[OK]/[FAIL]/[WARN]` + 强制 UTF-8 stdout
4. **P1 成功判定过于简单**：`child_count > 2` 存在误判（登录态也有多子元素） → 升级为双条件判定（必须找到 `mmui::ChatSingleWindow` 或 `mmui::ChatTextItemView`）

---

## 2. 修改文件清单

| # | 文件路径 | 改动类型 | 改动说明 |
|---|---------|---------|---------|
| 1 | `scripts/runtime/verify_uia_recovery.py` | 修改 | 窗口发现多类名 + 标题兜底 + UTF-8 编码 + 去 emoji + 双条件判定 + pywinauto 多类名连接 |
| 2 | `docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wechat_uia_verify_fix_v2_20260326.md` | 新增 | 本报告 |

---

## 3. 本机已验证项

| # | 验证项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | Lint (IDE linter) | ✅ PASS | 0 errors |
| 2 | Python 语法检查 (py_compile) | ✅ PASS | 脚本可编译 |
| 3 | 逻辑走查 | ✅ PASS | 见下方详细说明 |
| 4 | 编码兼容性 | ✅ PASS | 全文无 emoji，增加了 UTF-8 stdout wrapper |

---

## 4. 真机实测清单

> 以下测试项需要在**运行节点**执行。重点验证 4 个修正项是否解决了上轮反馈的问题。

### T-01: 窗口发现 — 多类名兼容 — P0

**前置条件**：
- 微信已启动并处于主界面（至少一个聊天窗口打开）

**测试步骤**：
1. `cd D:\Natsume_Ai`
2. `git pull origin codex`
3. `python scripts/runtime/verify_uia_recovery.py`（可用交互式计划任务包装）

**预期结果**：
- 输出 `[OK] 找到微信窗口: class_name='Qt51514QWindowIcon'` 或其他匹配类名
- **不再**出现 `[FAIL] 未找到微信窗口` 的错误
- 脚本继续执行到方案 A/B/C 阶段

**验证命令/方法**：
```bash
python scripts/runtime/verify_uia_recovery.py 2>&1 | findstr "[OK].*微信窗口"
```

---

### T-02: 编码兼容性 — 无 UnicodeEncodeError — P0

**前置条件**：
- 使用默认 PowerShell 或计划任务环境（**不**设置 `PYTHONUTF8=1` 和 `chcp 65001`）

**测试步骤**：
1. 在默认编码环境下执行 `python scripts/runtime/verify_uia_recovery.py`

**预期结果**：
- 脚本完整运行到汇总阶段，**不**出现 `UnicodeEncodeError`
- 所有输出使用 `[OK]/[FAIL]/[WARN]/[INFO]` 等纯 ASCII 标记，不含 emoji

---

### T-03: P0 方案 A/B/C 实际执行 — P0

**前置条件**：
- T-01 通过（窗口已找到）
- `pip install comtypes pywinauto uiautomation` 已完成

**测试步骤**：
1. 执行 `python scripts/runtime/verify_uia_recovery.py`
2. 观察是否出现 `[A] comtypes`、`[B] pywinauto`、`[C] uiautomation` 三个阶段的输出
3. 检查每个方案的 `直接子元素数量` 输出

**预期结果**：
- 三个方案至少能执行到输出子元素数量（不再被窗口查找阻塞）
- 如果子元素数 > 2，会进一步搜索 `mmui::ChatSingleWindow` 等目标控件
- 如果子元素数 <= 2，输出 `[FAIL] UI 树未恢复`

---

### T-04: 双条件成功判定 — P0

**前置条件**：
- 方案至少一种报告 `child_count > 2`

**测试步骤**：
1. 检查汇总输出中每个方案的状态
2. 确认状态为以下三种之一：
   - `success`：child_count > 2 **且** 找到 ChatSingleWindow 或 ChatTextItemView
   - `partial`：child_count > 2 **但** 未找到关键聊天控件
   - `failed`：child_count <= 2

**预期结果**：
- 如果微信在聊天主界面：至少一种方案报告 `success`
- 如果微信在登录态/设置页：报告 `partial`（不会误报为 success）
- 汇总输出 `[OK]` / `[PARTIAL]` / `[FAIL]` 标记清晰正确

---

### T-05: pywinauto 多类名连接 — P1

**前置条件**：
- pywinauto 已安装

**测试步骤**：
1. 观察方案 B 输出中的连接信息

**预期结果**：
- 输出 `[OK] pywinauto 通过 class_name='Qt51514QWindowIcon' 连接成功` 或通过标题兜底连接
- **不再**因为 `WeChatMainWndForPC` 找不到而报错

---

### T-06: JSON 报告生成 — P1

**前置条件**：
- 脚本完整运行到汇总阶段

**测试步骤**：
1. 检查 `uia_recovery_result.json` 是否生成
2. 检查 JSON 中是否包含 `wechat_class_name` 和 `any_partial` 字段

**预期结果**：
- JSON 文件生成成功
- 包含新增字段 `wechat_class_name`、`any_partial`
- 每个方案的 `status` 字段值为 `success`/`partial`/`failed`/`error`/`not_installed` 之一

**验证命令**：
```bash
python -c "import json; d=json.load(open('uia_recovery_result.json')); print(d.get('wechat_class_name'), d.get('any_partial'))"
```

---

### T-07: 引用保活（成功或 partial 时）— P2

**前置条件**：
- 至少一种方案报告 `success` 或 `partial`

**测试步骤**：
1. 确认脚本输出 `[INFO] 保持脚本运行中`
2. 在另一个终端用 Inspect.exe 或 Accessibility Insights 检查微信控件树

**预期结果**：
- 脚本保持运行不退出
- partial 状态下也保活（便于调试）

---

## 5. 实测统计

| 优先级 | 总数 | 说明 |
|--------|------|------|
| P0（阻塞） | 4 | 必须全部通过（窗口发现、编码、方案执行、成功判定） |
| P1（重要） | 2 | 应通过 ≥ 80%（pywinauto 多类名、JSON 报告） |
| P2（增强） | 1 | 引用保活 |

---

## 6. 已知限制与风险

| # | 描述 | 影响范围 | 缓解措施 |
|---|------|---------|---------|
| 1 | DEV 节点本机无微信运行，无法端到端验证 | 窗口发现和 UI 树恢复逻辑 | 依赖 RUN 节点真机验证 |
| 2 | `Qt51514QWindowIcon` 可能随微信版本更新变化 | 未来版本兼容性 | 保留标题兜底搜索 + 在 JSON 报告中记录实际类名 |
| 3 | 当微信不在聊天主界面时，partial 状态无法区分"UIA 恢复成功但不在聊天页" vs "UIA 恢复不完全" | 诊断精确性 | 在输出中提示用户确认微信界面状态 |

---

## 7. 回滚方案

如果修正版导致新问题：

```bash
git revert <commit_hash>
```

**受影响文件**：
1. `scripts/runtime/verify_uia_recovery.py` — 核心修改
2. `docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wechat_uia_verify_fix_v2_20260326.md` — 本报告

---

## 8. 修正点与代码位置对照

| 修正项 | 代码位置 | 修正方式 |
|--------|---------|---------|
| 多类名窗口发现 | L47-49: `WECHAT_CLASS_NAMES` 列表；L76-100: `find_wechat_hwnd()` | 遍历 `[WeChatMainWndForPC, Qt51514QWindowIcon]` + `FindWindowW(None, "微信")` 兜底 |
| UTF-8 编码兼容 | L37-41: stdout/stderr 重包装 | `io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")` |
| 去 emoji | 全文 | `[OK]/[FAIL]/[WARN]/[INFO]/[FOUND]/[PARTIAL]/[SKIP]/[ERROR]/[END]` 替代 emoji |
| 双条件判定 | L142-157: `evaluate_success()` | 核心条件=找到 `ChatSingleWindow`/`ChatTextItemView`，辅助条件=`child_count > 2` |
| pywinauto 多类名 | L265-289: `test_pywinauto()` 中的窗口连接 | 遍历类名列表 + 标题兜底 |
