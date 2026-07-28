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

当前活动实现波为 `NE-08_IN_PROGRESS / DL-06_MOTIVE_GLOBAL_SINGLETON_RETIREMENT_SLICE`：以实时
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
scope 确定性选择单一 aggregate，旧 `last_target` 无发送权。`proactive.gateway` 的 immutable
Host/Gateway receipt 现重验 outbox、授权摘要、scope 与 target 后才写入 deterministic
`relationship_proactive_delivered`；receipt 后崩溃由 Host 启动或 outbox tick 只读重开，绝不
重发外部消息。它不代表第三方平台送达或用户已阅读。
DL-06 现另有签名的 structured permission ingress：严格 JSON 只可写 `ALLOWED/DENIED`，
并将 `user principal == relationship subject`、`tenant == workspace` 与 `companion:contact:write`
一并重验，不能由自然语言、Bond、情绪或模型替代；claim completion 与 Life receipt 支持
exact duplicate/restart replay。0147 已将低风险表达 profile 接入 canonical Life command：候选必须
重开五条独立、跨三天、`HELPFUL`、boundary-safe 且 `helpfulness>=0.85` 的 aftereffect；五次后续
真实互动才可 activate，并只能 exact rollback 到当前 profile 预先绑定的旧版本。CANARY/ACTIVE profile
会真实进入下一轮 text/Voice/Avatar 的冻结 plan，但不能改变 Soul、consent 或 self-affect；当前
保守文本 classifier 为 `0.72`，不会误作学习成功。0148 已将受认证的结构化 profile control
接入 app-local Host：`POST /v1/digital-life/relationship/companion-profile` 必须同时满足
`companion:profile:write`、`user principal == relationship subject` 与 `tenant == workspace`；它只能
从 canonical strong aftereffect history 启动 candidate canary，不能从自然语言推断、直接指定 activation
或改写 Soul/consent/self-affect，Secure Ingress audit ref 与 candidate 一同持久化。Monitor 审核控件、
canary 真实证据、旧 writer 清零仍未完成，不得称为主动陪伴闭环。受治理预算流与连接恢复仍由后续 DL-06～DL-07
交付。此前 NE-01～NE-07 的 Universal Runtime、搜索、
Lab、Skill、Knowledge 和服务栈工作仅保留为基础资产；它们不能证明七海具有独立
情感因果、长期关系修复或文本/语音/Avatar 同轮表达。后文关于 NE-01～NE-07
的段落是历史范围说明，不再决定当前执行顺序。

本切片同时新增 receipt-scoped `CompanionResponseFeedbackRequestV1`：
`POST /v1/digital-life/relationship/response-feedback` 只允许 `HELPFUL/NEUTRAL/MISUNDERSTOOD/BOUNDARY`，
必须持有 `companion:feedback:write`、exact user/relationship subject 与 tenant/workspace 绑定，并且
引用一条仍处于 canonical provisional episode 的已接受 response receipt。只有明确的 `HELPFUL` 映射为
`0.95` strong aftereffect；它只可推进既有 profile CANARY，无法自动创建或 activation profile。现有
自然语言 classifier 的 `0.72` 和旧 ResponseFeedback proposal 保持低权重/proposal-only，均不具备该
成长 authority。

当前子波改为 `DL-06_SIGNED_PROFILE_ROLLBACK_CONTROL_SLICE`：
`POST /v1/digital-life/relationship/companion-profile/rollback` 只接受
`CompanionProfileRollbackRequestV1` 的 current active profile identity，必须另持
`companion:profile:rollback` 并重验 exact user/relationship subject、tenant/workspace。
body 没有历史 target；canonical lifecycle 只能恢复 current profile 的 frozen predecessor，且将
audit-derived `source_control_ref` 写进 immutable rollback event，使 duplicate、claim-result 中断和
restart 仅重开一个 Life receipt。该入口不改变 Soul、consent 或 self-affect；Monitor/Review、长期
canary、legacy writer cutover 与真实服务验收仍未完成。

当前子波此前进入 `DL-06_HOST_EMOTION_RUNTIME_CUTOVER_SLICE`：生产
`build_production_host_application_ports()` 已不再构造或发布 mutable
`HostEmotionRuntime`，也不再发布 process-wide `user_emotion_profile`。ASGI 为 Tribe
注入与 Input 相同的惰性 `LifeCommandService` authority；二者在真实 request 时均经
`AffectFusionService` 写入 scoped observation/appraisal，再读取 canonical self-affect。
`HostEmotionRuntime` 只可在没有 Life authority 的明确 offline/test compatibility composition
中使用，不能作为生产 fallback。

当前 `DL-06_LIMBIC_LEGACY_AFFECT_WRITE_RETIREMENT_SLICE` 已在同一 immutable
`TribeApplicationPorts` 上标记 canonical Life authority。production Limbic 遇到 legacy
`EmotionPersistence` restore/persist、EmotionVector metabolism、`emotion_timeline` append、scheduler
decay 或 Bond progress/regress callback 时，统一返回 `RETIRED_CANONICAL_LIFE`，不再创建、更新或追加
第二份 self-affect；这个 gate 只在明确 offline/test compatibility composition 未选 canonical Life 时放开。
它没有替代新的 Life command，也没有退休 BodySense、Endocrine、Motive 或 volatile Bond queue，故 DL-06
仍为进行中。

当前 `DL-06_ENDOCRINE_LEGACY_MUTATION_RETIREMENT_SLICE` 继续复用该 production authority：
Endocrine 只能返回输入 hard-state 的无状态 compatibility projection，不能消费或累加旧
cortisol/oxytocin delta、更新 `_last_state`、生成旧 hormonal hint 或向 Limbic/Interoception/PainReward
fan-out。`record_stress_event`、Bond/loneliness/task/immune callback 与显式 delta 入口在 canonical
mode 均返回 `RETIRED_CANONICAL_LIFE`；Circadian/MirrorNeuron 已停止直接写 Endocrine 私有字段。
这只退役 Endocrine mutation authority。随后 `DL-06_BODYSENSE_GLOBAL_MUTATION_RETIREMENT_SLICE`
已在同一 canonical authority 下让 production Surface/startup 不再 warmup 或读取 global
`BodySenseModel`：每次读取都必须提供 relationship subject，并从对应 Life snapshot 做纯读 projection。
legacy conversation/tick/loneliness mutation 统一返回 `RETIRED_CANONICAL_LIFE`，retired tick 不再发
`BODY_SENSE_UPDATED`，因此不能重新驱动 silence 或 legacy metabolism。随后 Motive 已去除模块级
`_engine`：兼容工厂每次创建无状态规则对象，所有动机只能由本次传入的快照纯投影，不能作为跨用户或跨
turn 的情感 authority。volatile Bond queue 仍待处理；这一事实不是 DL-06 或真实陪伴验收完成。

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
