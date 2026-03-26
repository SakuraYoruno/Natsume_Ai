受众：CODEX
标题：运行反馈报告 — 微信 4.x UIA 树恢复验证脚本

# 运行反馈报告：微信 4.x UIA 树恢复验证脚本

| 字段 | 值 |
|------|-----|
| **功能名称** | 微信 4.x UIA 树恢复验证脚本（P0 方案验证） |
| **对应开发报告** | `DEV_TEST_REPORT_wechat_uia_tree_recovery_20260326.md` |
| **运行节点** | `edge-lan`（补充 DEV 节点对照证据） |
| **报告日期** | 2026-03-26 |
| **测试人** | Codex |
| **总体结论** | 🔴 关键失败 |

---

## 1. 测试环境

| 项目 | 值 |
|------|-----|
| **操作系统** | RUN: Microsoft Windows 11 专业版；DEV: Microsoft Windows 11 Pro for Workstations |
| **Python** | RUN: 3.11.6；DEV: 3.11.0 |
| **GPU** | 两端均为 `GameViewer Virtual Display Adapter` |
| **微信版本** | RUN: 4.1.1.19；DEV: 4.1.7.59 |
| **VTS 版本** | N/A（本次仅验证微信 UIA 恢复脚本） |
| **Git Commit** | `540f396` |
| **Node.js** | N/A |
| **其他关键依赖** | `comtypes`、`pywinauto`、`uiautomation`、交互式计划任务（RUN） |

### 环境差异说明

- 开发报告假定运行节点可直接执行 `python verify_uia_recovery.py`；实际在远端真机联调中，为了进入 `console` 交互桌面，需要用交互式计划任务包装执行。
- RUN 节点首次执行时还遇到控制台编码问题：脚本中的 emoji 输出在默认编码下触发 `UnicodeEncodeError`。测试中通过 `PYTHONUTF8=1` + `chcp 65001` 规避后，才能得到完整失败日志。
- DEV 节点这次微信处于登录/挤下线提示态，不是聊天主界面；因此 DEV 侧证据只作为辅助对照，不作为 P0 成功判定依据。

---

## 2. 测试执行记录

> 主验证在 RUN 节点完成；DEV 节点做了同脚本对照执行与补充探针，帮助定位问题根因。

### T-01: 依赖安装 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | RUN 节点补装 `uiautomation` 后，`comtypes` / `pywinauto` / `uiautomation` 三个库均可导入；DEV 节点也通过了同样的导入检查。 |

**证据**：
```text
RUN import check: OK
DEV import check: OK
```

---

### T-02: P0 方案验证 — comtypes 能否恢复 UI 树 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ❌ FAIL |
| **实际行为** | 脚本在进入方案 A 之前就失败：`find_wechat_hwnd()` 使用 `FindWindowW("WeChatMainWndForPC")` 未找到窗口，因此根本没有执行 comtypes 恢复逻辑。 |

**证据**（RUN，UTF-8 交互执行）：
```text
============================================================
  微信 4.x UIA 树恢复验证 — P0 方案
  时间: 2026-03-26T12:27:01.592265
  系统: Windows-10-10.0.26100-SP0
  Python: 3.11.6
============================================================
? 未找到微信窗口（class_name='WeChatMainWndForPC'），请确保微信已启动
   提示：微信 4.x 窗口类名为 WeChatMainWndForPC
```

**偏差说明**：
- 预期是看到「方案 A: comtypes」并输出子元素数量。
- 实际连方案 A 都没有进入，阻塞点是脚本的窗口查找前置条件失败。

---

### T-03: P0 方案验证 — pywinauto 能否恢复 UI 树 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ❌ FAIL |
| **实际行为** | 同 T-02。由于脚本在 `find_wechat_hwnd()` 阶段提前退出，方案 B 没有实际执行。 |

**证据**：
```text
未出现“▶ 方案 B: pywinauto”标题；脚本在窗口句柄获取阶段已退出。
```

**偏差说明**：
- 这不是 pywinauto 恢复逻辑本身失败，而是脚本没有机会走到这一分支。

---

### T-04: P0 方案验证 — uiautomation 库能否恢复 UI 树 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ❌ FAIL |
| **实际行为** | 同 T-02。方案 C 没有实际执行。 |

**证据**：
```text
未出现“▶ 方案 C: uiautomation 库”标题；脚本在窗口句柄获取阶段已退出。
```

---

### T-05: 汇总结论确认 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ❌ FAIL |
| **实际行为** | 未出现“验证结果汇总”；也没有生成 `uia_recovery_result.json`。 |

**证据**：
```text
RUN: NO_JSON_FILE
DEV: NO_JSON
```

**偏差说明**：
- 预期是至少一种方案成功，并生成 JSON 报告。
- 实际脚本在窗口检测阶段终止，因此根本没有汇总阶段产物。

---

### T-06: 引用保活验证 — P1

| 字段 | 值 |
|------|-----|
| **结果** | ⚠️ PARTIAL |
| **实际行为** | 未执行。因为 T-05 未达成“至少一种方案成功”，不满足前置条件。 |

**偏差说明**：
- 当前无法验证“保持脚本运行中”是否真的能保活 UIA 树。

---

### T-07: 脚本退出后 UI 树是否回收 — P2

| 字段 | 值 |
|------|-----|
| **结果** | ⚠️ PARTIAL |
| **实际行为** | 未执行。因为 T-06 未达成，不满足前置条件。 |

---

## 3. 测试结果汇总

| 优先级 | 总数 | PASS | FAIL | PARTIAL | 通过率 |
|--------|------|------|------|---------|--------|
| P0（阻塞） | 5 | 1 | 4 | 0 | 20% |
| P1（重要） | 1 | 0 | 0 | 1 | 0% |
| P2（增强） | 1 | 0 | 0 | 1 | 0% |
| **合计** | **7** | **1** | **4** | **2** | **14.3%** |

### 闭环判定

- [ ] P0 100% PASS
- [ ] P1 ≥ 80% PASS
- [x] P2 已评估

**结论**：🔴 需重大修改

---

## 4. 新发现问题

| # | 问题描述 | 严重程度 | 复现步骤 | 建议 |
|---|---------|---------|---------|------|
| 1 | `scripts/runtime/verify_uia_recovery.py` 将 `WECHAT_CLASS_NAME` 硬编码为 `WeChatMainWndForPC`，而两端真机当前可见主窗体类名均为 `Qt51514QWindowIcon` | P0 | 运行 `python verify_uia_recovery.py` | 先把窗口发现逻辑改为枚举多类名，至少兼容 `Qt51514QWindowIcon` |
| 2 | 开发报告“不同微信版本的 ClassName 可能不同，脚本搜索多种已知类名”的说法与实际代码不一致 | P0 | 查看 `scripts/runtime/verify_uia_recovery.py:34` 与 `:48` | 修正文档或代码，避免测试预期与实现脱节 |
| 3 | RUN 节点默认控制台编码下，脚本直接打印 emoji 会触发 `UnicodeEncodeError`，导致失败信息都不完整 | P1 | 在默认 PowerShell/计划任务环境执行脚本 | 脚本应避免依赖 emoji 输出，或显式设置 UTF-8 输出编码 |
| 4 | DEV 节点补充探针显示当前微信处于登录/挤下线提示态，出现 `mmui::LoginWindow`、`mmui::XDialog` 等控件；说明单纯用“child_count > 2”作为成功标准存在误判风险 | P1 | 运行 `runtime/tmp_capture_wechat_window_uia.py` | 成功标准应优先检查 `mmui::ChatSingleWindow` / `mmui::ChatTextItemView`，再辅以 child_count |

---

## 5. 性能观察（可选）

| 指标 | 实测值 | 基线/预期 | 评价 |
|------|--------|---------|------|
| RUN 单次交互式验证脚本调度耗时 | 约 12–14 秒 | 诊断脚本可接受 | ✅ 正常 |
| DEV 单次脚本执行耗时 | < 1 秒 | 诊断脚本可接受 | ✅ 正常 |

---

## 6. 建议与补充

- 这轮失败不是 UIA 客户端冒充三方案本身已被证伪，而是**验证脚本的窗口发现前置条件先失败了**。
- 开发节点下一步应先修验证脚本的“找窗体”问题，再谈三路方案哪一路真正有效。
- 建议把成功判定从“`child_count > 2`”升级为“双条件”判定：
  - 必须发现 `mmui::ChatSingleWindow` 或 `mmui::ChatTextItemView`
  - `child_count > 2` 仅作为辅助信号
- RUN 节点补充探针再次确认：当前真实可见窗体为：
  ```json
  {
    "hwnd": 197660,
    "pid": 14020,
    "title": "微信",
    "class_name": "Qt51514QWindowIcon",
    "rect": [773, 165, 1680, 815],
    "summary": {
      "descendant_count": 2,
      "mmui_count": 0,
      "has_chat_single_window": false,
      "has_chat_text_item_view": false
    }
  }
  ```

---

## 7. 附件清单

| # | 类型 | 文件名/路径 | 说明 |
|---|------|-----------|------|
| 1 | 开发报告 | `docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wechat_uia_tree_recovery_20260326.md` | 本次对应的开发实测报告 |
| 2 | 运行日志 | `edge-lan:D:\Natsume_Ai\runtime\verify_uia_recovery_interactive.out.txt` | RUN 节点交互式执行验证脚本输出 |
| 3 | 辅助探针 | `D:\Natsume_Ai\runtime\tmp_capture_wechat_window_uia.py` | 双节点补充 UIA 窗体探针 |
| 4 | 运行日志 | `edge-lan:D:\Natsume_Ai\runtime\interactive_uia_capture.out.txt` | RUN 节点辅助探针输出 |

