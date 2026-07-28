受众：CODEX
标题：当前架构程序注册表

# 当前架构程序注册表

版本：v4.7
维护日期：2026-07-28
状态：`ONE_ACTIVE_PROGRAM`

## 唯一活动程序

- 目标：`../50_project/arch/TARGET_ARCHITECTURE.md`
- 当前事实：`../50_project/arch/CURRENT_CODE_ARCHITECTURE.md`
- 差距：`../50_project/arch/ARCHITECTURE_GAP_MATRIX.md`
- 执行合同：`NEXT_GENERATION_EVOLUTION_EXECUTION.md`
- 机器状态：`../../configs/release/current_architecture_status.json`

当前执行波为 `NE-08 / DL-00～DL-07`。NE 是面向产品的端到端 Architecture
Wave，DL 是该波内部的连续交付面，不拆回逐个 TASK Seal。

当前活动实现波为 `NE-08_IN_PROGRESS / DL-06_CANONICAL_TARGET_REGISTRY_SLICE`：以实时
情感陪伴数字生命为唯一产品主线。DL-00 已冻结体验基线；DL-01 已完成 canonical
Affective Life Kernel、0143 迁移和 1002 个真实 SQLite journal event 的重放/重启
确定性验证；DL-02 已把 workspace-scoped AffectFusion/Appraisal 接入 Host Input，
使用户 observation 先落 journal、再可选形成七海自身 CARE/CONCERN 等 self-affect，
而非镜像用户标签。DL-03 已把 accepted reply 的 provisional relation episode、
下一次输入的 aftereffect、boundary/repair/proactive permission 和 Surface 的可信关系
约束接入同一 canonical Life journal；生产 Bond 默认关闭积分自动亲密晋级。DL-04 已将
可信 hand-off 冻结为 `AffectiveTurnPlanV1`，普通低歧义文本可使用
`ModelAttemptService.stream()` 逐句输出；每个稳定句先写 canonical Life delivery
intent，候选模型输出和纯文本 fallback 均不能改写 self-affect，高歧义/边界/修复仅
允许恰好两候 buffered selection。DL-05 已将同一冻结 `ExpressionPlanV1` 编译为 Voice
`SPEAK` 与 Avatar `TRANSITION` 的成对 Host protocol，并在结束/打断时追加
`RELEASE`/`CANCEL`；0146 只投影 Host staging，远端 Edge 尚未启动，因此不能声称播放或
Avatar 已执行。DL-06 的首个 Scheduler 切片仅在 canonical `proactive_permission=ALLOWED`、
无 repair/未结 episode 且 self regulation 未要求克制时，才以 bounded canonical
self-affect 生成无回应压力的主动消息；旧 Bond、legacy `affect` 与固定模板均无发送权。
已接受 foreground reply 现在会把 scope-bound `CompanionProactiveTargetV1` 与 provisional
episode 原子写入同一 relationship command；Scheduler 从 durable registry 按冻结 Scene
scope 确定性选择单一 aggregate，旧 `last_target` 无发送权。delivery-success 后效应对账、
重启 reconciliation 或偏好 canary/rollback 仍未完成，不得称为主动陪伴闭环。受治理预算流、
连接恢复和旧兼容 writer 清零仍由后续 DL-06～DL-07
交付。此前 NE-01～NE-07 的 Universal Runtime、搜索、
Lab、Skill、Knowledge 和服务栈工作仅保留为基础资产；它们不能证明七海具有独立
情感因果、长期关系修复或文本/语音/Avatar 同轮表达。后文关于 NE-01～NE-07
的段落是历史范围说明，不再决定当前执行顺序。

NE-02 已补齐 D20c 的生产 transport identity 边界：真实 provider adapter
生成脱敏 endpoint digest、调用时延和外部 HTTPS 决策，canonical audit 与
Research attestation 必须精确一致；本地、私网、CLI、未知 transport 及历史
无 identity receipt 均不可计收益。真实凭据、24 项 Research cohort、Code
20 项和 Pentest 30 次验收尚未执行，因此 NE-02 保持
`CODE_COMPLETE_LOCAL / ACCEPTANCE_PENDING_EXTERNAL`。

NE-03 当前实现 0137 runtime-fenced 长期目标 authority：激活/撤销只能经
Secure Ingress、Governor、TaskGraph、Review、Action Preview 和 Acceptance
Effect，随后 event/head 在同一 SQLite UoW 提交。World 只投影 active goal，
撤销以 `SOURCE_REVOKED` expiry 清除；Context/Intent 冻结 exact refs 与 typed
constraints，并在 admission 重开 current identity。现行 Operator inventory
为 `16 active / 23 retired / 39 known / blocked=0`。本地定向链和全部
`test_ne03_*` 单元/集成波次均已通过；波次结果为
`271 passed, 2 warnings in 332.52s`。部署多实例 canary、Windows graceful
stop 和 NE-07 全栈仍未运行，因此本波保持 `IN_PROGRESS`。

Research 24 项验收协议的纯合同、确定性 evaluator 与 0136 durable owner 已落地：固定 cohort
规模/主题/来源覆盖，hidden verifier 独立 execution 与 response binding，
10,000 次 paired bootstrap，以及质量、citation、严重错误、含 verifier 成本
和 p95 时延的不可放宽门槛均有定向测试。owner 会先从 0107 terminal ledger
重开每个 pair，再持久化成员 proof 和完整报告。下一步是运行真实双臂；当前不得把
`proposal_eligible` 的可计算性写成已获得真实收益。

Pentest L3 已把固定根全集改为两层 catalog：当前 ToolContract/PreActionGate
通过的动作只作为 executable roots；同一 server policy 生成但缺 capability
的已登记动作只作为 virtual successors，待纯 transition 的新 capability
满足后才可展开。successor catalog digest/count 已进入 durable shadow
calibration，生产选择仍强制使用 L1/L2 baseline。固定 30 次协议和跨域
proposal Gate 的纯合同现已落地：每次运行绑定授权环境、facts/capability/
policy 派生 successor、独立 Task/Authorization/Verification receipt 和
校准指标；Pentest 合格且 Research/Code 至少一域证明收益后也只生成强制
Review、无 activation authority 的 proposal。0136 已把 0105 calibration
与三类外部 receipt authority 绑定为统一 cohort 成员。下一步仍是 30 次真实
授权实验、真实非 Pentest 收益与 Review；当前不得升级
L3 production influence。

Code 20 项收益协议已冻结：同 source/model/provider/profile/budget、独立 fresh
worktree 与 execution；通过率提升至少 10 个百分点，或非劣 5 个百分点且成本
降低至少 15%，同时 hidden/static/diff-risk 不得退化。D19 失败 arm 现会在
cleanup 后形成 terminal observation，不再因测试失败被 cohort 丢弃。下一步
仍是真实外部模型执行、20 项 evidence 和 proposal Gate。

本地 NE-02 波次集合最终为 `169 passed, 1 skipped in 104.80s`，跳过项仅为
Windows symlink 权限缺失且不计 PASS。该波 migration 为 0136；仓库当前
continuous head 为 0137。首次集合中的
4 项失败已透明保留并修复。当前本地代码状态为
`CODE_COMPLETE_LOCAL / ACCEPTANCE_PENDING_EXTERNAL`，不等于真实收益达标，
也不允许 L3 production promotion。
收口发布合同首次因 manifest 仍硬编码 0135 得到 `58 passed / 1 failed`；
更新为 0136 后完整复跑为 `59 passed in 8.75s`。统一 static/doc Gate
和 fresh 架构 Gate 1～14 均通过，失败清单为空。

## 历史编号

- TASK-0000～TASK-0622、M48～M57、AW-00～08、GR-00～06 已冻结。
- 原文位于 `../_history/programs/2026-07/` 或 Git 历史。
- 历史编号仍可用于搜索，不得决定当前完成度、优先级或 Goal。

## 记录规则

1. 小工作片只写 PR checklist 和定向测试，不扩张本文件。
2. Architecture Wave 状态写在唯一执行合同和机器状态中。
3. 一个波只有 production cutover、旧 authority 删除和 wave integration 同时成立才可完成。
4. 提交信息与 PR 全部使用中文。
