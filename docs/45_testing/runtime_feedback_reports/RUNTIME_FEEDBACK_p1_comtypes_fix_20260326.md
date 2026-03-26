受众：CODEX
标题：运行反馈报告 — P1 脚本自举修复（comtypes.gen + S2 启动逻辑统一）

# 运行反馈报告：P1 脚本自举修复

| 字段 | 值 |
|------|-----|
| **功能名称** | P1 脚本 `comtypes.gen.UIAutomationClient` 导入修复 + S2 Narrator 启动逻辑统一 |
| **对应开发报告** | `DEV_TEST_REPORT_p1_comtypes_fix_20260326.md` |
| **运行节点** | `edge-lan`（辅以 DEV `D:\Natsume_Ai` 本机验证） |
| **报告日期** | 2026-03-26 |
| **测试人** | Codex |
| **总体结论** | 🟡 部分通过 |

---

## 1. 测试环境

| 项目 | 值 |
|------|-----|
| **操作系统** | RUN: `Windows-10-10.0.26100-SP0`；DEV: `Windows-10-10.0.26220-SP0` |
| **Python** | RUN: `3.11.6`；DEV: `3.11.0` |
| **GPU** | RUN: `GameViewer Virtual Display Adapter` + `Intel(R) Iris(R) Xe Graphics`；DEV: `GameViewer Virtual Display Adapter` + `NVIDIA GeForce RTX 5080` |
| **微信版本** | RUN: `4.1.1.19`；DEV: `4.1.7.59` |
| **VTS 版本** | N/A |
| **Git Commit** | RUN/DEV 最终均为 `ab6a91ff` |
| **Node.js** | N/A |
| **其他关键依赖** | `comtypes`、`pywinauto`、`uiautomation`、RUN 侧交互式计划任务（`schtasks /IT`） |

### 环境差异说明

- 本轮先在本地 `git pull --ff-only origin codex`，随后发现 RUN 侧仍停留在旧提交 `464b3de6`，已补做 `edge-lan:D:\Natsume_Ai` 的 `git pull --ff-only origin codex`，再开始正式真机重测。
- T-01 属于非界面检查，DEV 与 RUN 均直接执行；涉及微信/UIA 的测试全部在 RUN 侧通过交互式计划任务进入 `console` 会话执行。
- S1/S3 独立脚本属于长时间运行进程；为避免任务长期占用，本轮采用“交互式启动 + 30 秒后强制回收”的方式尝试留证，但 stdout 文件最终为 0 字节，因此 T-05/T-06 以 RUN 标准对比器的同策略分支日志作为主要真机证据，并把独立脚本留证问题单列为新发现问题。

---

## 2. 测试执行记录

> 对照 `D:\Natsume_Ai\docs\45_testing\dev_test_reports\DEV_TEST_REPORT_p1_comtypes_fix_20260326.md` 执行。

### T-01: comtypes GetModule 兼容性 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | DEV 与 RUN 两端直接执行 `GetModule('UIAutomationCore.dll')` 后，均能成功导入 `IUIAutomation`，`AttributeError` 不再出现。 |

**证据**：
```text
LOCAL: OK
REMOTE(edge-lan): OK
```

---

### T-02: GetModule OSError fallback — P1

| 字段 | 值 |
|------|-----|
| **结果** | ⚠️ N/A |
| **实际行为** | 本轮未触发 fallback。因为 RUN/DEV 两端短名 `UIAutomationCore.dll` 均直接加载成功，无需走 `%SystemRoot%\System32\UIAutomationCore.dll` 完整路径兜底。 |

**说明**：
- 该项属于条件触发测试；当前环境不构成失败，也不能证明 fallback 路径已被真机覆盖。

---

### T-03: 对比器标准模式全流程 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | RUN 侧交互式执行 `python test_p1_all_strategies.py` 后，基线恢复为有效值 `1`（不再是 `-1`），S1/S2/S3 三策略都进入了有效运行阶段；日志中已不再出现 `module 'comtypes.gen' has no attribute 'UIAutomationClient'`。 |

**证据**：
```text
--- 基线检查 ---
  子元素数: 1

[S1] ...
  [OK] 事件监听器已注册

[S2] ...
  [OK] Narrator 已通过 PowerShell 启动

[S3] ...
  [OK] 已注册: UIA事件=1, WinEvent=3, 缓存请求=OK
```

**偏差说明**：
- 自举修复已生效，但三策略最终仍都停留在 `child_count=1 / descendant_count=0`，说明后续问题已从“脚本起不来”转为“策略没有真正拉起微信 UIA 树”。

---

### T-04: S2 对比器 Narrator PowerShell fallback — P0

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | RUN 标准对比日志中，S2 首先命中 `WinError 740`，随后自动改走 PowerShell fallback 并成功启动 Narrator；单独执行 `--only s2` 时也不再报 `no_screen_reader_available`。 |

**证据**：
```text
[WARN] Narrator 直接启动失败: [WinError 740] 请求的操作需要提升。
[INFO] 尝试通过 PowerShell 启动 Narrator ...
[OK] Narrator 已通过 PowerShell 启动
```

**补充说明**：
- 单独 `--only s2` 复测时，日志显示 `Narrator 已在运行中`，说明至少不会再走回旧版的“找不到任何屏幕阅读器”错误路径。

---

### T-05: S1 单脚本独立运行 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ⚠️ PARTIAL |
| **实际行为** | RUN 标准对比器中的 S1 分支已经证明事件监听器能在真机上成功注册并持续 20 秒输出检查结果；但专门为 `p1_uia_event_listener.py` 制作的交互式独立包装任务在 30 秒强制回收后仅留下 0 字节输出文件，未能拿到独立入口的 stdout 证据。 |

**证据**：
```text
[S1] 持久化 UIA 事件监听器
[OK] 事件监听器已注册
[INFO] 等待 20s ...
5s / 10s / 15s / 20s: 子元素=1, 后代=0, 事件=0
```

**偏差说明**：
- 就“COM 类型库加载 + 事件处理器注册”这一修复目标而言，S1 已在 RUN 标准对比器中被有效覆盖。
- 但就“独立脚本入口必须单独留证”这一更严格口径而言，本项仍缺一份稳定 stdout 证据。

---

### T-06: S3 单脚本独立运行 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ⚠️ PARTIAL |
| **实际行为** | RUN 标准对比器中的 S3 分支已经证明 UIA/WinEvent/缓存请求初始化成功，并进行了多轮戳探；但 `p1_uia_keepalive_client.py` 的独立交互式包装任务同样只留下 0 字节输出文件，未拿到独立入口的 stdout 证据。 |

**证据**：
```text
[S3] Minimal Accessibility Client (主动戳探 + 监听)
[OK] 已注册: UIA事件=1, WinEvent=3, 缓存请求=OK
戳探 #2 / #4 / #6 / #8: 子元素=1, 后代=0, UIA事件=0, WinEvent=0
```

**偏差说明**：
- 修复目标“`IUIAutomation` 可创建、WinEvent 钩子可注册”已在真机上验证通过。
- 仍缺单独脚本 stdout 证据，建议后续为长跑脚本补 `--duration` / `--once` 之类的测试模式。

---

### T-07: verify_uia_recovery.py 回归验证 — P1

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | RUN 侧交互式 `python verify_uia_recovery.py` 在修复后正常执行，comtypes 路径不再报 `AttributeError`；结果与修复前的真实微信状态一致：`comtypes=1`、`pywinauto=1`、`uiautomation=0`，仍未恢复出聊天树。 |

**证据**：
```text
基线子元素数: 0
[FAIL] comtypes        -- 子元素数:   1  状态: failed
[FAIL] pywinauto       -- 子元素数:   1  状态: failed
[FAIL] uiautomation    -- 子元素数:   0  状态: failed
```

**偏差说明**：
- 回归目标已经达成：`verify_uia_recovery.py` 的 comtypes 分支恢复可用。
- 但微信 UIA Provider 依旧稀疏，说明真正的功能性瓶颈仍在策略效果层，而不是导入层。

---

## 3. 测试结果汇总

| 优先级 | 总数 | PASS | FAIL | PARTIAL | 通过率 |
|--------|------|------|------|---------|--------|
| P0（阻塞） | 5 | 3 | 0 | 2 | 60% |
| P1（重要，已执行） | 1 | 1 | 0 | 0 | 100% |
| **合计（已执行）** | **6** | **4** | **0** | **2** | **66.7%** |

**条件项说明**：T-02 为条件触发项，本轮记为 `N/A`，未纳入通过率。

### 闭环判定

- [ ] P0 100% PASS
- [x] P1 ≥ 80% PASS
- [x] 已确认自举修复生效

**结论**：🟡 自举修复已通过，策略效果仍需继续验证/增强

---

## 4. 新发现问题

| # | 问题描述 | 严重程度 | 复现步骤 | 建议 |
|---|---------|---------|---------|------|
| 1 | `comtypes.gen.UIAutomationClient` 自举失败问题已修复，但微信 UIA 树仍未被任何 P1 策略真正拉起；RUN 标准对比中 S1/S2/S3 全部维持 `child_count=1`、`descendant_count=0`。 | P0 | RUN 侧交互式执行 `python test_p1_all_strategies.py` | 下一轮应把重点转向“如何让 Provider 真正展开”，而不是继续围绕导入问题修补。 |
| 2 | S1/S3 长跑独立脚本在“交互式计划任务 + 强制回收”模式下，stdout 文件为 0 字节，导致难以稳定留证。 | P1 | RUN 侧交互式运行 `p1_uia_event_listener.py` / `p1_uia_keepalive_client.py` 并在外部强制结束 | 给这两个脚本增加 `--duration` / `--once` / `--self-exit-after` 测试参数，避免依赖外部强杀。 |
| 3 | S2 已能自动走 PowerShell fallback，但即便 Narrator 成功启动，UIA 树仍没有增长，说明“屏幕阅读器存在”本身不足以触发微信 4.x Provider 展开。 | P1 | RUN 侧标准模式或 `--only s2` | 可考虑更强触发手段：保持 Narrator 常驻更久、切换到 NVDA、或探索非 UIA 路径（OCR/Hook/图像识别）。 |

---

## 5. 性能观察（可选）

| 指标 | 实测值 | 基线/预期 | 评价 |
|------|--------|---------|------|
| 标准对比完成时长 | 约 60–70 秒 | 脚本可完整跑完 | ✅ 正常 |
| `--only s2` 单策略完成时长 | 约 25–30 秒 | 只执行单策略 | ✅ 正常 |
| S1/S3 独立包装留证 | 输出文件 0 字节 | 应能拿到 stdout | ⚠️ 不满足留证预期 |

---

## 6. 建议与补充

- **可以确认本轮修复已解决根因 #1/#2。** `GetModule` 方案让基线从 `-1` 恢复到 `1`，S2 也不再报 `no_screen_reader_available`。
- **下一步不该再围绕 `comtypes.gen` 纠缠。** 现在真正需要攻克的是：为什么在 Narrator / 事件监听 / keepalive 客户端都存在时，微信 4.x 仍只暴露 1 个直接子元素。
- **建议给长跑脚本加测试模式参数。** 这能让 RUN 节点更稳定地自动留证，而无需外部强制杀进程。
- **如开发节点继续沿 UIA 路径推进，建议优先顺序：** 先给 S1/S3 增加可自退出模式 → 再尝试更强辅助技术（NVDA 常驻/更长等待）→ 若仍无效，再评估非 UIA 路径。

---

## 7. 附件清单

| # | 类型 | 文件名/路径 | 说明 |
|---|------|-----------|------|
| 1 | 开发报告 | `docs/45_testing/dev_test_reports/DEV_TEST_REPORT_p1_comtypes_fix_20260326.md` | 本轮对应开发报告 |
| 2 | 运行日志 | `edge-lan:D:\Natsume_Ai\runtime\p1_compare_edge_standard.out.txt` | RUN 标准模式对比输出 |
| 3 | 运行日志 | `edge-lan:D:\Natsume_Ai\runtime\p1_only_s2_edge.out.txt` | RUN 单独 S2 输出 |
| 4 | 运行日志 | `edge-lan:D:\Natsume_Ai\runtime\verify_uia_recovery_edge_p1check.out.txt` | RUN P0 回归输出 |
| 5 | 运行日志 | `edge-lan:D:\Natsume_Ai\runtime\p1_event_listener_edge_fix.out.txt` | RUN S1 独立包装输出（0 字节，留证问题样本） |
| 6 | 运行日志 | `edge-lan:D:\Natsume_Ai\runtime\p1_keepalive_edge_fix.out.txt` | RUN S3 独立包装输出（0 字节，留证问题样本） |
| 7 | JSON 结果 | `edge-lan:D:\Natsume_Ai\scripts\runtime\p1_comparison_result.json` | RUN 最近一次 P1 对比 JSON（由 `--only s2` 覆盖） |
| 8 | JSON 结果 | `edge-lan:D:\Natsume_Ai\scripts\runtime\uia_recovery_result.json` | RUN 最近一次 P0 回归 JSON |
