受众：CODEX
标题：七海实时情感数字生命当前执行合同

# 当前执行合同

机器状态标记：`NE-08_IN_PROGRESS`

## 1. 唯一目标

在不把普通对话重型审计化的前提下，完成七海情感生命主链的仓库收口、目标 Edge 三模态接线、真实多轮体验验证和完整服务栈验收。

本合同不以通用 PlanningSearch、通用 Skill Compiler 或多租户治理为当前完成条件；只有直接服务于情感连续性、低延迟、安全和真实交付的底座工作保留。

## 2. 当前基线

- 情感生命核心代码已覆盖观察、自我情绪、关系 episode、表达计划、主动陪伴和 profile 版本。
- 普通聊天采用轻量实时路径；工具和外部副作用仍采用 Governed Runtime。
- GPT-SoVITS V4 方案 B 已在本机 CUDA 环境完成参考条件生成和人工试听；当前使用预训练权重，不是七海专属微调 checkpoint。
- #194 与文档收口 PR #209 已合入 `dev`，本地和远端临时分支已清理。
- 历史验收已取得目标 Edge 的 ASR、GPT-SoVITS TTS、音频播放、Edge RPC、Unified Gateway 与真实消息平台核心往返证据；当前 Edge 保持关闭，本轮不得自行启动或重启。
- Gateway Chat 已贯通 canonical HostCommand／TaskGraph／Acceptance／Outbox；重复入站不会重复建图或外发。
- OBS/Monitor 已如实投影双端状态；VTS 缺兼容情绪热键而 `DEGRADED`，barge-in 关闭并为 `NOT_TESTED`。
- Monitor 的普通本机浏览器会话已接通：启动器生成 ACL 保护的 owner token，浏览器经 loopback/same-origin bootstrap 获得 HttpOnly Cookie，HTTP 与 WebSocket 不再显示伪“待连接”。
- Security Hunt、Tool Execute 和 MateEngine 直推的退役按钮已改为只读迁移说明；不会为通过 UI 测试恢复第二套写 authority。
- Cockpit 深层诊断默认折叠；依赖审计已消除 `brace-expansion` high，React Router 仍有 2 项仅适用于当前依赖边界的 moderate 待上游安全版本。
- GitHub Actions 因账户额度不可用，远程状态为 `NOT_RUN_ACCOUNT_QUOTA`。
- 整体状态保持 `INCOMPLETE`。
- 完整 48 条真实文本的人工作品检查仍失败；当前先以 `CompanionUtteranceFrameV1` 替代反复追加的自然语言规则，让模型围绕本轮语义和七海自身立场自然表达。代码与 543 项相关回归已完成，真实 Provider 定向复测尚未完成，不能提升人感状态。

## 3. 执行顺序

```mermaid
flowchart LR
    A["仓库与文档收口：已完成"] --> B["Edge / Gateway 核心往返：已完成"]
    B --> C["Host / OBS / Monitor 真实 UI与本机会话：已完成"]
    C --> D["固定版本 Docker 重启持久性：已完成"]
    D --> E["VTS 热键与 barge-in：待外部配置"]
    D --> F["新语义骨架真实定向复测"]
    F --> E
    E --> G["完整 48 条与三人盲测、性能"]
    G --> H["发布判定"]
```

## 4. 交付规则

- 每个实现提交使用中文标题并立即推送。
- 同一提交同步受影响文档与机器状态。
- Feature 分支基于最新 `dev` rebase，不创建深层堆叠链。
- 小切片只运行定向单测、合同和静态检查；完整服务栈集中验收。
- CI 零步骤额度失败记为 `NOT_RUN_ACCOUNT_QUOTA`，不修删 workflow，也不冒充通过。
- 运行证据放在忽略目录；密钥、用户数据、模型、音频语料和日志不得提交。
- 未经用户明确许可不得启动另一台电脑上的 Edge。

## 5. 仓库收口结果

仓库收口已经满足：

1. 当前权威文档与历史材料已经分层，旧结论不再进入当前 authority。
2. 文档 audience、authority、freshness 和 link Gate 已在 PR #209 本地验证。
3. 机器状态继续保持 `INCOMPLETE`，没有把局部实现写成整体交付。
4. #194 与 #209 均以中文 squash 提交进入 `dev`。
5. 本地和远端只保留长期分支 `dev`、`main`。

本轮 P0 代码已绑定提交 `cdaa4ea54`，固定版本 Neo4j、Chroma 与 Redis 的真实写读、容器重启和持久读取已经通过。当前执行点转为本机代码与体验门槛：真实 Provider 定向复测、完整多轮盲测和实时性能。目标 Edge 的历史证据继续保留，但当前运行未获重新授权；VTS、barge-in 和物理三模态不在本轮自行执行。

当前最小复验点已收窄到关系修复的可见文本边界。`92c49e7c6` 真实报告证明 canonical Appraisal／self affect／Voice／Avatar 已是克制受伤，但旧 Surface 重试仍把该感受抹回流程化道歉；现有工作树已修正首轮、唯一重试、候选评分与本地 fallback，566 项回归通过。下一步只重启本机 Host 并复验 `user_angry_at_natsume:1`；不得启动远端 Edge，也不得在该条逐句接受前扩大到 mixed 或完整 48 条。

`0f0a0b67b` 的复验已让最终可见句保留七海的克制受伤，但真实 Flash 双候选仍依赖本地 fail-soft，候选收益状态继续失败。现有工作树只把 composite 微策略改成两个正向口语结构，80 项生成链回归通过；再跑同一单轨迹一次。若仍是 `benefit_eligible=false`，停止围绕这一句追加 Gate，保留可见文本改进，并把真实模型／少量正向语料作为下一决策点。

`6d8f8e4c4` 已将 composite 末轮提升为真实 Flash 双候选一次通过，报告 SHA-256 为 `6625b532f2621a55a702f93bfa5782f4d907814a8216c49faddf9351b750eda2`。该窄因果与候选链关闭，不再重复运行；第一轮正式认错口吻作为 broader repair register 人工缺口保留。下一本机测试转到 `shared_joy_and_mixed_feeling:3`，仍不启动或探测远端 Edge。

`43014e45c` 的 mixed 单轨迹自动 1/1，但人工拒绝末轮纯镜像与“先这样待着”的情绪安排；报告 SHA-256 为 `6d12c4744c8bd6dccf79f1955b4c8c3674ba0ad5c1d654d1d2b2e51bce9f957a`。上游 frame 已正确，当前切片只修可见生成边界：typed shared-mixed frame 必须写七海自己的高兴与悬心，纯镜像进入既有一次重试。380 项相关回归通过；提交后只重启本机 Host 并重跑同一 case，不启动或探测远端 Edge，也不扩大到完整 48 条。

`b11de0f11` 的复验报告 SHA-256 为 `e35254a294e5616f6c0da5107f7b0b18c955527575dab5960827437cf9204465`。两个 mixed 深路径均一次真实 Flash 双候选通过，无重试或本地 fallback，三句经当前逐句复核接受；该窄根因关闭。候选结构复用和实时延迟仍是独立缺口，下一步转向更宽代表场景或 Architecture 2.0 非安全验收，不重复挑跑本 case；远端 Edge 继续不启动、不探测。

更宽的 5 个 mixed 变体报告 SHA-256 为 `0c10701bc1720b103ceddaa3d94834a76c3c3a7f85e6cb1f3fd9b96261240066`，只有 4/5 自动完成，且 3 条正文逐字复用同一模板；`:4` 的唯一重试还产生无 authority 的工作状态而整图失败。当前切片将完整正例改为三个语义槽和两种不同节奏，380 项回归通过。提交并重启本机 Host 后只复验 `:1/:4/:6`；远端 Edge、网络安全和 Pentest 继续跳过。

`60cab0e1e` 的三条语义槽报告 SHA-256 为 `11a46672e27c4728df1208b1807ce34b1d5499e0d62d1d89efd06b46b6d2604e`，自动仅 1/3，人工 0/3；抽象槽让 Flash 回到建议和工作助手口吻。当前最后一次有界 prompt 迭代改用按当前措辞稳定选择的两组 clause-level 语感碎片，仍只有同一次双候选和一次既有重试。380 项回归通过后重跑同三条；若仍失败，保留证据并转入模型／真实文本微调，不继续扩规则。远端 Edge 不启动、不探测。

`f87ad26f8` 的最终短语感实验报告 SHA-256 为 `708a0808b0372e02fc599f4618ce3dce5b12c84471bcc5f66ca48c65670f442b`，仍只有 1/3 自动完成且 2 条不可计收益。停止条件已触发：实验 helper 删除，生产 prompt 恢复为 `b11de0f11` 内容等价版本，380 项回归通过。下一步不再修改 mixed Gate；转向模型／真实文本微调方案或继续其他非安全验收。远端 Edge 继续不启动、不探测。

## 6. 最终验收

最终验收必须使用：真实 Docker 服务、真实 Host／OBS／Monitor／Gateway 进程、至少一个真实模型 provider，以及在用户允许后启动的目标 Edge。TTS、Avatar、重启、打断、网络恢复和长期多轮必须走真实传输，不得用端口占位、fixture 或 scripted replay 冒充。

状态判定：

- `PASS`：适用的本地与目标 Edge 强制链路、体验和实时指标全部通过。
- `ACCEPTANCE_PENDING_EXTERNAL`：本地强制项通过，仅缺用户未提供的外部凭据或设备。
- `FAIL`／`INCOMPLETE`：存在代码、体验、恢复、安全或真实性缺口。

## 7. 回滚

- 代码通过 `dev` 上的合并提交整体回滚。
- Profile activation 必须指向 exact previous version。
- TTS 声线配置可回退到前一已验证配置，不复制新版本证据。
- Acceptance 数据根与用户真实数据隔离，默认保留证据但不提交仓库。
## 2026-08-08 本机全栈收口与数字生命决策

本轮在隔离 acceptance 数据根中完成了本机强制服务复验。Neo4j、Chroma、Redis 均完成真实写入、更新、容器重启、持久重读和测试 namespace 清理；Host、OBS、Nginx Monitor 与本地 Unified Gateway 保持在线。真实 Chrome 对运行中的 Monitor 串行执行核心交互、外周状态和 Operator Session 三个用例，结果 3/3；最终日志 SHA-256 为 `c468d253e81d717bf1dcac68e1dddbc23ad9d845db7c03e9f0d4f770c93b394e`。外周状态合同明确区分 7 个未启动的远端 Edge 服务与已在线的本地 Gateway，不再使用固定数量产生过期断言；执行索引先于 detail 投影出现时，测试等待同一 graph revision 收敛到完整只读合同，90 秒内未收敛仍失败。

本地 Gateway 的翻译、Memory Store 和多 Skill Plan 三条真实路线均完成 TaskGraph、Acceptance 与 Outbox；报告 SHA-256 依次为 `540f3e76286b6d332a8e50a0c4bc13752d4dd72f913b2b3cfc5e706f0ad6f3e9`、`544ff73f9ac2e550b3d9f7ce1c571d3595c7c7bd25743c3f3eddfe8860c38b4f` 和 `3254cb880049cdf09ad4f39a3c791771a659049493f9623c5dccec6f7e7e6d3b`。同一 webhook 首次返回 accepted、第二次返回 duplicate，数据库只有一条 Inbox 和一条 Host request；报告 SHA-256 为 `ccc9aa9e7ccac3745e59f6428403b43404738078226444c941c39305c4dff9bc`。

随后执行完整 48 条文本多轮队列，共 144 个真实 Gateway→DeepSeek→TaskGraph 回合。144/144 终态完成，canonical writer 正常推进且旧 writer 增量为 0；自动结果为 46 条 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、2 条 `FAIL`。原始报告 SHA-256 为 `1efd6886792d2df8ea2ae5198bbf4127eaf273d17eeda04fd596a196fbda1cc8`。单人逐句复核不能接受整体体验：48 条末轮只有 45 个不同文本，同一句 mixed 回复出现 4 次，21 条以“嗯”开头，关系、自主、空间和修复场景仍出现模板化、政策化、敷衍或前后矛盾。

因此执行策略冻结为：不再增加 prompt 正例、禁词、评分规则或普通聊天审计；不再重复抽样同一随机轨迹挑选绿灯。下一阶段只能使用版本化真实自然文本调优或模型层方案，并以新的预注册变体队列比较 exact reuse、自然度、关系立场和三名评审偏好。远端 Edge 未启动或探测；网络安全、A09、A12、Security Monitor 与 Pentest 继续为 `NOT_RUN_USER_SKIPPED`。当前结论为“本机强制服务栈 PASS，数字生命人感 FAIL，整体 INCOMPLETE”。

首个 source-bound 原句对照没有关闭该缺口：同一 6 条中自动结果只从 1/6 变为 2/6，修复表达局部改善，mixed 仍复用完整模板。随后 3 条未用于前述选择的 mixed 变体全部失败，确认系统提示和 fail-soft 中的整句答案才是模板坍缩的直接来源。`00c52762f` 删除模板后的同三条真实报告 SHA-256 为 `ab77eb30aa9aedd42d1d06293a32d1375261deef14bc2aac6f647a01735adf89`：9/9 accepted、writer probe 通过、exact reuse 为 0，但自动仍为 0/3；一条是非收益 fail-soft，两条继续替用户安排情绪。去模板子项关闭，整体继续 `INCOMPLETE`，下一步只允许模型层或版本化真实文本方案。

### NE-04 真实 Docker Golden Suite

提交 `8afa13f856c588edcec6a8e1ac4d205efbea02d2` 的 clean detached worktree 已执行 Research Planning、Proactive、Memory Write 三类可信实验，各自包含 baseline/candidate，共 6 个真实 Docker arm。镜像为 `python@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df`；所有 arm 均为 `network=none`、只读根文件系统、`65534:65534` 非 root，effect audit 为 0。SQLite migration head 为 `0140#evolution_lab_redacted_dataset`，最终持久化 6 个 run receipt、3 个 report、3 个 proposal 和 1 个 suite receipt；运行目录无残留，生产副作用和生产影响均为 false。

正式收据 SHA-256 为 `fddf247166c7cc1fad7a9950e9013c5c5424734ecfe0e19a8b4fa1bff511bc4d`。同一提交还通过 2 项 opt-in 真实 Docker 集成，其中响应在 completion commit 后丢失时，第二次调用只重开 exact receipt，不重复 arm。临时 worktree 已移除，没有创建临时分支。NE-04 本项提升为 PASS；Skill lifecycle、Feedback/Knowledge/Identity 仍需分别取得生产 authority 级证据。

### NE-05／NE-06 本地生产 Authority 验收边界

NE-05 选定的 19 项非网络测试在提交 `2b9d42f4b` 上全部通过，覆盖确定性 Skill Compiler、accepted-trace 私密字段拒绝、真实签名 bundle 的 quarantine/stage、签名后篡改拒绝、V2 activation exact replay、两版本切换、exact predecessor rollback、degrade、未知 canary reconciliation、Host 只解析 active content-addressed version，以及旧 V1 mutation authority 和 Forge route override 的物理删除。日志 SHA-256 为 `ee294424670931d80f23fa309884498b8d674e3b5a7e6248fc9165bf2fc910dd`。这些用例使用真实 SQLite 和文件系统，但 Skill 专属 Lab attestation、Review 和 canary 仍由测试构造；状态只能是本地 authority PASS、完整生命周期 PARTIAL。

NE-06 的 19 项生产 Repository/Composition 集成也全部通过：Feedback 按 subject baseline 应用、回滚后新版本再次应用，签名 ingress 跨 scope 拒绝；Knowledge 过期、shadow→enforce、DecisionRecord、active winner、冲突 cursor 和 deprecation；Identity release review preparation、no-change、重大差异阻断和 exact previous snapshot rollback；V1 mutation surface 与 direct SQL 只读。日志 SHA-256 为 `38246070986f3db46fdcf12b8edd4403aa100152891840124cff5d2d6c12fc9d`。尚缺真实 Operator Review 与在线 Host 下一轮行为，因此 NE-06 同样保持 PARTIAL。
