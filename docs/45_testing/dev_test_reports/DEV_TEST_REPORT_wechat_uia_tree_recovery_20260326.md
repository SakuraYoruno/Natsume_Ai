受众：CODEX
标题：开发实测报告 — 微信 4.x UIA 树恢复验证脚本

# 开发实测报告：微信 4.x UIA 树恢复验证

| 字段 | 值 |
|------|-----|
| **功能名称** | 微信 4.x UIA 树恢复验证脚本（P0 方案验证） |
| **开发节点** | DEV (v_wenloliu workstation) |
| **报告日期** | 2026-03-26 |
| **报告版本** | v1 |
| **关联 Commit** | cb9affb |
| **关联分支** | codex |
| **状态** | 🔵 OPEN（待真机验证） |

---

## 1. 变更摘要

基于三路并行研究（UIA 自动化方案 / 非 UIA 替代方案 / Qt5 Accessibility 强制启用方法）的成果，创建了 P0 方案验证脚本。

**背景**：运行节点实测报告（2026-03-26）确认 DEV/RUN 两个节点的微信 4.x 均只暴露 2 个 UIA 顶层元素，`mmui::ChatSingleWindow` 和 `mmui::ChatTextItemView` 完全缺失。根因为微信 4.1.5+ 的"按需暴露"策略——只有持久化的无障碍客户端才能触发完整 UI 树加载。

**本次变更**：提供一个独立的验证脚本，包含 3 种 Python 实现（comtypes / pywinauto / uiautomation），在 RUN 节点执行即可判定 P0 方案是否可行。

---

## 2. 修改文件清单

| # | 文件路径 | 改动类型 | 改动说明 |
|---|---------|---------|---------|
| 1 | `scripts/runtime/verify_uia_recovery.py` | 新增 | P0 方案验证脚本，含 3 种 UIA 客户端冒充实现 |
| 2 | `docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wechat_uia_tree_recovery_20260326.md` | 新增 | 本开发实测报告 |

---

## 3. 本机已验证项

以下项目已在开发节点通过验证：

| # | 验证项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | Python 语法检查 | ✅ PASS | `python -m py_compile verify_uia_recovery.py` 通过 |
| 2 | 导入检查 | ⏭️ SKIP | comtypes/pywinauto/uiautomation 为 Windows 专用库，需真机环境 |
| 3 | 脚本结构审查 | ✅ PASS | 常量集中定义、异常处理完善、JSON 输出、引用保活机制 |
| 4 | 文档门禁 | ✅ PASS | 首两行 `受众：CODEX` + `标题：...` 符合规范 |

---

## 4. 真机实测清单

> 以下测试项需要在**运行节点**执行。验证 P0 方案（UIA 客户端冒充）是否可恢复微信 4.x 的完整控件树。

### T-01: 依赖安装 — P0

**前置条件**：
- Python 3.8+ 已安装
- 网络可用（pip 安装）

**测试步骤**：
1. 在运行节点打开 PowerShell
2. 执行 `pip install comtypes pywinauto uiautomation`
3. 确认三个包均安装成功

**预期结果**：
- 三个包全部安装成功，无报错
- `python -c "import comtypes; import pywinauto; import uiautomation; print('OK')"` 输出 OK

**验证命令**：
```bash
pip install comtypes pywinauto uiautomation
python -c "import comtypes; import pywinauto; import uiautomation; print('OK')"
```

---

### T-02: P0 方案验证 — comtypes 能否恢复 UI 树 — P0

**前置条件**：
- T-01 通过
- 微信 PC 端已启动（4.x 版本）
- 微信处于前台，至少打开了一个聊天窗口

**测试步骤**：
1. `cd D:\Natsume_Ai_repo\scripts\runtime`（或仓库对应路径）
2. 执行 `python verify_uia_recovery.py`
3. 观察「方案 A: comtypes」的输出

**预期结果**：
- "找到微信窗口: hwnd=..." 成功
- comtypes 方案的"直接子元素数量" **> 2**
- 输出 "✅ UI 树已恢复！"
- 深度搜索找到至少 `mmui::ChatSingleWindow`

**关键断言**：
- `child_count > 2`（区别于基线的 2 个元素）

---

### T-03: P0 方案验证 — pywinauto 能否恢复 UI 树 — P0

**前置条件**：
- 同 T-02

**测试步骤**：
1. 执行 `python verify_uia_recovery.py`（同一脚本，方案 B 自动执行）
2. 观察「方案 B: pywinauto」的输出

**预期结果**：
- pywinauto 方案的子元素数量 **> 2**
- 搜索到 `mmui::ChatSingleWindow`

**关键断言**：
- `child_count > 2`

---

### T-04: P0 方案验证 — uiautomation 库能否恢复 UI 树 — P0

**前置条件**：
- 同 T-02

**测试步骤**：
1. 执行 `python verify_uia_recovery.py`（方案 C 自动执行）
2. 观察「方案 C: uiautomation」的输出

**预期结果**：
- uiautomation 方案的子元素数量 **> 2**

**关键断言**：
- `child_count > 2`

---

### T-05: 汇总结论确认 — P0

**前置条件**：
- T-02 ~ T-04 已执行

**测试步骤**：
1. 查看脚本末尾的"验证结果汇总"
2. 确认 `any_success` 的值
3. 查看生成的 `uia_recovery_result.json` 文件

**预期结果**：
- **至少一种方案**显示 "✅ 成功"
- JSON 报告中 `any_success: true`

**验证命令**：
```bash
# 查看 JSON 报告
type uia_recovery_result.json
# 或
cat uia_recovery_result.json
```

---

### T-06: 引用保活验证 — P1

**前置条件**：
- T-05 中至少一种方案成功

**测试步骤**：
1. 验证脚本成功后会提示"保持脚本运行中"
2. **不要关闭脚本**，打开另一个 PowerShell
3. 在新窗口中运行 Inspect.exe 或 Accessibility Insights
4. 查看微信窗口的控件树是否仍然完整
5. 等待 5 分钟后再次检查子元素数

**预期结果**：
- 脚本运行期间，微信的 UIA 控件树保持完整
- 5 分钟后控件树仍然存在（心跳检查应每 60 秒报告一次）

**关键断言**：
- 5 分钟后 `child_count` 仍然 > 2（证明引用保活有效）

---

### T-07: 脚本退出后 UI 树是否回收 — P2

**前置条件**：
- T-06 通过

**测试步骤**：
1. 按 Ctrl+C 停止验证脚本
2. 等待 30 秒
3. 重新运行 `python verify_uia_recovery.py`
4. 查看基线检查的子元素数

**预期结果（两种可能，需记录实际情况）**：
- **情况 A**：基线子元素数回到 2 → 确认必须保持引用存活
- **情况 B**：基线子元素数仍然 > 2 → 说明只需触发一次即可持久

**无论哪种情况都是有效信息**，用于指导 wechat_uia_bridge.py 的设计。

---

## 5. 实测统计

| 优先级 | 总数 | 说明 |
|--------|------|------|
| P0（阻塞） | 5 | T-01 ~ T-05，必须全部执行，至少 T-01 + 任一方案(T-02/03/04) + T-05 通过 |
| P1（重要） | 1 | T-06，验证引用保活机制 |
| P2（增强） | 1 | T-07，验证退出后行为 |

---

## 6. 已知限制与风险

| # | 描述 | 影响范围 | 缓解措施 |
|---|------|---------|---------|
| 1 | comtypes 的 COM 类型生成需要首次运行时生成缓存 | 首次运行可能较慢 | 脚本已有异常处理，失败会提示 |
| 2 | 微信多开时 FindWindowW 只找到第一个窗口 | 多微信实例 | 后续可用 EnumWindows 枚举 |
| 3 | 不同微信版本的 ClassName 可能不同 | 跨版本兼容 | 脚本搜索多种已知类名 |
| 4 | DEV 节点本机无法运行完整测试（微信版本/环境限制） | 本机验证 | 仅做语法检查，完整测试在 RUN 节点 |

---

## 7. 回滚方案

如果验证脚本有问题：

```bash
# 仅新增文件，直接删除即可
git rm scripts/runtime/verify_uia_recovery.py
git rm docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wechat_uia_tree_recovery_20260326.md
git commit -m "revert: remove UIA recovery verification script"
```

**受影响文件回滚优先级**：
1. `scripts/runtime/verify_uia_recovery.py`（验证脚本）
2. 本报告文件（文档）

---

## 8. 数据流图

```
  [RUN 节点]
       │
       │  1. pip install comtypes pywinauto uiautomation
       │  2. python verify_uia_recovery.py
       │
       ▼
  ┌──────────────────────┐
  │ verify_uia_recovery  │
  │                      │
  │  FindWindowW() ──────┼──► 获取微信 hwnd
  │       │              │
  │       ▼              │
  │  comtypes COM ───────┼──► ElementFromHandle(hwnd) → 触发 UIA Provider
  │  pywinauto UIA ──────┼──► Desktop(backend="uia") → 触发 UIA Provider
  │  uiautomation ───────┼──► ControlFromHandle(hwnd) → 触发 UIA Provider
  │       │              │
  │       ▼              │
  │  遍历 Children/      │
  │  Descendants         │
  │       │              │
  │       ▼              │
  │  输出 JSON 报告 ─────┼──► uia_recovery_result.json
  │  保持引用存活        │
  └──────────────────────┘
       │
       ▼
  [运行节点反馈报告] → push → [DEV 节点分析]
```

---

<!-- 运行节点请在下方填写结果，或在 runtime_feedback_reports/ 中新建反馈报告 -->
