受众：CODEX
标题：开发实测报告 — P1 脚本自举修复（comtypes.gen + S2 启动逻辑统一）

# 开发实测报告：P1 脚本自举修复

| 字段 | 值 |
|------|-----|
| **功能名称** | P1 脚本 comtypes.gen.UIAutomationClient 导入修复 + S2 Narrator 启动逻辑统一 |
| **关联反馈报告** | `RUNTIME_FEEDBACK_p1_uia_strategies_20260326.md` |
| **开发节点** | DEV (`v_wenloliu` workstation) |
| **报告日期** | 2026-03-26 |
| **测试人** | Codex (DEV 侧静态验证 + 逻辑推演) |
| **总体结论** | 🟡 待真机验证 |

---

## 1. 问题溯源

RUN 节点反馈报告发现以下关键问题：

### 问题 #1：`comtypes.gen.UIAutomationClient` AttributeError (P0)

**根因**：P1 四个脚本（S1/S2/S3/对比器）和 P0 验证脚本都使用了：
```python
comtypes.client.CreateObject(
    "{ff48dba4-60ef-4201-aa87-54103eef594e}",
    interface=comtypes.gen.UIAutomationClient.IUIAutomation
)
```

这种写法依赖 `comtypes.gen` 目录下已存在 `UIAutomationClient.py` 类型库缓存文件。在全新环境或清理过缓存后，该文件不存在，直接引用 `comtypes.gen.UIAutomationClient` 会触发 `AttributeError: module 'comtypes.gen' has no attribute 'UIAutomationClient'`。

**修复方案**：在使用任何 `comtypes.gen.UIAutomationClient` 接口之前，先调用 `comtypes.client.GetModule("UIAutomationCore.dll")` 显式生成类型库缓存。该调用是幂等的（已存在时不重复生成），且带 `OSError` fallback（用完整路径兜底）。

### 问题 #2：对比器 S2 缺失 PowerShell fallback (P1)

**根因**：`test_p1_all_strategies.py` 的 `run_strategy_s2` 只有两级 Narrator 启动逻辑（直接启动 → 放弃），而单脚本 `p1_nvda_assisted_uia.py` 有三级回退（直接启动 → WinError 740 捕获 → PowerShell `Start-Process` fallback）。

RUN 节点的交互式计划任务可能无法直接以普通权限启动 `Narrator.exe`（需要 UAC 提升），单脚本能通过 PowerShell 绕过，但对比器不能。

**修复方案**：将 PowerShell fallback 逻辑统一到对比器中，并修复清理逻辑（PowerShell 启动时 `reader_proc` 为 `None`，改用 `reader_name` 判断是否需要 `taskkill`）。

---

## 2. 修改文件清单

| # | 文件 | 修改内容 |
|---|------|---------|
| 1 | `scripts/runtime/p1_uia_event_listener.py` | COM 初始化前添加 `GetModule("UIAutomationCore.dll")` + OSError fallback；改为从 `comtypes.gen.UIAutomationClient` 导入 `IUIAutomation` 后传入 `CreateObject` |
| 2 | `scripts/runtime/p1_nvda_assisted_uia.py` | `check_uia_tree()` 函数同上修复 |
| 3 | `scripts/runtime/p1_uia_keepalive_client.py` | `initialize_com()` 方法同上修复 |
| 4 | `scripts/runtime/test_p1_all_strategies.py` | 添加公共 `_ensure_uia_typelib()` + `_create_uia()` 辅助函数；`check_uia_tree()`/S1/S3 策略统一使用；S2 添加 PowerShell Narrator 回退；修复清理逻辑 |
| 5 | `scripts/runtime/verify_uia_recovery.py` | `test_comtypes()` 函数同上修复（一致性） |

---

## 3. 测试项目

### T-01: comtypes GetModule 兼容性 — P0

| 字段 | 值 |
|------|-----|
| **测试方法** | 静态分析 + 逻辑推演 |
| **预期行为** | `comtypes.client.GetModule("UIAutomationCore.dll")` 在 Windows 环境下成功加载类型库，`comtypes.gen.UIAutomationClient` 随后可用 |
| **验证命令** | `python -c "import comtypes.client; comtypes.client.GetModule('UIAutomationCore.dll'); from comtypes.gen.UIAutomationClient import IUIAutomation; print('OK')"` |
| **通过标准** | 输出 `OK`，无 AttributeError |

### T-02: GetModule OSError fallback — P1

| 字段 | 值 |
|------|-----|
| **测试方法** | 代码审查 |
| **预期行为** | 当 DLL 短名查找失败时，fallback 到 `%SystemRoot%\System32\UIAutomationCore.dll` 完整路径 |
| **验证方式** | 在 T-01 通过后可跳过；若 T-01 失败则需在 RUN 节点检查完整路径是否存在 |
| **通过标准** | 完整路径加载成功 |

### T-03: 对比器标准模式全流程 — P0

| 字段 | 值 |
|------|-----|
| **测试方法** | 真机执行 |
| **预期行为** | `python test_p1_all_strategies.py` 三策略全部进入有效运行阶段（不再出现 `comtypes.gen` 错误），`baseline.child_count ≠ -1` |
| **验证命令** | `cd D:\Natsume_Ai\scripts\runtime && python test_p1_all_strategies.py` |
| **通过标准** | 基线检查正常（≥0）、S1/S3 不报 `UIAutomationClient` 错误 |

### T-04: S2 对比器 Narrator PowerShell fallback — P0

| 字段 | 值 |
|------|-----|
| **测试方法** | 真机执行 |
| **预期行为** | 对比器 S2 在直接启动 Narrator 失败后自动尝试 PowerShell fallback，与单脚本行为一致 |
| **验证命令** | `python test_p1_all_strategies.py --only s2` |
| **通过标准** | 不再报 `no_screen_reader_available`，日志中出现 "PowerShell" 或 "已启动" 字样 |

### T-05: S1 单脚本独立运行 — P0

| 字段 | 值 |
|------|-----|
| **测试方法** | 真机执行 |
| **预期行为** | `python p1_uia_event_listener.py` 成功初始化 COM，注册事件监听器，进入心跳循环 |
| **验证命令** | `python p1_uia_event_listener.py`（运行 15-30s 后 Ctrl+C） |
| **通过标准** | 输出 "IUIAutomation 实例创建成功"、"事件处理器已注册" |

### T-06: S3 单脚本独立运行 — P0

| 字段 | 值 |
|------|-----|
| **测试方法** | 真机执行 |
| **预期行为** | `python p1_uia_keepalive_client.py` 成功初始化 COM + UIA 事件 + WinEvent 钩子，进入戳探循环 |
| **验证命令** | `python p1_uia_keepalive_client.py`（运行 15-30s 后 Ctrl+C） |
| **通过标准** | 输出 "IUIAutomation 创建成功"、"已注册: UIA事件=1, WinEvent=N" |

### T-07: verify_uia_recovery.py 回归验证 — P1

| 字段 | 值 |
|------|-----|
| **测试方法** | 真机执行 |
| **预期行为** | P0 验证脚本在修复后行为不退化，comtypes 方案仍能正常执行 |
| **验证命令** | `python verify_uia_recovery.py` |
| **通过标准** | comtypes 方案不报 AttributeError，输出与修复前一致 |

---

## 4. 测试汇总

| 优先级 | 总数 | 验证方式 | 说明 |
|--------|------|---------|------|
| P0（阻塞） | 5 | T-01 可 DEV 验证，T-03/T-04/T-05/T-06 需真机 | 核心修复验证 |
| P1（重要） | 2 | T-02 条件触发，T-07 需真机 | 回退路径 + 回归 |
| **合计** | **7** | | |

---

## 5. RUN 节点操作步骤

```bash
cd D:\Natsume_Ai && git pull origin codex

# T-01: 快速验证 GetModule（最重要的一步）
python -c "import comtypes.client; comtypes.client.GetModule('UIAutomationCore.dll'); from comtypes.gen.UIAutomationClient import IUIAutomation; print('OK')"

# T-03: 对比器全流程
cd scripts\runtime
python test_p1_all_strategies.py

# T-04: S2 单独测试
python test_p1_all_strategies.py --only s2

# T-05/T-06: 单脚本测试（各运行 15-30s 后 Ctrl+C）
python p1_uia_event_listener.py
python p1_uia_keepalive_client.py

# T-07: P0 回归
python verify_uia_recovery.py
```

---

## 6. 备注

- 本次修复只解决**脚本自举**层面的问题（COM 类型库加载 + Narrator 启动路径统一）
- 微信 UIA 树是否真正恢复（`child_count > 2` + 找到关键控件）取决于 P1 策略本身的效果，需要脚本自举成功后才能评估
- 如果修复后三策略全部成功启动但 UIA 树仍只有 0-1 个子元素，说明需要评估非 UIA 路径（OCR/图像识别/WinAPI Hook）
