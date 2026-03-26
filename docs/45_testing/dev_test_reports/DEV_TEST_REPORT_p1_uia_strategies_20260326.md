受众：CODEX
标题：开发实测报告 — P1 三方案 UIA 恢复策略实现与对比测试

# 开发实测报告：P1 三方案 UIA 恢复策略

| 字段 | 值 |
|------|-----|
| **功能名称** | P1 三方案 UIA 恢复策略（事件监听 / 屏幕阅读器 / 保活客户端） |
| **开发节点** | v_wenloliu workstation |
| **报告日期** | 2026-03-26 |
| **报告版本** | v1 |
| **关联 Commit** | 待提交 |
| **关联分支** | codex |
| **状态** | 🔵 OPEN（待真机验证） |

---

## 1. 变更摘要

基于 P0 方案验证结果（三种 UIA 客户端冒充方案在 RUN 节点均 failed），实现 P1 阶段的三种更强恢复策略。P0 的结论是"验证脚本本身没问题，但微信 4.x 的 UIA Provider 未完整暴露"。P1 方案的核心思路是**模拟持久化的辅助技术客户端存在**，迫使 Qt 的 QWindowsUiaAccessibility 插件保持活跃。

三种方案各有侧重：
- **S1（事件监听）**：最轻量，通过 COM 事件注册建立持久连接
- **S2（屏幕阅读器）**：利用成熟的 NVDA/Narrator，最接近真实辅助技术环境
- **S3（保活客户端）**：最激进，主动戳探 + 被动监听 + WinEvent + 缓存四重策略

同时提供了一个整合对比测试脚本，自动运行三种方案并择优推荐。

---

## 2. 修改文件清单

| # | 文件路径 | 改动类型 | 改动说明 |
|---|---------|---------|---------|
| 1 | `scripts/runtime/p1_uia_event_listener.py` | 新增 | S1: 持久化 UIA 事件监听器（StructureChanged + FocusChanged + PropertyChanged） |
| 2 | `scripts/runtime/p1_nvda_assisted_uia.py` | 新增 | S2: NVDA/Narrator 屏幕阅读器辅助恢复（自动检测、启动、多轮检查） |
| 3 | `scripts/runtime/p1_uia_keepalive_client.py` | 新增 | S3: Minimal Accessibility Client（主动戳探 + UIA事件 + WinEvent + 缓存） |
| 4 | `scripts/runtime/test_p1_all_strategies.py` | 新增 | 三方案对比测试脚本（自动运行、评分、择优推荐） |
| 5 | `docs/45_testing/dev_test_reports/DEV_TEST_REPORT_p1_uia_strategies_20260326.md` | 新增 | 本报告 |

---

## 3. 本机已验证项

| # | 验证项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | Python 语法检查 (py_compile) | ✅ PASS | 4 个脚本均可编译 |
| 2 | 代码逻辑走查 | ✅ PASS | 窗口发现逻辑与 v2 验证脚本一致（多类名 + 标题兜底），编码兼容（UTF-8 wrapper），无 emoji |
| 3 | 接口设计审查 | ✅ PASS | 每个方案都输出 JSON 状态报告，测试脚本输出统一格式的对比结果 |
| 4 | 文档门禁 | ✅ PASS | 所有脚本首两行 `受众：CODEX` + `标题：...` 符合规范 |

---

## 4. 真机实测清单

> 以下测试项需要在**运行节点**执行。核心目标：确定三种 P1 方案中是否有能恢复微信 UIA 树的。

### T-01: 依赖确认 — P0

**前置条件**：
- Python 3.8+
- `comtypes`、`pywinauto`、`uiautomation` 已安装

**测试步骤**：
1. `cd D:\Natsume_Ai`
2. `git pull origin codex`
3. `python -c "import comtypes; import pywinauto; import uiautomation; print('OK')"`

**预期结果**：
- 输出 `OK`

---

### T-02: 对比测试脚本一键运行 — P0

**前置条件**：
- T-01 通过
- 微信已启动，处于聊天主界面（至少一个聊天窗口打开）

**测试步骤**：
1. `cd D:\Natsume_Ai\scripts\runtime`
2. `python test_p1_all_strategies.py`
3. 观察三种方案的测试输出

**预期结果**：
- 三种方案依次执行（S1 → S2 → S3）
- 每种方案都输出子元素数和目标控件搜索结果
- 最终输出汇总对比表和推荐方案
- 生成 `p1_comparison_result.json`

**验证命令**：
```bash
python test_p1_all_strategies.py 2>&1 | findstr "[OK]\|[FAIL]\|[PARTIAL]\|推荐"
```

---

### T-03: S1 方案单独验证 — P0

**前置条件**：
- 微信在聊天主界面

**测试步骤**：
1. 终端 1：`python p1_uia_event_listener.py`
2. 等待 10 秒
3. 终端 2：`python verify_uia_recovery.py`
4. 对比终端 1 启动前后的子元素数变化

**预期结果**：
- 事件监听器成功注册（至少 StructureChanged）
- 心跳输出中的子元素数是否有变化
- verify_uia_recovery.py 的结果是否优于无监听器时

**关键断言**：
- `child_count` 在监听器运行期间是否 > 基线值

---

### T-04: S2 方案单独验证 — P0

**前置条件**：
- 微信在聊天主界面
- 如果 RUN 节点有 NVDA 安装更好，没有也可用 Narrator

**测试步骤**：
1. `python p1_nvda_assisted_uia.py --wait 30`
2. 观察屏幕阅读器启动后的 UIA 树变化

**预期结果**：
- 自动检测并启动 NVDA 或 Narrator
- 5 轮检查中子元素数是否有变化
- 生成 `p1_nvda_assisted_status.json`

**验证命令**：
```bash
python -c "import json; d=json.load(open('p1_nvda_assisted_status.json')); print(d['overall_result'], d['final_children'])"
```

---

### T-05: S3 方案单独验证 — P0

**前置条件**：
- 微信在聊天主界面

**测试步骤**：
1. `python p1_uia_keepalive_client.py`
2. 观察主动戳探的输出
3. 等待至少 30 秒后按 Ctrl+C 查看最终报告

**预期结果**：
- UIA 事件、WinEvent 钩子成功注册
- 主动戳探循环正常运行
- 观察子元素数是否随戳探次数增加而增长
- 生成 `p1_keepalive_status.json`

---

### T-06: 快速模式对比 — P1

**测试步骤**：
1. `python test_p1_all_strategies.py --quick`
2. 检查是否能在更短时间内完成对比

**预期结果**：
- 总时长不超过 60 秒
- 结果格式与标准模式一致

---

### T-07: 单方案模式 — P1

**测试步骤**：
1. `python test_p1_all_strategies.py --only s3`
2. 确认只运行了 S3 方案

**预期结果**：
- 只有 S3 的输出，没有 S1 和 S2
- 汇总表中只有一行

---

### T-08: 保活持久性（S1 或 S3 长时间运行后验证） — P2

**前置条件**：
- S1 或 S3 方案在子元素数上有改善

**测试步骤**：
1. 启动效果最好的方案
2. 在另一个终端反复运行 `python verify_uia_recovery.py`（间隔 1 分钟，共 3 次）
3. 观察子元素数是否稳定

**预期结果**：
- 子元素数在保活客户端运行期间保持稳定
- 不会出现回退

---

## 5. 实测统计

| 优先级 | 总数 | 说明 |
|--------|------|------|
| P0（阻塞） | 5 | T-01 ~ T-05，必须全部执行 |
| P1（重要） | 2 | T-06 ~ T-07，应通过 |
| P2（增强） | 1 | T-08，保活持久性 |

---

## 6. 已知限制与风险

| # | 描述 | 影响范围 | 缓解措施 |
|---|------|---------|---------|
| 1 | DEV 节点无微信运行环境，无法端到端验证 | 所有方案 | 完全依赖 RUN 节点真机验证 |
| 2 | S2 方案依赖 NVDA 安装，RUN 节点可能没有 | S2 | 自动回退到 Narrator；建议 RUN 节点安装 NVDA |
| 3 | WinEvent 钩子需要消息泵（S3），在计划任务环境可能受限 | S3 | 已内置消息泵处理；建议在交互式会话中运行 |
| 4 | 如果三种方案都失败，说明微信 4.x 的"按需暴露"机制可能需要更底层的方案 | 整体策略 | 备选方案：OCR/图像识别、Windows Hook 注入 |
| 5 | 之前短时拉起 Narrator 未成功（T-04 反馈报告），S2 方案加长了等待时间但效果待验证 | S2 | 默认等待 15s，支持 --wait 自定义 |

---

## 7. 回滚方案

所有文件均为新增，回滚直接删除即可：

```bash
git rm scripts/runtime/p1_uia_event_listener.py
git rm scripts/runtime/p1_nvda_assisted_uia.py
git rm scripts/runtime/p1_uia_keepalive_client.py
git rm scripts/runtime/test_p1_all_strategies.py
git rm docs/45_testing/dev_test_reports/DEV_TEST_REPORT_p1_uia_strategies_20260326.md
git commit -m "revert: remove P1 UIA recovery strategies"
```

---

## 8. 方案对比设计

```
  方案      触发机制                侵入性    依赖
  ──────────────────────────────────────────────────
  S1        UIA 事件订阅           低        comtypes
  S2        外部屏幕阅读器         中        NVDA/Narrator
  S3        主动戳探+事件+WinEvent  高        comtypes

  测试流程：
  ┌─────────────────────────────────────────────────┐
  │ test_p1_all_strategies.py                        │
  │                                                  │
  │  基线检查 ──► S1 测试 ──► 冷却 ──► S2 测试 ──►  │
  │  冷却 ──► S3 测试 ──► 汇总对比 ──► 推荐方案     │
  │                                                  │
  │  每种方案：                                       │
  │    启动 → 等待 → 多轮检查 → 评分 → 停止          │
  └─────────────────────────────────────────────────┘
```

---

## 9. 建议运行顺序

1. **首选**：直接运行 `python test_p1_all_strategies.py`（一键对比）
2. **如果对比脚本有问题**：按 S1 → S2 → S3 顺序单独运行
3. **如果某个方案有效**：用该方案的独立脚本做长时间保活测试（T-08）
4. **如果都无效**：在反馈报告中记录三种方案的具体数据，评估非 UIA 替代方案
