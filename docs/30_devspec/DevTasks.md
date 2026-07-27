受众：CODEX
标题：当前架构程序注册表

# 当前架构程序注册表

版本：v4.1
维护日期：2026-07-27
状态：`ONE_ACTIVE_PROGRAM`

## 唯一活动程序

- 目标：`../50_project/arch/TARGET_ARCHITECTURE.md`
- 当前事实：`../50_project/arch/CURRENT_CODE_ARCHITECTURE.md`
- 差距：`../50_project/arch/ARCHITECTURE_GAP_MATRIX.md`
- 执行合同：`NEXT_GENERATION_EVOLUTION_EXECUTION.md`
- 机器状态：`../../configs/release/current_architecture_status.json`

当前执行波为 NE-00～NE-07。NE 是端到端 Architecture Wave，不拆回逐个 TASK Seal。

当前活动实现波为 `NE-02`。NE-01 已达到
`CODE_COMPLETE_LOCAL / ACCEPTANCE_PENDING_NE07`，其目标部署 promotion、
真实网络、附属服务和回滚仍由 NE-07 统一验收；该口径不是把 NE-01 标成
production `COMPLETED`。

NE-02 当前已补齐 D20c 的生产 transport identity 边界：真实 provider adapter
生成脱敏 endpoint digest、调用时延和外部 HTTPS 决策，canonical audit 与
Research attestation 必须精确一致；本地、私网、CLI、未知 transport 及历史
无 identity receipt 均不可计收益。真实凭据、24 项 Research cohort、Code
20 项和 Pentest 30 次验收尚未执行，因此本波仍为 `IN_PROGRESS /
ACCEPTANCE_PENDING_EXTERNAL`。

Research 24 项验收协议的纯合同与确定性 evaluator 已落地：固定 cohort
规模/主题/来源覆盖，hidden verifier 独立 execution 与 response binding，
10,000 次 paired bootstrap，以及质量、citation、严重错误、含 verifier 成本
和 p95 时延的不可放宽门槛均有定向测试。下一步是在本波统一 next-free
migration 中建立 append-only cohort owner，再运行真实双臂；当前不得把
`proposal_eligible` 的可计算性写成已获得真实收益。

Pentest L3 已把固定根全集改为两层 catalog：当前 ToolContract/PreActionGate
通过的动作只作为 executable roots；同一 server policy 生成但缺 capability
的已登记动作只作为 virtual successors，待纯 transition 的新 capability
满足后才可展开。successor catalog digest/count 已进入 durable shadow
calibration，生产选择仍强制使用 L1/L2 baseline。下一步仍是 30 次授权实验、
receipt-bound outcome、安全零违规统计和 proposal Gate。

## 历史编号

- TASK-0000～TASK-0622、M48～M57、AW-00～08、GR-00～06 已冻结。
- 原文位于 `../_history/programs/2026-07/` 或 Git 历史。
- 历史编号仍可用于搜索，不得决定当前完成度、优先级或 Goal。

## 记录规则

1. 小工作片只写 PR checklist 和定向测试，不扩张本文件。
2. Architecture Wave 状态写在唯一执行合同和机器状态中。
3. 一个波只有 production cutover、旧 authority 删除和 wave integration 同时成立才可完成。
4. 提交信息与 PR 全部使用中文。
