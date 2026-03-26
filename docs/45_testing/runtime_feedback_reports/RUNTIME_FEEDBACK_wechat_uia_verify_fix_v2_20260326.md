受众：CODEX
标题：运行反馈报告 — 微信 4.x UIA 树恢复验证脚本修正（v2）

# 运行反馈报告：微信 4.x UIA 树恢复验证脚本修正（v2）

| 字段 | 值 |
|------|-----|
| **功能名称** | 微信 4.x UIA 树恢复验证脚本修正（窗口发现 + 编码 + 成功判定） |
| **对应开发报告** | `DEV_TEST_REPORT_wechat_uia_verify_fix_v2_20260326.md` |
| **运行节点** | `edge-lan`（辅以 DEV 节点对照） |
| **报告日期** | 2026-03-26 |
| **测试人** | Codex |
| **总体结论** | 🟡 部分通过 |

---

## 1. 测试环境

| 项目 | 值 |
|------|-----|
| **操作系统** | RUN: Microsoft Windows 11 专业版；DEV: Microsoft Windows 11 Pro for Workstations |
| **Python** | RUN: 3.11.6；DEV: 3.11.0 |
| **GPU** | 两端均为 `GameViewer Virtual Display Adapter` |
| **微信版本** | RUN: 4.1.1.19；DEV: 4.1.7.59 |
| **VTS 版本** | N/A |
| **Git Commit** | `64726a2` |
| **Node.js** | N/A |
| **其他关键依赖** | `comtypes`、`pywinauto`、`uiautomation`、交互式计划任务（RUN） |

### 环境差异说明

- RUN 节点用于主验证，采用交互式计划任务进入 `console` 桌面运行 `verify_uia_recovery.py`。
- DEV 节点只做辅助对照；当时微信处于登录/挤下线提示态，出现 `mmui::LoginWindow` / `mmui::XDialog`，不适合作为聊天窗体恢复成功判定依据。
- RUN 侧为了拿到实时 stdout，测试过程中额外使用了 `python -u` 启动参数；该参数仅影响缓冲行为，不改变脚本逻辑，也不涉及代码改动。

---

## 2. 测试执行记录

### T-01: 窗口发现 — 多类名兼容 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | RUN 节点成功通过 `Qt51514QWindowIcon` 找到微信主窗体，不再出现上轮的 `WeChatMainWndForPC` 硬编码失配问题。 |

**证据**：
```text
[OK] 找到微信窗口: class_name='Qt51514QWindowIcon', hwnd=0x0003031A (197402)
  使用窗口: class_name='Qt51514QWindowIcon', hwnd=0x0003031A
```

---

### T-02: 编码兼容性 — 无 UnicodeEncodeError — P0

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | RUN 节点在默认计划任务环境下执行时，未再出现上轮的 `UnicodeEncodeError`；输出使用 `[OK]/[FAIL]` 纯 ASCII 标记。 |

**证据**：
```text
[OK] 找到微信窗口...
[FAIL] UI 树未恢复（仅 1 个元素）
[INFO] JSON 结果已保存: uia_recovery_result.json
```

**补充说明**：
- 输出中仍有 `pywinauto` 自身的 `UserWarning: Revert to STA COM threading mode`，但这不是编码崩溃，也未中断流程。

---

### T-03: P0 方案 A/B/C 实际执行 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | RUN 节点已完整执行 A/B/C 三个方案，并分别输出直接子元素数量。 |

**证据**：
```text
[A] comtypes (COM 直调)
  直接子元素数量: 1

[B] pywinauto (UIA backend)
  [OK] pywinauto 通过 class_name='Qt51514QWindowIcon' 连接成功
  直接子元素数量: 1

[C] uiautomation 库
  直接子元素数量: 0
```

---

### T-04: 双条件成功判定 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ⚠️ PARTIAL |
| **实际行为** | 失败分支判定工作正常：三种方案都被正确标记为 `failed`。但本轮没有覆盖到 `partial` / `success` 分支，因此“双条件成功判定”只完成了负向验证。 |

**证据**：
```text
  [FAIL] comtypes        -- 子元素数:   1  状态: failed
  [FAIL] pywinauto       -- 子元素数:   1  状态: failed
  [FAIL] uiautomation    -- 子元素数:   0  状态: failed
```

**偏差说明**：
- 这说明“误把登录态判成 success”的问题已被规避。
- 但由于当前 RUN 节点仍未恢复出聊天控件树，无法验证 `partial` / `success` 分支的真实行为。

---

### T-05: pywinauto 多类名连接 — P1

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | 方案 B 成功通过 `Qt51514QWindowIcon` 连接到微信窗体，不再卡在旧类名上。 |

**证据**：
```text
[OK] pywinauto 通过 class_name='Qt51514QWindowIcon' 连接成功
```

---

### T-06: JSON 报告生成 — P1

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | RUN 节点成功生成 `uia_recovery_result.json`，且包含 `wechat_class_name`、`any_partial` 等新增字段。 |

**证据**：
```json
{
  "wechat_class_name": "Qt51514QWindowIcon",
  "baseline_child_count": 0,
  "any_success": false,
  "any_partial": false,
  "results": [
    {"method": "comtypes", "child_count": 1, "status": "failed"},
    {"method": "pywinauto", "child_count": 1, "status": "failed"},
    {"method": "uiautomation", "child_count": 0, "status": "failed"}
  ]
}
```

---

### T-07: 引用保活（成功或 partial 时）— P2

| 字段 | 值 |
|------|-----|
| **结果** | ⚠️ PARTIAL |
| **实际行为** | 未触发。由于 `any_success=false` 且 `any_partial=false`，脚本按设计直接失败退出，没有进入保活分支。 |

**偏差说明**：
- 当前只能说明“失败场景下会正常退出”。
- 无法证明 `partial/success` 状态下的保活逻辑是否如预期工作。

---

## 3. 测试结果汇总

| 优先级 | 总数 | PASS | FAIL | PARTIAL | 通过率 |
|--------|------|------|------|---------|--------|
| P0（阻塞） | 4 | 3 | 0 | 1 | 75% |
| P1（重要） | 2 | 2 | 0 | 0 | 100% |
| P2（增强） | 1 | 0 | 0 | 1 | 0% |
| **合计** | **7** | **5** | **0** | **2** | **71.4%** |

### 闭环判定

- [ ] P0 100% PASS
- [x] P1 ≥ 80% PASS
- [x] P2 已评估

**结论**：🟡 需继续真机验证 / 继续推进 P1 方案

---

## 4. 新发现问题

| # | 问题描述 | 严重程度 | 复现步骤 | 建议 |
|---|---------|---------|---------|------|
| 1 | RUN 节点上 v2 脚本虽然能找到窗体并执行三种方案，但三种方案全部仍为 `failed`，未恢复出聊天控件树 | P0 | 在 `edge-lan` 交互桌面执行 `python scripts/runtime/verify_uia_recovery.py` | 按脚本汇总建议继续尝试 P1：持久化无障碍客户端 / NVDA / minimal listener |
| 2 | `pywinauto` 在 DEV 节点登录态窗体上可能卡在连接后阶段，本地 30 秒内未走到汇总输出 | P1 | DEV 节点运行 v2 脚本 | DEV 侧仅作辅助，不应用于聊天主界面成功判定；如需本机调试，应先让微信回到聊天主界面 |
| 3 | DEV 辅助探针显示当前本机微信处于登录/挤下线提示态，`mmui_count=9` 但无 `ChatSingleWindow` / `ChatTextItemView` | P1 | 运行 `runtime/tmp_capture_wechat_window_uia.py` | 说明 v2 的“双条件成功判定”方向是正确的，避免把登录态误判为恢复成功 |

---

## 5. 性能观察（可选）

| 指标 | 实测值 | 基线/预期 | 评价 |
|------|--------|---------|------|
| RUN 单次交互式验证耗时 | 约 16–20 秒 | 诊断脚本可接受 | ✅ 正常 |
| RUN 三方案子元素数 | `1 / 1 / 0` | 预期至少一种 > 2 且带聊天控件 | ⚠️ 偏低 |
| DEV 辅助探针子元素数 | 36（登录态窗体） | 不应作为聊天页成功依据 | ⚠️ 仅供诊断 |

---

## 6. 建议与补充

- 这轮 v2 的核心收获是：**验证脚本本身的窗口发现、编码和 JSON 汇总问题基本修好了**。
- 但业务结论仍是：**P0 方案在 RUN 真机上依旧没把完整聊天 UIA 树拉出来**。
- 下一步建议直接按脚本末尾提示推进更强恢复路径：
  - 持久化无障碍监听器
  - 或保持 NVDA/类似屏幕阅读器运行
  - 然后再回到同一套 v2 验证脚本复测
- DEV 节点如果继续协助测试，应先确保本机微信回到聊天主界面；否则登录态会污染对 `partial/success` 的判断。

---

## 7. 附件清单

| # | 类型 | 文件名/路径 | 说明 |
|---|------|-----------|------|
| 1 | 开发报告 | `docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wechat_uia_verify_fix_v2_20260326.md` | 本次对应开发实测报告 |
| 2 | 运行日志 | `edge-lan:D:\Natsume_Ai\runtime\verify_uia_recovery_edge_v2.out.txt` | RUN 节点 v2 验证完整输出 |
| 3 | JSON 结果 | `edge-lan:D:\Natsume_Ai\scripts\runtime\uia_recovery_result.json` | RUN 节点 v2 JSON 结果 |
| 4 | 辅助探针 | `D:\Natsume_Ai\runtime\tmp_capture_wechat_window_uia.py` | 本机登录态对照探针 |

