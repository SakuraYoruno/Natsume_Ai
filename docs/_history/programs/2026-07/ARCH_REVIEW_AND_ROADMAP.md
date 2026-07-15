受众：MASTER
标题：Natsume_Ai 架构基线（v5.0，代码优先）

# Natsume_Ai 架构基线（v5.0，代码优先）

> 历史快照：本文已退出当前权威链，仅用于追溯 2026 年 5 月的架构判断。当前状态见 `../../../50_project/arch/CURRENT_CODE_ARCHITECTURE.md`。

**基准时间**：2026-05-30  
**更新目标**：从代码、测试和发布清单生成当前事实快照；不复写旧文档叙事。

## 0. 先说结论

按当前仓库快照，项目属于**大规模工程（58.6 万级文本行）**，但**还未到 100 万级**。核心代码结构已从“单体主链文件”转为可分层路由和适配层模式，不能再按老文档里的文件体量判断系统状态。

## 1. 代码体量快照（可复核）

- Git 追踪文件数：**2716**
- Git 追踪文本行总数：**586,152**
- 按后缀汇总（文本类）  
  - Python：**454,078 行 / 1,584 文件**
  - JavaScript：**32,397 行 / 274 文件**
  - TypeScript：**0 行 / 0 文件**
  - Markdown：**66,841 行 / 360 文件**
  - JSON：**16,208 行 / 317 文件**
  - YAML：**1,151 行 / 13 文件**
  - TOML：**45 行 / 2 文件**
- 目录聚合（排除 `.venv` / `node_modules`）  
  - `host_brain`：**928 文件 / 281,325 行**
  - `tests`：**1,024 文件 / 137,039 行**
  - `tools`：**175 文件 / 41,517 行**
  - `configs`：**25 文件 / 2,290 行**
  - `docs`：**361 文件 / 68,630 行**
  - 其余：**187 文件 / 55,346 行**

### 证据命令

- `git ls-files | Measure-Object`
- 文件行数统计：对文本文件执行 `Get-Content | Measure-Object -Line`（跳过 `.venv`、`node_modules`、明显二进制）
- 目录聚合采用同口径文件枚举

## 2. 关键代码路径（替代旧口径）

旧档案里提到的“主链体量”不再成立，当前结构是拆分式入口。

- `host_brain/persona/surface_main_chain.py`：**184 行**
- `host_brain/app/obs_routes.py`：**60 行**  
  （实际观测路由在 `host_brain/app/obs_routes_*.py` 分层）
- `host_brain/app/surface_main_chain_adapter.py`：**2053 行**
- `host_brain/app/obs_routes_core.py`：**3180 行**
- `tests/unit/test_surface_main_chain.py`：**133 行**
- `tests/unit/test_surface_main_chain_app_adapter.py`：**1416 行**

### 对应文件清单（入口确认）

- `host_brain/app/obs_routes.py`
- `host_brain/app/obs_routes_core.py`
- `host_brain/app/obs_routes_health.py`
- `host_brain/app/obs_routes_trace.py`
- `host_brain/app/surface_main_chain_adapter.py`
- `host_brain/persona/surface_main_chain.py`
- `tests/unit/test_surface_main_chain.py`
- `tests/unit/test_surface_main_chain_app_adapter.py`

## 3. 发布与状态基线（只看可验证事实）

`configs/release` 当前扫描到里程碑文件 `m12` 到 `m29` 共 **17** 个（排除 `release_rollback_map.json` 等非里程碑配置）。

- `configs/release/m*.json` 中发现状态标记总数：**109**
  - `DONE`：**100**
  - `CANCELLED`：**1**（`M26-D`）
  - `functional`：**4**
  - `implemented`：**4**
- 其中 `m28_release.json` 使用 `tasks_completed` 清单（**19 项**）且不直接带逐项 `status` 字段。
- `pytest --collect-only -q` 当前可收集测试：**6299**（仅测试收集层面）

### 里程碑结构示意（最小核对）

- M12~M16、M21~M25、M27、M29：常规 `tasks` 列表，`DONE` 覆盖为主
- M17：`tasks` 字段包含 `DONE` + route `status=functional`
- M18：`tasks` 字段包含 `DONE` + endpoint `status=implemented`
- M26：`DONE` + 一项 `CANCELLED`
- M28：`tasks_completed` 数组（19 项）不含逐项状态字段

## 4. 质量负担与维护风险（代码指标）

- 标记词计数（`TODO`/`FIXME`/`HACK`/`XXX`）：
  - `TODO`：**156**
  - `FIXME`：**2**
  - `HACK`：**2**
  - `XXX`：**7**
- 这些数值为“当前仓库文本扫描”结果，不代表阻塞性问题，但说明文档和关键链路仍有未完成收口项。

## 5. 我们下一步只写在事实层面要做什么

### P0
- 建立“真实验收闭环”：
  - 每次 Host/Edge/WebUI 关键验收后，更新一次本文件的“基线指标段”
  - 把验收通过时间作为唯一入口事实版本（优先于 PR 描述）

### P1
- 发布配置一致性：
  - 维持 `configs/release` 的结构一致性，新增里程碑后补齐 `tasks`/`status` 说明，或在 `tasks_completed` 场景下补说明字段
- 关键 TODO 治理：
  - 先处理 `host_brain/` 与 `tests/` 的新建/变更文件，减少未归档的 `TODO` 漂移

### P2
- 文档更新触发条件落地（固定）：
  - 任何 release 配置文件变更（包括 `status`）
  - `pytest --collect-only -q` 测试数量变化
- 任何 TODO/HACK/FIXME 计数变化超过 **±10** 时

## 6. 当前版本标识

- 本文为：**ARCH_REVIEW_AND_ROADMAP v5.0（代码先行重写）**
- 与旧版本冲突时，以本文件与源码/配置实测命令为准
