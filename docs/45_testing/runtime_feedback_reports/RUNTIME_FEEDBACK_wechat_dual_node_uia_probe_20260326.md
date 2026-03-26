受众：CODEX
标题：运行反馈报告 — 双节点微信 UIAutomation 探测

# 运行反馈报告：双节点微信 UIAutomation 探测

| 字段 | 值 |
|------|-----|
| **功能名称** | 微信 4.x 双节点 UIAutomation 探测 / UI 树恢复验证 |
| **对应开发报告** | 无（历史双节点微信探测结果补录，供开发节点修复 UIA 树恢复问题） |
| **运行节点** | RUN(`edge-lan`) + DEV(`D:\Natsume_Ai` 本机) 联合复探 |
| **报告日期** | 2026-03-26 |
| **测试人** | Codex（运行节点执行 / 文档回填） |
| **总体结论** | 🔴 关键失败 |

---

## 1. 测试环境

### 1.1 DEV 节点（本机）

| 项目 | 值 |
|------|-----|
| **操作系统** | Microsoft Windows 11 Pro for Workstations |
| **Python** | 3.11.0 |
| **GPU** | GameViewer Virtual Display Adapter |
| **微信版本** | 4.1.7.59 |
| **VTS 版本** | N/A（本次仅做微信 UIA 探测） |
| **Git Commit** | `e5e8e0f`（报告落库时）；核心探测结论形成于 `9d2aefc` |
| **其他关键依赖** | `comtypes`、`UIAutomationCore.dll`、`pywin32` |

### 1.2 RUN 节点（`edge-lan`）

| 项目 | 值 |
|------|-----|
| **操作系统** | Microsoft Windows 11 专业版 |
| **Python** | 3.11.6 |
| **GPU** | GameViewer Virtual Display Adapter |
| **微信版本** | 4.1.1.19 |
| **VTS 版本** | N/A（本次仅做微信 UIA 探测） |
| **Git Commit** | `e5e8e0f`（报告落库时）；核心探测结论形成于 `9d2aefc` |
| **其他关键依赖** | 交互式计划任务、`schtasks.exe`、`comtypes`、`pywin32` |

### 环境差异说明

- DEV 与 RUN 的微信版本不同：DEV 为 `4.1.7.59`，RUN 为 `4.1.1.19`。
- 尽管版本不同，两端均复现了同一类问题：**主窗体可定位，但聊天相关 UIA Provider 未暴露**。
- RUN 节点必须通过交互式计划任务进入 `console` 会话做探测；普通 SSH 会话无法得到有效桌面窗口证据。

---

## 2. 测试执行记录

> 本次报告整理的是 2026-03-26 已完成的双节点真机探测结果，目的是把之前散落在会话中的运行证据结构化回填到仓库，供开发节点据此修复。

### T-01: DEV 节点主窗体 UIA 探测 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ❌ FAIL |
| **实际行为** | 成功定位微信主窗体，但 UIA 仅暴露顶层壳元素，未出现 `mmui::ChatSingleWindow`、`mmui::ChatTextItemView` 等聊天控件。 |

**证据**（2026-03-26 11:12:15, Asia/Shanghai）：
```json
{
  "ok": true,
  "hwnd": 2561058,
  "pid": 26500,
  "title": "微信",
  "class_name": "Qt51514QWindowIcon",
  "rect": [478, 240, 1676, 967],
  "active_chat_title": "",
  "summary": {
    "descendant_count": 2,
    "mmui_count": 0,
    "has_chat_single_window": false,
    "has_chat_text_item_view": false
  },
  "candidate_windows": [
    {
      "hwnd": 2561058,
      "title": "微信",
      "class_name": "Qt51514QWindowIcon",
      "rect": [478, 240, 1676, 967],
      "pid": 26500
    }
  ]
}
```

**偏差说明**：
- 预期应至少看到聊天主窗体 / 消息项相关 `mmui::*` 控件。
- 实际只有顶层窗口壳，说明 UIA 树仍处于“瘦树/隐藏树”状态。

---

### T-02: DEV 节点窗口恢复后复探 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ❌ FAIL |
| **实际行为** | 本机微信主窗体曾出现屏幕外矩形；恢复到前台正常坐标后再次探测，UIA 树仍只有 2 个元素。 |

**证据**：
```json
{
  "hwnd": 2561058,
  "show_restore": true,
  "set_foreground": true,
  "rect_after": [478, 240, 1676, 967]
}
```

恢复后再次复探：
```json
{
  "ok": true,
  "hwnd": 2561058,
  "title": "微信",
  "class_name": "Qt51514QWindowIcon",
  "summary": {
    "descendant_count": 2,
    "mmui_count": 0,
    "has_chat_single_window": false,
    "has_chat_text_item_view": false
  }
}
```

**偏差说明**：
- 这一步的价值在于排除“坐标漂移/窗口位置异常”是主因。
- 即使窗口已经回到前台可见区域，聊天 UIA 树仍未长出，因此问题不在坐标层。

---

### T-03: RUN 节点交互式会话 UIA 探测 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ❌ FAIL |
| **实际行为** | 通过交互式计划任务进入 `console` 会话后，能稳定定位微信主窗体，但 UIA 树依旧只有 2 个元素。 |

**证据**（2026-03-26 11:12:24, Asia/Shanghai；`D:\Natsume_Ai\runtime\interactive_uia_capture.out.txt`）：
```json
{
  "ok": true,
  "hwnd": 197660,
  "pid": 14020,
  "title": "微信",
  "class_name": "Qt51514QWindowIcon",
  "rect": [773, 165, 1680, 815],
  "active_chat_title": "",
  "summary": {
    "descendant_count": 2,
    "mmui_count": 0,
    "has_chat_single_window": false,
    "has_chat_text_item_view": false
  },
  "candidate_windows": [
    {
      "hwnd": 197660,
      "title": "微信",
      "class_name": "Qt51514QWindowIcon",
      "rect": [773, 165, 1680, 815],
      "pid": 14020
    }
  ]
}
```

附带恢复日志：
```text
2026-03-26 11:12:24,872 [WARNING] Natsume.wechat_uia_bridge: ui_tree_recovery: sparse UI tree (descendants=2 < 5), will retry in 30s — consider starting Windows Narrator briefly or ensuring the WeChat window is visible
```

**偏差说明**：
- RUN 节点不存在“普通 SSH 会话看不到桌面”的误判：本次探测已经在交互会话中完成。
- 仍然只有 2 个元素，说明核心问题是 **微信未暴露完整 UIA Provider**，不是执行上下文错误。

---

### T-04: RUN 节点 Narrator 辅助恢复复探 — P1

| 字段 | 值 |
|------|-----|
| **结果** | ❌ FAIL |
| **实际行为** | 先通过交互式计划任务拉起 Narrator，再立即重跑 UIA 探测，结果与未拉起时完全一致。 |

**证据**：
```text
narrator_started=true
```

Narrator 后复探（2026-03-26 11:05:33）仍为：
```json
{
  "ok": true,
  "hwnd": 197660,
  "title": "微信",
  "class_name": "Qt51514QWindowIcon",
  "summary": {
    "descendant_count": 2,
    "mmui_count": 0,
    "has_chat_single_window": false,
    "has_chat_text_item_view": false
  }
}
```

**偏差说明**：
- 说明当前“短时拉起 Narrator”并未对该机型/该微信版本产生可观察的 UIA Provider 展开效果。
- 开发节点后续若继续沿 UIA 恢复方案推进，需要考虑更强触发方式，而不是继续在业务桥接层打补丁。

---

### T-05: 根因判别（坐标问题 vs UIA Provider 隐藏） — P1

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | 已获得足够证据判定：当前失败根因是 UIA Provider 未完整暴露，而不是坐标点击偏移、适配器转发、桥接队列或 webhook。 |

**证据要点**：
- DEV、RUN 两端均能定位同一个可见主窗体，并得到正常窗口矩形。
- 候选可见微信窗体均只有 1 个，不存在“抓错窗体”的证据。
- 主窗体稳定存在，但两端都缺失 `mmui::ChatSingleWindow` 与 `mmui::ChatTextItemView`。

---

## 3. 测试结果汇总

| 优先级 | 总数 | PASS | FAIL | PARTIAL | 通过率 |
|--------|------|------|------|---------|--------|
| P0（阻塞） | 3 | 0 | 3 | 0 | 0% |
| P1（重要） | 2 | 1 | 1 | 0 | 50% |
| P2（增强） | 0 | 0 | 0 | 0 | N/A |
| **合计** | **5** | **1** | **4** | **0** | **20%** |

### 闭环判定

- [ ] P0 100% PASS
- [ ] P1 ≥ 80% PASS
- [x] P2 已评估

**结论**：🔴 需重大修改

---

## 4. 新发现问题

| # | 问题描述 | 严重程度 | 复现步骤 | 建议 |
|---|---------|---------|---------|------|
| 1 | DEV 与 RUN 两端主窗体均只暴露 2~3 个 UIA 顶层元素，完全缺失聊天控件树 | P0 | 打开微信主窗体后运行 `runtime/tmp_capture_wechat_window_uia.py` | 继续攻克 UIA Provider 恢复，不要再在旧坐标/视觉补丁上消耗时间 |
| 2 | RUN 节点在交互会话中复探后仍失败，说明问题不在 SSH 非交互上下文 | P0 | 使用 `runtime/tmp_run_interactive_uia_capture_cmd.ps1` 运行探针 | 后续恢复方案必须在交互桌面继续验证，但重点应转向 Provider 触发机制 |
| 3 | 短时拉起 Narrator 未让 RUN 节点 UIA 树展开 | P1 | 先执行 `runtime/tmp_run_interactive_narrator_cmd.ps1`，再复探 | 评估更强的辅助功能客户端触发方式，或补充对 Narrator 常驻/会话归属的检测 |
| 4 | DEV 节点曾出现窗口跑到屏幕外，但恢复前台后 UIA 仍失败 | P1 | `ShowWindow(SW_RESTORE)` + `SetForegroundWindow` 后复探 | 坐标/窗口位置不是主因，不建议继续在页面乱跳或坐标偏移方向投入 |

---

## 5. 性能观察（可选）

| 指标 | 实测值 | 基线/预期 | 评价 |
|------|--------|---------|------|
| 单次 UIA 探针耗时（RUN） | 约 8–9 秒（含交互式计划任务调度） | 诊断脚本可接受 | ✅ 正常 |
| 单次 UIA 探针耗时（DEV） | < 1 秒 | 诊断脚本可接受 | ✅ 正常 |

---

## 6. 建议与补充

- 开发节点下一步应只聚焦 **UIA 树恢复 / 完整 Provider 暴露**，不要再回到旧的坐标点击补丁路线。
- 后续修复完成后，运行节点应优先重测以下两个断言：
  - `mmui::ChatSingleWindow` 是否出现
  - `mmui::ChatTextItemView` 是否出现
- 在这两个断言成立之前，不建议继续做“微信入站监听 / 业务链路 / ASR→微信→回发”类真机集成测试，因为链路前置条件尚未满足。

---

## 7. 附件清单

| # | 类型 | 文件名/路径 | 说明 |
|---|------|-----------|------|
| 1 | 探针脚本 | `D:\Natsume_Ai\runtime\tmp_capture_wechat_window_uia.py` | 本次双节点统一使用的 UIA 主窗体探针 |
| 2 | 运行日志 | `edge-lan:D:\Natsume_Ai\runtime\interactive_uia_capture.out.txt` | RUN 节点交互式 UIA 探测原始输出 |
| 3 | 运行脚本 | `D:\Natsume_Ai\runtime\tmp_run_interactive_uia_capture_cmd.ps1` | RUN 节点交互式计划任务包装脚本 |
| 4 | 运行脚本 | `D:\Natsume_Ai\runtime\tmp_run_interactive_narrator_cmd.ps1` | RUN 节点 Narrator 辅助触发脚本 |

