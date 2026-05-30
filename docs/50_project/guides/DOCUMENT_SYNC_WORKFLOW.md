受众：PROJECT
标题：文档过期治理工作流（与代码同步）

版本：v1.0
日期：2026-05-30

这是一版「项目级文档维持」工作流，目标是把关键事实文档与代码、测试、发布数据保持可验证同步，避免“文档留在旧世界”。

## 1. 你需要先知道什么

我们把文档变更分成两个等级：

1) **事实文档（每次代码迭代都要同步）**
   - `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md`
   - `docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md`
   - `docs/50_project/arch/dataflow_report.md`
   - `docs/50_project/arch/DIGITAL_LIFE_BODY_FRONTEND_ARCHITECTURE_20260419.md`
   - `docs/60_trace/requirements_trace.md`
   - `docs/00_index/00_DOCMAP.md`

2) **静态历史文档（非现实主链，不要求实时联动）**
   - 版本归档、阶段汇总、回溯总结、试运行记录。

> 规则：凡是“事实文档”，代码、测试或配置变化后，都必须尽快更新；历史归档文档不进入本门禁。

## 2. 门禁定义（脚本与配置）

### 2.1 配置文件

文档过期门禁由配置驱动，不用人脑背：
- `tools/doc_lint/doc_freshness_policy.json`

该配置明确了：
- 每份目标文档（doc）
- 对应的源码/配置/测试源（sources）
- 允许的最大差异天数（max_age_days）

### 2.2 检查脚本

- `tools/doc_lint/doc_freshness_gate.py`

规则核心：只要某份文档的源文件发生更新，且文档最近提交时间与源最新提交时间差超过阈值，CI 认为该文档已过期。

### 2.3 CI 触发

- `workflow` 文件：`\.github/workflows/doc-freshness-gate.yml`
- 触发：`pull_request`、`push(main)`、`workflow_dispatch`、`schedule`
- 运行策略：
  - PR / push：只校验“本次变更触碰到源文件”的文档。
  - schedule：全量扫描（默认 14 天阈值）用于追踪长期积压。

## 3. 我怎么更新文档

当脚本报“过期”时，按以下顺序处理：

1. 先补齐**事实文档**中对应章节（新增/修订/重排）；
2. 把变更映射到 `TRACE` 或 `DOCMAP`；
3. 在本次提交里连带更新版本注记（版本号按 Project 约定）；
4. 重新运行文档门禁确保过期项清零。

更新粒度要求：以“事实性句段”为主，不需要重写整篇，删减历史推演章节也可以，前提是文档不与实际代码冲突。

## 4. 失败处理策略

- **一次变更触发 1–3 个事实文档过期**：同步更新这 1–3 个文档即可；
- **变更波及多份事实文档**：按 `sources` 映射批量更新，不得只改一句“目前同步中”；
- **无法及时更新**：在 PR 描述中说明延后计划并开一个补齐任务（不得把过期文档作为正式交付口径）。

## 5. 常见例外

- 归档文件、旧阶段总结、回顾类文档不在本策略内；
- 仅改中文本提示语、参数注释、拼写不影响事实路径时，可不触发强制更新；
- 当上游源文件是外部依赖且仓库不直接改造时，不列入“必须更新”条目，待对应里程碑评审补齐。

## 6. 结果输出与治理

门禁输出字段：
- `failed_artifact`：过期文档路径；
- `message`：超期天数与阈值；
- `latest_source`：触发源文件；
- `changed_source_count`：命中变更的源数量；
- `check_all`：当前模式（是否全量扫描）。

### 6.1 完成定义

一个 PR 视为完成，需满足：
- 受影响文档与配置映射一致；
- `doc-freshness-gate` 无失败；
- `docs` 变更不再向历史事实文档写入不再成立的路径引用。
