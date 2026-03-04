受众：CODEX
标题：监控面板 UI 升级变更记录

# 监控面板 UI 升级变更记录

> 文件路径：`host_brain/app/static/panel.html`（v1/v2）→ `host_brain/app/static/`（v3 多文件）
> 文档维护：自动留痕，每次 UI 重大升级后更新

---

## v3.0 — 2026-03-04（M17 + M18，已完成）

### 变更概述

将现有单页 `panel.html` 重建为专业运维监控 SPA，参考 **Dify 简约白色风格**（非深色主题），实现双端全服务监控 + Dify 风格节点链路可视化展示。

**完成状态**：M17（前端重建）+ M18（后端 API 扩展）全部 DONE；`npm run build` 构建成功；1577 全量测试通过（2 skipped，0 failed）。

### 核心技术栈约定（v3.0）

> 为确保 UI 质量对标 Dify，并保证链路图（Trace）和复杂图表的可维护性，v3.0 全面引入现代化框架，不再使用原生 JS 强撸。

#### 核心基座

| 技术 | 版本 | 用途 |
|------|------|------|
| **React** | 18.x | 组件化开发，所有页面均为 React 组件 |
| **Vite** | 5.x | 快速构建，`npm run build` 产出静态文件由 FastAPI StaticFiles 托管 |
| **Tailwind CSS** | 3.x | 原子化样式，实现 Dify 简约白色风格；**严格禁用大面积高饱和度渐变与廉价发光阴影** |
| **React Router** | 6.x | 4 个核心页面的 History/Hash 路由切换 |

#### 核心业务库

| 库 | 用途 | 说明 |
|----|------|------|
| **React Flow** | Trace 节点流图 | Dify Workflow 同款底层库，原生支持节点卡片、有向连线、自定义节点、分支/并行布局 |
| **Apache ECharts** | Metrics 图表 | 时序折线图、柱状图、条形图，性能优秀，抗锯齿效果佳 |
| **Zustand** | 全局状态管理 | 管理 30s 自动轮询状态、全局时间范围过滤；轻量，无 Redux 样板代码 |
| **Lucide React** | 图标库 | 统一专业的纯色 SVG 图标；**禁止使用 Emoji 作为图标** |

#### 构建与部署约定

- 源码目录：`host_brain/app/monitor_ui/`（React 项目根目录，含 `package.json` / `vite.config.js`）
- 构建产出：`host_brain/app/static/monitor/`（FastAPI StaticFiles 托管，入口为 `index.html`）
- 开发模式：`npm run dev`（Vite dev server，代理 `/obs/*` 到 FastAPI）
- 生产构建：`npm run build`（产出物提交到仓库，无需 Node.js 运行时）

---

### 架构变更

| 项目 | v2.x | v3.0 |
|------|------|------|
| 文件结构 | 单文件 panel.html + panel.css + panel.js | **React + Vite SPA**（源码 `monitor_ui/`，构建产出 `static/monitor/`） |
| 主题风格 | 深灰色（Grafana 风格） | **白色/浅灰（Dify 简约风格）** |
| 主色调 | `#4a9eff` 蓝 | `#2970FF` 蓝（Dify 蓝） |
| 页面数量 | 1 个页面 | **4 个页面**（Overview / Sessions / Trace / Metrics） |
| 路由 | 无 | React Router（/overview / /sessions / /trace / /metrics） |
| 链路展示 | 时间线列表 | **React Flow 节点流图**（Dify 风格，含入参/出参侧边面板） |
| 服务监控 | 无 | **双端全服务健康卡片**（≥12 个服务） |
| 图表引擎 | 内联 Canvas | **Apache ECharts**（时序/分布/延迟图表） |
| 状态管理 | 无 | **Zustand**（轮询状态 + 全局时间范围过滤） |
| 图标 | Emoji / 文字 | **Lucide React**（统一 SVG 图标） |

### 新增页面

#### Overview（全服务健康总览）
- 双端所有服务状态卡片网格（ASR/TTS/VTS/LLM/Neo4j/SQLite/Chroma/Scheduler 等）
- 顶部汇总统计行（总服务数/健康/降级/故障）
- 30 秒自动轮询 `/obs/services/health`

#### Sessions（会话列表）
- 历史会话表格（request_id / 时间 / 状态 / 耗时 / 通道）
- 过滤栏（时间范围 + 状态 + 关键词）
- 分页控件，点击行跳转 Trace 页

#### Trace（链路可视化）
- **Dify 风格节点流图**：节点卡片 + 有向连线 + 分支/并行支持
- 节点点击侧边面板：input/output JSON 格式化展示（折叠/展开）
- 节点状态颜色：成功（绿）/ 拒绝（红）/ 降级（橙）/ 运行中（蓝动画）

#### Metrics（指标大盘）
- 请求量趋势折线图
- 拒绝率趋势折线图
- reason_code 分布横向条形图
- 各服务响应延迟 P50/P95 柱状图
- 时间范围选择器（1h / 6h / 24h / 7d）

### 新增后端 API（M18）

| 端点 | 用途 |
|------|------|
| `GET /obs/services/health` | 聚合双端所有服务健康状态 |
| `GET /obs/sessions` | 分页会话列表 + 过滤 |
| `GET /obs/trace/{request_id}/nodes` | 结构化节点列表（含入参/出参） |
| `GET /obs/metrics/timeseries` | 多维度时序数据 |

### 文件变更（规划）

#### 前端（React + Vite 项目）

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `host_brain/app/monitor_ui/package.json` | 新建 | React 18 + Vite + Tailwind + React Flow + ECharts + Zustand + Lucide React 依赖声明 |
| `host_brain/app/monitor_ui/vite.config.js` | 新建 | Vite 构建配置，产出到 `../static/monitor/` |
| `host_brain/app/monitor_ui/tailwind.config.js` | 新建 | Tailwind 配置，含 Dify 设计 token（主色 `#2970FF`） |
| `host_brain/app/monitor_ui/src/main.jsx` | 新建 | React 应用入口 |
| `host_brain/app/monitor_ui/src/App.jsx` | 新建 | 根组件 + React Router 路由配置 |
| `host_brain/app/monitor_ui/src/store/` | 新建 | Zustand store（轮询状态 + 时间范围过滤） |
| `host_brain/app/monitor_ui/src/pages/Overview.jsx` | 新建 | Overview 页面组件 |
| `host_brain/app/monitor_ui/src/pages/Sessions.jsx` | 新建 | Sessions 页面组件 |
| `host_brain/app/monitor_ui/src/pages/Trace.jsx` | 新建 | Trace 页面组件（含 React Flow 节点图） |
| `host_brain/app/monitor_ui/src/pages/Metrics.jsx` | 新建 | Metrics 页面组件（含 ECharts 图表） |
| `host_brain/app/monitor_ui/src/components/` | 新建 | 公共组件（ServiceCard / NodePanel / StatusBadge 等） |
| `host_brain/app/monitor_ui/src/api/` | 新建 | API 请求层（obs.js，封装所有 `/obs/*` 请求） |
| `host_brain/app/static/monitor/` | 构建产出 | Vite build 产出，FastAPI StaticFiles 托管 |

#### 后端（Python）

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `host_brain/obs/service_health_aggregator.py` | 新建 | 服务健康聚合器 |
| `host_brain/obs/session_query.py` | 新建 | 会话查询服务 |
| `host_brain/obs/trace_node_builder.py` | 新建 | 链路节点构建器 |
| `host_brain/obs/metrics_timeseries.py` | 新建 | 指标时序服务 |

### 里程碑关联

- M17（TASK-0182~0187）：前端重建
- M18（TASK-0188~0192）：后端 API 扩展

---

## v2.0 — 2026-03-04（全面重构）

### 变更概述

对监控面板 `panel.html` 进行全面重构，从原始的静态单次加载页面升级为具备自动刷新、实时趋势图、最近请求列表的现代化监控 Dashboard。

### 新增功能

#### A. 自动轮询刷新
| 项目 | 说明 |
|------|------|
| 刷新间隔 | 30 秒自动刷新一次 |
| 倒计时徽章 | 右上角实时显示剩余秒数，≤5s 时变黄色警示 |
| 开关控件 | Toggle 开关可随时暂停 / 恢复自动刷新 |
| 立即刷新 | 「↻ 立即刷新」按钮手动触发，同时重置倒计时 |
| 上次刷新时间 | 右上角显示 `上次刷新 HH:MM:SS` |
| 并发刷新 | 每次刷新同时调用 `loadMetrics()` + `loadRecentRequests()` |

#### B. 最近请求列表
| 项目 | 说明 |
|------|------|
| 数据来源 | 调用 `/ui/search-requests`，拉取最近 24h 内 20 条请求 |
| 展示字段 | `request_id`（截断显示）、时间、状态徽章 |
| 状态徽章 | ✓ OK（绿）/ ✗ 拒绝（红）/ ⚠ 错误（橙）/ —（灰） |
| 点击交互 | 点击任意条目直接查询该请求的链路时间线，同步高亮选中项 |
| 自动同步 | 随 30s 自动刷新同步更新列表 |

### 优化项

#### 统计卡片升级（5 个）
| 卡片 | 变更 |
|------|------|
| 总请求 | 新增副标题（Chat 占比 %） |
| Chat 请求 | 新增副标题（占比 %） |
| 拒绝次数 | 新增副标题（有无拒绝事件提示） |
| **拒绝率 %**（新增） | 计算公式：`rejections / total × 100`，>10% 显示 ⚠️ 偏高 |
| 运行时长 | 格式化显示：`3661s` → `1h 1m 1s`，副标题保留原始秒数 |

每张卡片顶部新增彩色渐变条（紫 / 绿 / 红 / 橙 / 黄），hover 时轻微上浮。

#### Canvas 趋势折线图（新增）
- 保留最近 **10 次采样点**，超出后滚动丢弃最旧数据
- 绘制三条折线：总请求（紫）/ Chat（绿）/ 拒绝（红）
- 每条折线下方有半透明填充区域
- 自动计算 Y 轴范围，绘制 4 条网格线 + Y 轴标签
- X 轴显示首、中、尾三个时间标签
- 窗口 resize 时自动重绘，支持 HiDPI（devicePixelRatio）

#### 视觉 / 交互优化
| 项目 | 说明 |
|------|------|
| 顶部导航栏 | sticky 定位，backdrop-filter 毛玻璃效果 |
| 卡片 hover | border-color 渐变高亮 |
| 加载动画 | CSS spinner 动画，替代原来的纯文字 |
| 滚动条美化 | 时间线区域自定义细滚动条 |
| 清空按钮 | 一键清空时间线、搜索框、Source 过滤器 |
| 回车搜索 | 搜索框支持 Enter 键直接触发查询 |
| 渐入动画 | 列表刷新时 `fade-in` 动画 |
| 响应式 | 1100px 以下 5 列变 3 列，768px 以下单列布局 |

### 文件变更

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `host_brain/app/static/panel.html` | 全量重写 | v1.x → v2.0，约 1248 行 |

### 依赖接口

| 接口 | 用途 |
|------|------|
| `GET /metrics` | 获取统计指标（总请求、Chat、拒绝、运行时长、reason_code 分布） |
| `GET /ui/search-requests` | 获取最近请求列表（最近 24h，page_size=20） |
| `POST /observability/trace` | 查询指定 request_id 的链路时间线 |

---

## v1.x — 历史版本（已废弃）

### v1.0 初始版本特性
- 深色主题，CSS 变量体系
- 响应式布局（768px 断点）
- 4 个统计卡片（总请求 / Chat / 拒绝 / 运行时长）
- 请求链路时间线（按 source 过滤）
- reason_code 分布横向条形图（纯 CSS div 模拟）
- Source 分布横向条形图
- 空状态 / 加载 / 错误提示
- **无自动刷新**，指标仅在页面加载时拉取一次
- **无最近请求列表**，需手动输入 request_id
- **无趋势图**，看不出时间维度变化
- 运行时长直接显示原始秒数（如 `3600`）
