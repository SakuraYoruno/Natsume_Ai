受众：CODEX
标题：运行反馈报告 — P1 三方案 UIA 恢复策略

# 运行反馈报告：P1 三方案 UIA 恢复策略

| 字段 | 值 |
|------|-----|
| **功能名称** | P1 三方案 UIA 恢复策略（事件监听 / 屏幕阅读器 / 保活客户端） |
| **对应开发报告** | `DEV_TEST_REPORT_p1_uia_strategies_20260326.md` |
| **运行节点** | `edge-lan`（辅以 DEV `D:\Natsume_Ai` 本机对照） |
| **报告日期** | 2026-03-26 |
| **测试人** | Codex |
| **总体结论** | 🔴 关键失败 |

---

## 1. 测试环境

| 项目 | 值 |
|------|-----|
| **操作系统** | RUN: `Windows-10-10.0.26100-SP0`；DEV: `Windows-10-10.0.26220-SP0` |
| **Python** | RUN: `3.11.6`；DEV: `3.11.0` |
| **GPU** | RUN: `GameViewer Virtual Display Adapter` + `Intel(R) Iris(R) Xe Graphics`；DEV: `GameViewer Virtual Display Adapter` + `NVIDIA GeForce RTX 5080` |
| **微信版本** | RUN: `4.1.1.19`；DEV: `4.1.7.59` |
| **VTS 版本** | N/A |
| **Git Commit** | RUN/DEV 均为 `464b3de60026f271dffd84763ac3e0ee40ac6c47` |
| **Node.js** | N/A |
| **其他关键依赖** | `comtypes`、`pywinauto`、`uiautomation`、RUN 侧交互式计划任务（`schtasks /IT`） |

### 环境差异说明

- 按本轮指令，**未执行 `git pull`**；直接基于当前双节点同一提交 `464b3de6` 执行最新测试计划。
- RUN 侧所有涉及微信窗口/UIA 的命令都必须通过**交互式计划任务**进入 `console` 会话执行；普通 SSH 会话无法稳定拿到桌面窗口证据。
- DEV 侧当前微信并不处于稳定聊天主界面，`verify_uia_recovery.py` 观测到 `mmui::LoginWindow` / `mmui::XDialog` 痕迹；因此 DEV 侧只用于复现脚本自举问题，**真机结论以 RUN 侧交互式执行为准**。

---

## 2. 测试执行记录

> 对照 `docs/45_testing/dev_test_reports/DEV_TEST_REPORT_p1_uia_strategies_20260326.md` 执行；其中 RUN 侧核心证据来自 `edge-lan` 交互式计划任务，DEV 侧用于补充定位脚本自举失败原因。

### T-01: 依赖确认 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | DEV 与 RUN 两端均可成功导入 `comtypes`、`pywinauto`、`uiautomation`。 |

**证据**：
```text
LOCAL_OK
REMOTE_OK
```

**补充说明**：
- 本轮按指令跳过了开发报告中的 `git pull origin codex`，但两端 HEAD 已一致，因此不影响本次依赖确认。

---

### T-02: 对比测试脚本一键运行 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ❌ FAIL |
| **实际行为** | RUN 侧交互式标准模式能够找到微信主窗体 `Qt51514QWindowIcon`，但基线 `child_count` 直接变成 `-1`，三种策略均未进入有效对比；`p1_comparison_result.json` 显示 S1/S3 共用同一启动错误，S2 则报 `no_screen_reader_available`。 |

**证据**：
```json
{
  "baseline": {"child_count": -1},
  "strategies": {
    "s1": {"error": "module 'comtypes.gen' has no attribute 'UIAutomationClient'"},
    "s2": {"error": "no_screen_reader_available"},
    "s3": {"error": "module 'comtypes.gen' has no attribute 'UIAutomationClient'"}
  }
}
```

**偏差说明**：
- 该测试确实生成了汇总表和 JSON，但**对比结论无效**；因为 S1/S3 在 UIA 初始化阶段即失败，未形成有效策略评分。
- 日志仍给出 `recommendation: S1` / `best_score: 0`，属于误导性输出，不可作为选型依据。

---

### T-03: S1 方案单独验证 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ❌ FAIL |
| **实际行为** | DEV 侧直接运行 `p1_uia_event_listener.py` 可定位微信窗口，但在 `IUIAutomation` 初始化阶段立即失败，未注册任何事件处理器，因此 RUN 侧也无法进入“等待 10 秒后再跑 `verify_uia_recovery.py`”的阶段。RUN 标准对比日志中的 S1 分支报错与 DEV 完全一致。 |

**证据**：
```text
[OK] 找到微信窗口: class_name='Qt51514QWindowIcon', hwnd=0x00110068
[INFO] 初始化 COM / IUIAutomation ...
[FAIL] COM 初始化失败: module 'comtypes.gen' has no attribute 'UIAutomationClient'
```

**偏差说明**：
- 开发报告预期的“事件监听器注册成功 + 心跳输出 + 再跑 `verify_uia_recovery.py`”没有发生。
- 当前失败发生在**脚本自举层**，不是微信 UIA 树恢复效果层。

---

### T-04: S2 方案单独验证 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ❌ FAIL |
| **实际行为** | DEV 侧短参运行 `p1_nvda_assisted_uia.py --wait 5 --checks 1 --interval 1` 时，直接启动 Narrator 命中 `WinError 740`，但脚本内置的 PowerShell fallback 可以拉起 Narrator；随后 UIA 检查仍因 `comtypes.gen.UIAutomationClient` 缺失而返回 `-1`。RUN 侧一键对比器中的 S2 分支没有复用该 fallback，只返回 `no_screen_reader_available`。 |

**证据**：
```text
[WARN] Narrator 启动失败: [WinError 740] 请求的操作需要提升。
[OK] Narrator 已通过 PowerShell 启动
[WARN] UIA 树检查异常: module 'comtypes.gen' has no attribute 'UIAutomationClient'
[FAIL] P1 方案二未恢复 UIA 树。
```

**偏差说明**：
- S2 存在两个问题：一是 UIA 初始化仍被 `comtypes.gen` 缺陷阻断；二是对比器和单脚本的 Narrator 启动策略不一致。
- 因 RUN 侧一键对比器未进入有效检查环节，本次不能证明屏幕阅读器方案在真机上“有效”或“无效”，只能证明**当前实现不可测**。

---

### T-05: S3 方案单独验证 — P0

| 字段 | 值 |
|------|-----|
| **结果** | ❌ FAIL |
| **实际行为** | DEV 侧直接运行 `p1_uia_keepalive_client.py` 时可定位微信窗口，但 `initialize_com()` 立即抛出 `module 'comtypes.gen' has no attribute 'UIAutomationClient'`；脚本虽落出了 `p1_keepalive_status.json`，但其中所有探针/事件计数均为 0。RUN 侧交互式 `--only s3` 过滤模式也复现同类失败，未观察到任何恢复迹象。 |

**证据**：
```text
[FAIL] 运行异常: module 'comtypes.gen' has no attribute 'UIAutomationClient'
[INFO] 最终结果: failed
最大子元素数: 0
总戳探次数: 0
UIA 结构事件: 0
WinEvent 总数: 0
```

**偏差说明**：
- S3 没有进入“主动戳探 + WinEvent + 缓存请求”的有效运行阶段。
- 因此 T-05 的失败同样属于**脚本启动失败**，不是策略效果评估失败。

---

### T-06: 快速模式对比 — P1

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | RUN 侧交互式 `python test_p1_all_strategies.py --quick` 正常执行，输出格式与标准模式一致；日志起始时间为 `2026-03-26 15:13:02.999382`，输出文件最后写入时间为 `2026-03-26 15:13:13`，实际完成时间约 10 秒，满足“不超过 60 秒”的预期。 |

**证据**：
```text
模式: 快速
LastWriteTime: 2026-03-26 15:13:13
```

**补充说明**：
- 该测试验证的是 `--quick` 选项本身可用；并不改变三种策略在本轮仍全部失败的事实。

---

### T-07: 单方案模式 — P1

| 字段 | 值 |
|------|-----|
| **结果** | ✅ PASS |
| **实际行为** | RUN 侧交互式 `python test_p1_all_strategies.py --only s3` 只执行了 S3 分支，输出中未再出现 S1 / S2，汇总表中也只有一行，说明单方案过滤逻辑正常。 |

**证据**：
```text
限定方案: S3
--- S3 前基线 ---
[S3] Minimal Accessibility Client (主动戳探 + 监听)
S3     [FAIL]       0      -1       0        无               -1
```

**补充说明**：
- 该测试验证的是 `--only s3` 过滤能力；S3 功能本身仍未通过。

---

### T-08: 保活持久性（S1 或 S3 长时间运行后验证） — P2

| 字段 | 值 |
|------|-----|
| **结果** | ⚠️ PARTIAL |
| **实际行为** | 未满足前置条件。RUN 侧交互式重新执行 `verify_uia_recovery.py` 后，基线仍为 `0`，三种 P0 方法结果分别是 `1 / 1 / 0` 个直接子元素；本轮没有任何 P1 策略表现出“子元素数上升且可持续”的迹象，因此未进入长时间保活验证。 |

**证据**：
```text
基线子元素数: 0
[FAIL] comtypes        -- 子元素数:   1  状态: failed
[FAIL] pywinauto       -- 子元素数:   1  状态: failed
[FAIL] uiautomation    -- 子元素数:   0  状态: failed
```

**偏差说明**：
- 这不是“长稳态退化”问题，而是**压根没有观察到可持续提升**。
- 在修复 P1 脚本自举缺陷之前，继续做 T-08 没有意义。

---

## 3. 测试结果汇总

| 优先级 | 总数 | PASS | FAIL | PARTIAL | 通过率 |
|--------|------|------|------|---------|--------|
| P0（阻塞） | 5 | 1 | 4 | 0 | 20% |
| P1（重要） | 2 | 2 | 0 | 0 | 100% |
| P2（增强） | 1 | 0 | 0 | 1 | 0% |
| **合计** | **8** | **3** | **4** | **1** | **37.5%** |

### 闭环判定

- [ ] P0 100% PASS
- [x] P1 ≥ 80% PASS
- [x] P2 已评估

**结论**：🔴 需重大修改

---

## 4. 新发现问题

| # | 问题描述 | 严重程度 | 复现步骤 | 建议 |
|---|---------|---------|---------|------|
| 1 | P1 三个脚本和对比器公共依赖 `comtypes.gen.UIAutomationClient`，在双节点上都会触发 `AttributeError`，导致基线直接变成 `-1`，S1/S3 无法启动。 | P0 | 在任一节点执行 `cd D:\Natsume_Ai\scripts\runtime && python p1_uia_event_listener.py` 或 `python p1_uia_keepalive_client.py` | 先修复 `UIAutomationClient` 类型库加载：显式 `GetModule/UIAutomationCore.dll` 或改为可兜底的导入路径；修完后先重跑 T-02/T-03/T-05。 |
| 2 | `test_p1_all_strategies.py` 的 S2 路径只有“直接启动 Narrator”逻辑，没有复用 `p1_nvda_assisted_uia.py` 中可工作的 PowerShell fallback，因此 RUN 侧会误报 `no_screen_reader_available`。 | P1 | RUN 侧交互式执行 `python test_p1_all_strategies.py` | 统一 S2 的启动实现，避免“单脚本能拉起 Narrator、对比器却不行”的分叉行为。 |
| 3 | 即使在 RUN 侧 `console` 交互桌面中重新执行 `verify_uia_recovery.py`，微信 4.x UIA 树仍只有 `0~1` 个直接子元素，说明核心 Provider 稀疏问题依旧存在。 | P0 | RUN 侧执行交互式 `verify_uia_recovery.py` | 修复 P1 自举后，优先在 RUN 侧重测 S1/S3；若仍无提升，再评估非 UIA 路径（OCR/图像识别/Hook）。 |

---

## 5. 性能观察（可选）

| 指标 | 实测值 | 基线/预期 | 评价 |
|------|--------|---------|------|
| `--quick` 模式完成时长 | 约 10 秒 | ≤ 60 秒 | ✅ 正常 |
| `--only s3` 过滤行为 | 仅输出 S3 一行汇总 | 仅运行目标策略 | ✅ 正常 |

---

## 6. 建议与补充

- **先修脚本自举，再谈策略优劣。** 当前 S1/S3 在双节点上都死在 `comtypes.gen.UIAutomationClient`，此时继续比较“哪种 P1 策略更有效”没有意义。
- **统一 S2 启动路径。** 把 `p1_nvda_assisted_uia.py` 中可工作的 Narrator PowerShell fallback 下沉到 `test_p1_all_strategies.py`，避免 RUN 侧对比器误判。
- **继续坚持 RUN 侧交互式计划任务验证。** 本轮再次证明：普通 SSH 会话不具备有效桌面窗口上下文，不能用于微信 UIA 真机判定。
- **修复后优先重跑顺序**：T-02（标准对比） → T-03（S1）/T-05（S3） → T-04（S2） → 若任一策略出现提升，再执行 T-08。

---

## 7. 附件清单

| # | 类型 | 文件名/路径 | 说明 |
|---|------|-----------|------|
| 1 | 开发报告 | `docs/45_testing/dev_test_reports/DEV_TEST_REPORT_p1_uia_strategies_20260326.md` | 本轮对应开发实测报告 |
| 2 | 运行日志 | `edge-lan:D:\Natsume_Ai\runtime\p1_compare_edge_standard.out.txt` | RUN 侧交互式标准模式对比输出 |
| 3 | 运行日志 | `edge-lan:D:\Natsume_Ai\runtime\p1_compare_edge_quick.out.txt` | RUN 侧交互式快速模式输出 |
| 4 | 运行日志 | `edge-lan:D:\Natsume_Ai\runtime\p1_only_s3_edge.out.txt` | RUN 侧交互式 `--only s3` 输出 |
| 5 | 运行日志 | `edge-lan:D:\Natsume_Ai\runtime\verify_uia_recovery_edge_p1check.out.txt` | RUN 侧交互式 P0 复核输出 |
| 6 | 运行日志 | `edge-lan:D:\Natsume_Ai\runtime\interactive_uia_capture.out.txt` | RUN 侧交互式窗口探针输出 |
| 7 | JSON 结果 | `D:\Natsume_Ai\scripts\runtime\p1_comparison_result.json` | DEV 侧一键对比 JSON，清晰暴露 S1/S3 公共导入错误 |
| 8 | JSON 结果 | `D:\Natsume_Ai\scripts\runtime\p1_keepalive_status.json` | DEV 侧 S3 单脚本状态报告（0 探针 / 0 事件） |
| 9 | JSON 结果 | `D:\Natsume_Ai\scripts\runtime\p1_nvda_assisted_status.json` | DEV 侧 S2 单脚本状态报告 |
| 10 | JSON 结果 | `D:\Natsume_Ai\scripts\runtime\uia_recovery_result.json` | DEV 侧 P0 复核结果 |
