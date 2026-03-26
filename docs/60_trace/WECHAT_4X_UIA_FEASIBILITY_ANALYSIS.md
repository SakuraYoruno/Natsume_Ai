受众：MASTER、实现层
标题：微信 4.x UIAutomation 可行性分析

# 微信 4.x PC 版 UIAutomation 自动化方案 — 可行性分析报告

> 分析人：七海 | 日期：2026-03-25 | 版本：v1.0

---

## 一、执行摘要

**结论：UIAutomation 方案对微信 4.x 可行，但需要注意版本差异和技术细节。**

微信 4.0 从 Duilib 框架迁移到 QT+C++ 原生跨平台架构后，UI 自动化的生态经历了一波"断裂→恢复→再断裂→再恢复"的过程。截至 2026 年 3 月，社区已有多个成熟的开源项目（pywechat、wx4、wechat-automation-api 等）验证了 UIAutomation 方案在微信 4.x 上的可行性，并在生产环境中实际运行。

但有一个关键变量：**微信 4.1.5+ 版本采用了"按需暴露"UI 树的策略**，需要特殊处理才能获取完整控件结构。

---

## 二、技术背景

### 2.1 微信 4.0 的架构变化

| 维度 | 旧版（3.x） | 新版（4.0+） |
|------|------------|-------------|
| UI 框架 | Duilib (Win32 原生) | QT 5.15.14 (跨平台) |
| 控件体系 | 标准 Win32 控件，有 Name | 自研 `mmui::*` 控件，无 objectName |
| 编程范式 | 面向过程 | 面向对象 + PPL 并行任务 |
| 数据库 | SQLite | WCDB（密钥使用后清理） |
| 逆向难度 | 低（Hook 注入直接可用） | 高（封装层级深，反逆向加固） |

**来源**：看雪安全社区逆向分析帖 (2025-08-09)

### 2.2 UI 自动化的技术路线演变

```
微信 3.x       → pyautogui / pywinauto / uiautomation 均可用 ✅
微信 4.0~4.1.4 → QT 控件暴露 mmui 类名，uiautomation 基本可用 ✅
微信 4.1.5+    → "按需暴露"策略，UI 树默认精简 ⚠️（需触发恢复）
```

---

## 三、可行性分析（三种技术路线）

### 方案 A：纯 UIAutomation（推荐 ✅）

#### 原理
通过 Windows UIAutomation / Accessibility 接口遍历微信窗口的控件树，定位聊天列表、消息气泡、输入框等元素，实现消息监听和发送。

#### 可行性证据

**1. 已验证的开源项目**

| 项目 | Stars | 技术栈 | 微信版本 | 最近更新 | 状态 |
|------|-------|--------|---------|---------|------|
| pywechat (Hello-Mr-Crab) | — | pywinauto | 4.1+ | 活跃 | ✅ 生产可用 |
| wx4 (chengcheng84) | — | uiautomation + pyautogui | 4.x (4.0.5) | 2025-08 | ✅ 可用 |
| wechat-automation-api (LAVARONG) | — | Flask + uiautomation | 4.0+ | 2026-03-12 | ✅ 生产可用 |
| SegmentFault 脚本 (TANKING) | — | uiautomation | 4.x | 2025-08 | ✅ 概念验证 |

**2. 已知可定位的 QT 控件类名**

社区逆向分析和实际使用中已确认的 mmui 控件：

```
mmui::ChatSingleWindow     — 独立聊天窗口
mmui::ChatTextItemView     — 文本消息气泡
mmui::ChatImageItemView    — 图片消息（推测）
mmui::ChatFileItemView     — 文件消息（推测）
```

定位方式：通过 `ClassName` 而非 `objectName`（QT 未设置 objectName）。

**3. 核心操作验证情况**

| 操作 | 可行性 | 实现方式 | 备注 |
|------|--------|---------|------|
| 监听新消息 | ✅ | 轮询控件树 / 红点检测 | 需要目标窗口保持打开（可最小化） |
| 读取消息内容 | ✅ | 控件 Name 属性 / OCR | 文本消息可直接读取 |
| 发送文本消息 | ✅ | 定位输入框 + SetValue / 剪贴板 | 多个项目已验证 |
| 发送图片/文件 | ✅ | 剪贴板 + Ctrl+V | wechat-automation-api 已实现 |
| 获取通讯录 | ✅ | 遍历列表控件 | pywechat 已实现 |
| 获取朋友圈 | ✅ | 模拟操作 + 控件遍历 | pywechat 已实现 |

#### 关键风险：4.1.5+ 版本的"UI 树消失"问题

**现象**：微信 4.1.5.16 起，默认只暴露极少量 UI 元素（"瘦身树"）。

**根因**：微信采用"按需暴露"的 UIAutomation Provider 策略——只有当系统检测到无障碍客户端（如屏幕阅读器）运行时，才加载完整的 UIA Provider。

**解决方案**（已验证）：

```
方法一（推荐）：编写自定义 UIAutomation Client，伪装为无障碍客户端
  → 使用 AutomationElement.FromHandle() 附着到微信窗口
  → 系统会通知微信"有无障碍客户端接入"
  → 微信加载完整 UI 树

方法二：启动 Windows 讲述人（Narrator）
  → 简单但影响用户体验

方法三：修改注册表 / 注入标记
  → 风险较高，不推荐
```

CSDN 文章（2025-11-24）提供了完整的 C# 实现代码。Python 侧可通过 `comtypes` 或 `pywinauto` 调用相同的 COM 接口。

#### 综合评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 技术可行性 | ⭐⭐⭐⭐ | 多个项目验证，有成熟方案 |
| 稳定性 | ⭐⭐⭐ | 微信更新可能破坏控件结构 |
| 封号风险 | ⭐⭐⭐⭐ | 非注入非协议，风险较低 |
| 开发效率 | ⭐⭐⭐⭐ | 可站在 pywechat/wx4 肩膀上 |
| 长期维护 | ⭐⭐⭐ | 需跟随微信版本更新适配 |

---

### 方案 B：视觉方案（OpenCV + OCR）

#### 原理
完全不依赖控件树，通过屏幕截图 + 计算机视觉定位 UI 元素，OCR 识别文本内容。

#### 可行性

| 维度 | 评分 | 说明 |
|------|------|------|
| 技术可行性 | ⭐⭐⭐⭐ | Quicker 社区有详细方案 |
| 稳定性 | ⭐⭐ | DPI 敏感、主题色/深色模式干扰 |
| 封号风险 | ⭐⭐⭐⭐⭐ | 完全无侵入 |
| 开发效率 | ⭐⭐ | 需要大量视觉算法调优 |
| 长期维护 | ⭐⭐⭐ | 界面改版需重新标注 |

#### 关键技术点
- 红点检测：HSV 色彩空间（H: 0-10/170-180, 高 S, 高 V）
- 文本识别：微信内置 OCR（wxocr.dll）或 PaddleOCR
- 屏幕捕获：BitBlt (GDI) 局部截取

#### 适用场景
作为方案 A 的**降级备选**——当微信某次更新彻底关闭 UIAutomation 支持时使用。

---

### 方案 C：协议/Hook 注入（不推荐 ❌）

#### 原理
逆向微信二进制，注入 DLL 调用内部函数（如 SendMsgTask）。

#### 不推荐理由

| 问题 | 严重程度 |
|------|---------|
| 微信 4.0 面向对象 + PPL 并行，逆向难度极高 | 🔴 |
| 数据库密钥使用后清理，无法简单提取 | 🔴 |
| 属于修改客户端行为，封号风险最高 | 🔴 |
| 每次微信更新都需要重新逆向 | 🔴 |
| 违反 Natsume_Ai 的"Governed Channel"安全原则 | 🔴 |

---

## 四、与 Natsume_Ai 架构的适配分析

### 4.1 Edge Client 的 Senses / Actuators 模式

UIAutomation 方案与 Edge Client 的现有架构天然契合：

```
┌─────────────────────────────────────────┐
│              Edge Client                │
│                                         │
│  Senses (入站)         Actuators (出站) │
│  ┌─────────────────┐  ┌───────────────┐ │
│  │ wechat_monitor  │  │ wechat_sender │ │
│  │ (UIAutomation)  │  │ (UIAutomation)│ │
│  │                 │  │               │ │
│  │ - 轮询消息列表  │  │ - 定位输入框  │ │
│  │ - 读取控件Name  │  │ - 设置文本    │ │
│  │ - 去重+归一化   │  │ - 模拟发送    │ │
│  └────────┬────────┘  └───────┬───────┘ │
│           │                   │         │
│           └──────┬────────────┘         │
│                  │ RPC                  │
│           ┌──────┴──────┐               │
│           │  wechat.py  │               │
│           │  (契约层)   │               │
│           └──────┬──────┘               │
└──────────────────┼──────────────────────┘
                   │ gRPC / HTTP
          ┌────────┴────────┐
          │   Host Brain    │
          │ channels/       │
          │ wechat.py ✅    │
          │ (已完成)        │
          └─────────────────┘
```

### 4.2 配置扩展

```python
# edge_client/config.py 需新增
WECHAT_ENABLED = True
WECHAT_POLL_INTERVAL_MS = 2000      # 消息轮询间隔
WECHAT_TARGET_CHATS = []            # 监控的聊天窗口列表
WECHAT_UI_TREE_RECOVERY = True      # 是否启用 UI 树恢复
WECHAT_SEND_INTERVAL_SEC = 1.0      # 发送间隔（防风控）
```

---

## 五、风险清单与缓解措施

| # | 风险 | 概率 | 影响 | 缓解措施 |
|---|------|------|------|---------|
| R1 | 微信更新破坏控件类名 | 中 | 高 | 控件类名抽取为配置项，版本适配层解耦 |
| R2 | 4.1.5+ UI 树不可见 | 已发生 | 高 | UI 树恢复方案（伪装无障碍客户端），已有代码 |
| R3 | 微信风控检测自动化操作 | 低 | 高 | 操作间隔随机化、模拟人类行为模式 |
| R4 | 多线程操作导致微信卡死 | 中 | 中 | 严格单线程操作，任务队列串行化 |
| R5 | DPI 缩放导致坐标偏移 | 低 | 中 | UIAutomation 基于控件树（非坐标），影响小 |
| R6 | 微信窗口被关闭/最小化 | 低 | 中 | 窗口状态监控 + 自动恢复 |
| R7 | 腾讯法律风险（TOS 违规） | 低 | 高 | 仅个人使用、非商业化、不涉及协议破解 |

---

## 六、推荐实施路径

### Phase 1：可行性验证（1-2 天）

```
目标：确认当前微信版本的 UI 树可达性
步骤：
1. 检查本机微信版本号
2. 安装 uiautomation 库
3. 运行探测脚本，打印微信窗口的完整控件树
4. 如果 UI 树为空 → 实施"UI 树恢复"方案
5. 确认关键控件类名（ChatSingleWindow / ChatTextItemView 等）
```

### Phase 2：骨架代码（2-3 天）

```
目标：wechat_monitor.py + wechat_sender.py 核心骨架
步骤：
1. 参考 pywechat/wx4 的控件定位逻辑
2. 按 Edge Senses/Actuators 模式封装
3. 实现基础消息监听（轮询 + 去重）
4. 实现基础消息发送（定位输入框 + 文本注入）
```

### Phase 3：RPC 串联（1-2 天）

```
目标：与 Host Brain 已有的 wechat.py 通道对接
步骤：
1. 定义 RPC 契约（消息格式、事件类型）
2. 入站消息 → Host 的 channel_io_log
3. Host 出站 → Edge 的 wechat_sender
4. 端到端测试
```

### Phase 4：健壮性加固（持续）

```
- 异常恢复（微信崩溃/重启自动重连）
- 消息类型扩展（图片、文件、表情）
- 治理审计（符合 Governed Channel 模式）
```

---

## 七、结论

| 维度 | 结论 |
|------|------|
| **技术可行性** | ✅ 可行。多个开源项目已在微信 4.x 上验证通过 |
| **推荐方案** | 方案 A（UIAutomation），以方案 B（视觉）作为降级备选 |
| **核心依赖** | `uiautomation` 或 `pywinauto` 库 |
| **最大风险** | 微信版本更新可能破坏控件结构（可缓解） |
| **与项目适配** | 与 Edge Client 的 Senses/Actuators 架构完美契合 |
| **不推荐** | Hook 注入方案（安全风险 + 违反项目原则） |

**七海的建议**：方案可行性已经被社区充分验证，不需要从零摸索。下一步应该做的是在哥哥的机器上跑一个最小探测脚本，确认当前微信版本的 UI 树状态，然后就可以直接进入骨架开发了。

---

## 参考资料

1. [Quicker — 微信4.0+环境下的RPA自动化技术深度研究报告](https://getquicker.net/KC/Kb/Article/1171) (2025-12-07)
2. [看雪 — 微信PC4.0的变化（逆向分析）](https://bbs.kanxue.com/thread-287950.htm) (2025-08-09)
3. [CSDN — 微信4.1 UI树"消失"问题与恢复方案](https://blog.csdn.net/WWW7530471/article/details/155169701) (2025-11-24)
4. [GitHub — pywechat（支持4.1+的Python微信RPA）](https://github.com/Hello-Mr-Crab/pywechat)
5. [GitHub — wx4（微信4.x自动化库）](https://github.com/chengcheng84/wx4)
6. [GitHub — wechat-automation-api（Flask + uiautomation HTTP服务）](https://github.com/LAVARONG/wechat-automation-api) (2026-03-12)
7. [SegmentFault — 微信4.X新版获取群消息的自动化脚本](https://segmentfault.com/a/1190000047217890) (2025-08-26)
8. [知乎 — 微信PC版4.0：新架构，新升级](https://zhuanlan.zhihu.com/p/16415378172) (2025-01-04)
