受众：PROJECT
标题：Natsume AI 当前任务清单

# 当前任务清单

本清单只记录从当前代码到“七海实时情感数字生命可真实验收”的剩余工作，不重复历史阶段流水账。

## 当前收口

- [x] 分离用户情绪观察与七海 self affect。
- [x] 建立情感 episode、关系后效应、profile 版本和兼容写入计量。
- [x] 普通聊天恢复轻量实时路径，外部副作用保留 Governed Runtime。
- [x] 选择 GPT-SoVITS V4 参考条件方案 B，并完成本机 CUDA 生成样例与人工试听。
- [x] 将数字生命主链合入 `dev`，清理本地与远端临时分支。
- [x] 通过 PR #209 重整当前文档、固定证据和历史归档，并在合并后的 `dev` 上复核文档 Gate。

当前声线仍使用预训练权重和参考音频，不是七海专属微调 checkpoint；下一步从目标 Edge 的真实语音与 Avatar 接线开始。

## P0：目标 Edge 三模态验收

- [x] 将已选择的声线配置、参考音频和所需权重同步到 Edge 电脑的非仓库模型目录。
- [ ] 在 Edge 上验证中文 ASR → 日语 TTS 的完整语言链路。
- [ ] 实测普通、开心、安慰、委屈、边界、修复、安静陪伴等表达计划。
- [ ] 接通 VTS/VRM，确认与 TTS 共用 `synchronization_id`。
- [ ] 实测播放中打断、TTS 断线、Avatar 断线和 Host 重连。
- [ ] 测量首段 TTS、barge-in 和 Voice／Avatar 起始偏差。

2026-08-06 当前实测增量：Edge 已在显式隔离 `DataRoot` 下以单实例启动，ASR／GPT-SoVITS 为 `READY`；短句真实播放完成，播放中的长句经签名 RPC 在 16 ms 内中断。短句播放启动为 2641 ms，只是一个样本且略高于 2500 ms 门槛。VTS 仍卡在设备端授权窗口且没有加载角色模型；当前只通过“手动签名打断”，没有通过“麦克风声学检测自动打断”，因此其余 P0 项不勾选。

2026-08-06 第二轮实测已关闭两个运行缺陷：分段 TTS 的父命令现在会由子分段终态结算，首段失败会取消其余分段，barge-in 会同时清空同父命令待播队列；真实物理探针确认打断后 `playing=false`、队列为 0。VTS API 51 被识别为等待人工授权，自动重连暂停，后台线程与失败计数均不再增长。Host 的中日配对修复已在 `686a8835f` 真实 Gateway 链路得到 `voice.speak=APPLIED`；代码回答随后暴露单句门禁修复触发第二次模型调用并超时，以及 durable REJECT 被误报为 COMMIT drift。`084fd6aa6` 的真实新代码请求只调用一次外部模型，Host 主链 `OK`、完整代码正文与远端 Voice 均通过；Avatar 仍只被 VTS 外部授权阻断。

约束：Edge 在另一台电脑上。本轮双端验收已经用户明确授权；后续新一轮远程启动或可能影响设备的操作仍需遵守当轮授权边界。

## 2026-08-04 已完成的双端观测切片

- [x] Life Body 从 canonical `hard_state.emotion_tag` 派生人格上下文，并在真实 OBS／Nginx Monitor 浏览器中消除“待连接／待恢复”。
- [x] Gateway 完成事件公开最小 Reply Outbox commit marker；回复正文与私有字段不进入观测投影。
- [x] 用真实签名 Gateway 分别跑通翻译 Delegate、Memory Store 与多 Skill Plan；最新有效桶的 Reply Outbox、Closed Loop、Completion 均为 100%。
- [x] 保留 360 分钟历史失败，不降低阈值，不把 Outbox commit 标作外部平台 ACK。
- [x] Monitor read-model 27 项、生产构建、真实后端主链与核心交互浏览器用例通过。

本切片不关闭 VTS 情绪热键、barge-in、常驻 ASR 噪声、PlanningSearch 收益、人类盲测或最终 SHA 故障矩阵。

## 2026-08-05 Architecture 2.0 当前 SHA 前置门禁

- [x] 将 migration catalog／覆盖合同推进到现有 `0153`，不改写历史 migration。
- [x] 对 A03 报出的 15 个生产热点执行真实职责拆分，未扩大 cohesive 白名单。
- [x] 当前工作树非安全静态门禁 A01～A08、A10～A11、A13～A14 通过；A09、A12 依用户要求保持 `NOT_RUN_USER_SKIPPED`。
- [x] 修正旧状态对 A03 的误报：不扩大白名单，拆分 Surface 候选约束、时间指向与 Affect observation，Gate 3 重新通过。
- [x] 当前工作树通过 249 项 Runtime/恢复、迁移/备份、Workspace、通用路线及 Evolution/Skill/Identity 非安全回归。
- [x] Research/Code PlanningSearch 当前工作树机械回归 128 passed、1 项 Windows symlink 权限分类跳过；Code 执行范围与真实 cohort 提交逐 blob 相同，Research 仅有保持默认行为的通用 scene 参数扩展。
- [x] Pentest、A09、A12 与网络安全保持 `NOT_RUN_USER_SKIPPED`，未计入通过项。
- [x] 拆分后的受影响波次回归 306 项通过，pytest 全量收集通过。
- [x] 在当前运行时代码 SHA 上启动真实 Docker、Host、OBS、Nginx Monitor 与 Gateway；Neo4j／Chroma／Redis 完成隔离写入、容器重启、重读和清理，Monitor 的普通会话与非安全核心页面 E2E 通过。
- [x] 当前运行时代码 SHA 的 4 类 12 回合真实 Gateway／DeepSeek 数字生命冒烟为 4/4 自动待人工，12 个 turn 全部 accepted，旧情感 writer 增量为 0。
- [x] 非安全 Monitor read-model 扩展到 45 项并全部通过；当前工作树 LCOV 为 line 93.67%、branch 70.83%、function 83.90%。该切片显式排除 Security 页面和安全 organ model，排除项保持 `NOT_RUN_USER_SKIPPED`，不能表述为完整前端覆盖率。
- [x] Python Unit 覆盖预检定位并修复 5 个非安全回归：Scheduler 冻结时钟、Gateway 已退役线程断言、OBS 隔离测试的新鲜度耦合，以及缺失 canonical ports 时 Prefrontal 重新获得 Endocrine／Limbic／Pain writer 的历史测试语义。相邻回归 29 项通过，完整 Unit 分片仍需在提交 SHA 上重跑。
- [x] `d696417c47` 的六片完整非安全 Unit 首轮实际执行为 7939 passed／7 failed／8 classified skips／224 deselected。7 个失败已收口为 canonical mode timestamp、三项轻量 Chat 路由旧断言和三项显式 legacy Prefrontal fixture，目标复跑 7/7；该失败运行不计覆盖率，下一提交重新跑六片。
- [x] 当前候选树的非安全 Unit run7 为 7954 个 JUnit testcase＋2 个成功 subtest，合计 7956 outcome、0 failure/error、8 classified skips；Contract 为 1898/1898，本地 E2E 为 68/68。Unit-only 全生产分母为 line 57.55%、branch 39.85%，不得冒充全套覆盖率。
- [x] 当前 SHA 的完整 Integration 六片为 862 passed／2 Redis env skips／0 failed；首轮 11 个失败已在完整相邻集合中关闭。实际代码修复为 Scheduler/Internal 共享 clock、精确 startup replay 不重新要求父 Grant 存活，以及未发生 takeover 时 heartbeat 可用 token/version/fence CAS 晚续租；E2E 同步确认 `ASR_CHUNK` 不进入长期 voice ledger。
- [x] 生成当前 SHA 的 Unit／Contract／Integration／E2E 合并覆盖：去重 10786 个 outcome，10776 passed、10 classified skips、0 failed/error；完整生产源码分母 line 68.50%、branch 51.40%、综合 64.27%，低于冻结 Gate，明确记为失败而非完成。
- [ ] 提升覆盖率并完成剩余非安全故障／回滚与三人盲评；A09、A12、Pentest 按用户要求不运行。全部强制结论满足前不宣称 Architecture 2.0 完成。

## P0：真实对话体验

- [x] 将 72 条覆盖说明与自然可执行 setup 分离；无真实多模态／主动／重启／Edge fault driver 时 fail-honest 为 `NOT_RUN`。
- [x] 修复自然文本只产生中性观察的问题；低落、拒绝建议、混合感受、好消息、空间和现实关系边界均进入 canonical Appraisal，本地相邻回归 124 项通过。
- [ ] 用更广真实 Gateway／DeepSeek／远端 Voice cohort 与三人盲测确认语言质量。`e4c6fb564` 三轨迹为 2 自动通过、1 失败；自动低分的“有点怕也正常”人工更自然，自动通过的“省得你嫌烦”却明显不合格。已补齐同义边界／自主／混合退化门禁（101 项定向测试通过），但不再用三条反复抽样冒充“像人”证明；下一步进入其余 A15 强制项并保留人感未证状态。
- [x] 修复真实多轮 runner 每回合改变发送者、把同一会话伪装成多个用户的问题；新增 subject/session 漂移 fail-closed。
- [x] 复验三类多轮情感连续性修复。`4127752f0` 的第三次同 cohort 报告 SHA-256 为 `a818e4cbaa4f190c05f23d1cbe6396d71618caacc771c7ac334aa93b4353a6bf`：9 轮真实 Gateway／Host／DeepSeek／TaskGraph 全部接受，Task artifact 的最近对话角色交替且 typed continuity 进入最终 packet；低落、空间边界和混合结果文本经窄范围单人复核 3/3 通过，无额外 semantic retry。自动为 2/3，剩余失败仅来自远端 TTS `ERROR` 导致三轮 `EDGE_NOT_IDLE`；Voice/Avatar 和 48 条三人盲测仍是后续独立 Gate。
- [x] 清理并复验远端 GPT-SoVITS worker 生命周期。真实资源快照确认 17 个残留 runtime、约 2.5GB 可用内存和约 7.9/8GB 显存占用；只终止匹配模型 runtime 后恢复到约 24.7GB／675MB。`1909d0b3e` 已部署到实际 acceptance 副本，Edge 干净启动后 TTS 为 `READY` 且只有 1 个 worker。全新三轮 Gateway→Host→TaskGraph→Edge 轨迹最终 Voice 为 `TTS_PLAYBACK_FINISHED/APPLIED`，报告 SHA-256 为 `3fbd674e9f828a67f434f2cfef5acb950dbf53643be03c0a86fcd35f206f5f3a`；独立 5 秒 READY timeout 得到预期 `TimeoutError` 并释放子进程，结束后仍只有原单 worker。生命周期收据 SHA-256 为 `3e8024afd135f69d6e6dac693e71539100481c9b584b040d9f59a2b37ce3f607`。VTS 不在本项范围内，继续保持外部未连接。
- [x] 修复 Gateway 401 等入口拒绝仍轮询 180 秒的问题；A15 双机回调 HMAC 摘要已重新对齐并只重启远端 Gateway，未重启 Edge。
- [x] 修复 Research 双臂共享提示摘要导致不同 source set 无法形成真实可验收请求的问题；新 RunSpec 按 arm 持久绑定渲染提示摘要，旧 0107 规格保持精确重放，25 项相邻回归通过。
- [x] 补齐官方 DeepSeek V4 provider-owned token usage 的版本化成本换算；真实 canonical Research 探针形成 51 tokens／9 micro-USD／零工具调用的 benefit-eligible attestation，代理和不完整 usage fail-closed。
- [x] 补齐 Research 隐藏质量评审边界：新鲜双臂正文只在同进程短暂传递，终态 ledger 和 cohort receipt 只保存摘要；隐藏评审独立走 canonical 外部 ModelAttempt，并绑定自己的 prompt digest、usage、cost、transport 与响应摘要。22 项相邻回归通过。
- [x] 冻结 24 题 Research 真实收益队列：运行时恢复、存储持久化、模型证据各 8 题，每题含本地与官方 Web 来源；24/24 baseline 使用 secondary，24/24 search 由真实 AUTO L3 选择 primary＋independent，dry-run 外部调用为 0。
- [x] 在 `1c9c2b2bd` 上以独立 namespace 完成单题真实双臂＋隐藏评审预检；状态 `PASS_PREFLIGHT_ONLY`，报告 SHA-256 为 `03b72f857a57a94098269e1537879154ccb3dce9471189b88844bc6193a7a0bb`。
- [x] 完成 24 组真实 Research baseline／PlanningSearch 配对。质量均值提升 0.85416667、bootstrap 95% 下界 0.75、citation validity 1.0、成本比 1.32215978、p95 延迟比 0.98756943、严重错误 0，最终状态 `PASS`；报告 SHA-256 为 `eb17699c0f85b2b9b680f02f5ce1c716726e2f15f2bfd8b72cf437f6c7277894`。只生成 promotion proposal 资格，未启用生产影响。
- [x] 冻结并实现 Code 20 题真实收益入口：20/20 baseline 关闭 L3、20/20 selected 由公共 Code PlanningSearch AUTO 条件 MCTS 选择 safe path；确定性源提交、随机 arm 顺序、独立外部请求、fresh detached worktree、隐藏 pytest、静态 pytest、清理收据和 cohort ledger 均已接线。dry-run 外部调用与 worktree 执行为 0，50 项相邻回归通过。
- [x] 在 `e3094cfbd` 上完成 Code 单题真实预检：v1/v2 的 selected 均成功、baseline 均在 8 秒 provider 上限超时，失败收据保留；v3 显式使用 30 秒上限后 baseline 隐藏测试失败、selected 通过，状态 `PASS_PREFLIGHT_ONLY`，报告 SHA-256 为 `147da7fffe5937379ed8da80c53127ac22a1a4dcfa0432ca584a807cfaa269a4`。
- [x] 修复预检发现的路由边界：Code 不再冒用最终 `RESEARCH` scene，改用 `CODE_BENCHMARK`；runner 默认模型上限 30 秒并把 scene、timeout 与 90 秒 request deadline 绑定进 execution profile 和外层收据，Research 默认调用保持兼容；57 项相邻回归和修复后 20/20 dry-run 通过。
- [x] 在路由修复后的 `926d643c6` 上重跑单题：arm 顺序为 baseline→selected，baseline 隐藏测试失败、selected 通过，两臂静态测试通过，DeepSeek 外部 attestation 完整，状态 `PASS_PREFLIGHT_ONLY`，文件 SHA-256 为 `63559c45967520bddc45ac98104b24cb34e4af4c0fb945efe7c9bcd0214a66f1`。
- [x] 保留首轮完整队列失败：前 5/20 题均为 baseline 失败、selected 通过，第 6 题 baseline 在 30,015 ms 触发 `PROVIDER_TIMEOUT`，整轮为 `FAIL`，文件 SHA-256 为 `272638f91ffbb5b10a449ce725e25750b6de3ab80a7aec383b917641b5ecce8d`。
- [x] 将固定队列升级为 `2026-08-06-v2`，为 Code prompt 冻结 temperature 0、top-p 0.8、max tokens 600、thinking disabled 的 JSON generation profile，并把该 profile 纳入 execution digest；v1 失败成员不覆盖、不挑选。98 项相邻回归和 v2 20/20 dry-run 通过，profile digest 为 `cdedce854508661e46540b011eb9c7005519eb23cf89f7217e866537cfde5302`。
- [x] v2 首次预检在外部调用前被 Code ledger 以 `CODE_BENCHMARK_REPLAY_DRIFT` 拒绝：cohort/config 已升级但 workload registration 仍使用 v1 题号。现改为 `<固定题号>@<cohort_version>`，workload/baseline ref 同时绑定 cohort version；失败尝试没有模型调用且保留原记录。
- [x] v2 第二次预检的两只 DeepSeek arm 均成功且延迟仅 625～922 ms，但随后 Windows bare clone 因 acceptance 根目录、namespace 与 run ID 叠加过长而失败；失败文件 SHA-256 为 `376e1a62822cbaabbfe8213d1823bae7147119e91601d5633f9897aade8ee2ae`。现将 runtime directory 与 run ID 改为固定长度 SHA-256 前缀，语义身份仍由 ledger/receipt 保存；32 项定向回归及 20/20 dry-run 通过。
- [x] 短路径修复后的 v2 单题预检完整通过模型、双 worktree、隐藏／静态测试和清理，但 baseline 与 selected 隐藏测试都通过，文件 SHA-256 为 `41a6584882ad883c064555b9cc71982bc52df930bb863336c656dfc53424e71c`；判定为 objective 泄漏导致的无效判别，不计收益。
- [x] 队列升级为 `2026-08-06-v3`：20 题 objective 统一冻结为“仅依据本轮选中证据实现”，不得出现绝对值、分块、slug 等正确语义；正确规格只保留在 selected evidence 与隐藏 cases，loader 对 objective drift fail-closed；33 项定向回归与 20/20 dry-run 通过。
- [x] 在 `3eaa5486b` 上完成 v3 单题真实预检：baseline 隐藏测试失败、selected 通过，两臂静态检查通过，完整双 worktree 与 cleanup 通过，文件 SHA-256 为 `224c24147f1daafe608d2df2878fa58f23a801eac291df634d27c5356bcbe544`。
- [x] 完成 v3 20 题真实 Code cohort：baseline／selected 通过率 0／1、提升 1.0，静态检查率 1／1、diff-risk 0.1／0.1、成本 493／783 micro-USD；40 个外部请求全部 started→succeeded，状态 `PASS`。完整文件 SHA-256 为 `9c9938f15d303f5699debc60af5d7ebf09b417f70b4fd2824e8115cf12062170`，proposal eligible 但 production influence false。
- [ ] 运行 Pentest 30 次授权实验；Pentest 没有真实最终 cohort 前，PlanningSearch 三域总状态保持 `PARTIAL_RESEARCH_CODE_PASS_PENTEST_PENDING`。
- [x] 复验普通 Gateway 陪伴实时模型路由。`8cbdadb7d` 加载后真实命中 Tier B／`CHAT_SURFACE_REALTIME`／`deepseek-v4-flash`，webhook 63 ms；192-token 报告 SHA-256 为 `fc9b091c3a45b5774e9617e82486c3bfe6bff233064a6b11a8d676e5220487a1`。路由切换和入口 fail-fast 已证实，回复可用性由下一项继续验收。
- [x] 复验实时配对 JSON 的 non-thinking 路径。`c6a6e0b36` 三轮均完成 TaskGraph／Acceptance，最终轮远端 Voice 为 `TTS_PLAYBACK_FINISHED/APPLIED`；provider latency 1,407～2,467 ms、completion 69～128 tokens，报告 SHA-256 为 `45ae99ee90ea795dd070691bebf2e3968fbc14c72859febe388726f569bf97c0`。自动结构状态为 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`；Host 总耗时 7,739～12,715 ms、前两轮语义修复和 VTS 外部拒绝继续进入后续性能／体验项。
- [ ] 使用至少一个真实非 mock provider 跑固定 72 条场景。
- [ ] 修复真实 provider 首字延迟和异常回退，不以缓存数据替代。
- [x] 修复最终 SHA Host 探针只按 HTTP 200 产生假绿的问题：Health／Ready／Canon 必须校验业务语义，Host-only pending external 只能通过显式参数接受。
- [x] 修复 `wc-004` 空 hash 与 Canon 漂移被误分类为 source unavailable；全部 `wc-*` 生产资产必须通过真实 loader 合同。
- [x] 修复 Canon 健康 detail 经隐私投影隐藏后仍显示 unavailable 的矛盾文案；只有已验证 `OK` 可使用固定健康文案。
- [x] 在 `2cf552c92` 上完成严格 Host-only 探针：Canon `OK`、真实 Chat COMMIT、required unavailable 为 0；远端 Edge 未探测，报告 SHA-256 为 `0164db7266eddf87ab45216651d1de350817670f91f6aaf706bee71c21bb44ec`。
- [ ] 在最终 SHA 继续复验 Docker 三库、Monitor 真实浏览器和本地 Gateway，仍排除网络安全与远端 Edge。
- [x] 对齐外部服务 runner 与当前 A15 隔离项目身份；两次无副作用配置拒绝均保留失败收据，正式运行必须使用完整 Git SHA。
- [x] 在 `f2a7f176c` 完成 Neo4j／Chroma／Redis 真实写入、更新、容器重启、持久重读与清理；报告 SHA-256 为 `39d7d22a4ce6dee0441cf44ae492a6e1d4ec59575a380558cd6ad348fb6a9af6`。
- [ ] 从 72 条场景中冻结 48 条多轮盲测集。
- [ ] 邀请至少 3 名评审，与稳定基线盲测并达到 PRD 门槛。
- [ ] 重点检查情绪变化幅度、混合情绪、裂痕／修复和技术任务中的人格连续性。

## P1：完整本地服务栈

- [x] 以隔离 acceptance 数据根启动 Neo4j、Chroma、Redis、Host、OBS、Monitor、Gateway 和远端 Edge。
- [x] 对三项 Docker 服务完成真实写入、读取、重启恢复和清理 namespace。
- [x] 验证 Monitor 展示真实 task、budget、verification、acceptance、effect 和 receipt 数据。
- [x] 验证真实 Gateway 重复入站和 Gateway 重启：同一 webhook 只有 1 条 Inbox、1 个 HostCommand、1 张 TaskGraph，重启后未新增 replay。微信客户端不可用，平台物理外发仍为 `NOT_TESTED_CLIENT_UNAVAILABLE`。
- [x] 验证 Outbox 对账／网络恢复不会重复外发：`1616168d2` 的隔离真实 SQLite 与 loopback OneBot HTTP 收据证明，发送前适配器缺失先 `RETRY_WAIT`，恢复后两次 Attempt 只产生 1 个 HTTP；响应丢失路线只产生 1 个 HTTP 后进入 `GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN`／DLQ，重复提交、进程重启和无 reconciliation 的 operator replay 均未再次发送。收据 SHA-256 为 `c878cda4f2b3d428f8bbad04b55a03f72be6f8ac4b43e8a923435af20c5c17b2`。真实 QQ／微信客户端不可用，物理送达继续为 `NOT_TESTED_PLATFORM_CLIENT_UNAVAILABLE`，本地 ACK／DLQ 未被冒充为平台证据。
- [x] 复验 Gateway Inbox／Outbox 独立推进。`4c66d61b8` 远端真实时间线中，Outbox 从 dispatch 到 uncertain DLQ 持续 16,075 ms；同一窗口内两条 Inbox 由独立 `:inbox` worker 在持久化后 118／197 ms 领取，第一条早于 Outbox 终态 15,604 ms。三轮 TaskGraph／Acceptance 完成、最终 Voice 真实播放；报告 SHA-256 为 `8709a206ce052a2eeb597f6ba549d3e5f98315efa3d62245a682555267208992`。平台物理送达仍按外部不可用处理。
- [x] 注入真实 Host 崩溃与 Lease 过期：修复后旧 Lease 变为 `EXPIRED`，fencing token 2 在 2.446 秒后接管同一 Attempt，Node Grant 尚余 57.211 秒；最终图、节点、预算和 6 类 Acceptance Effect 全部正常结算且无重复命令／图／Attempt。
- [x] 运行隔离真实 SQLite terminal reconciliation canary：精确可修复案例自动释放预算，模糊案例保持未修改并以 `TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED` 阻断；报告强制绑定真实 migration head `0153`。
- [x] 注入真实进程 unknown effect：operation reservation 已 `RESERVED` 后强杀 Sidecar Host；重启后同一 Attempt 进入 `RECOVERABLE`，operation 进入 `RECOVERY_REQUIRED` 并生成 signed-reconciliation hold，持续观察无第二次 worker／模型／Acceptance／Effect／reply。
- [x] 注入 rollback failure：隔离进程使用真实 SQLite／生产 `ProcessTaskRuntimeContainer`，验证失败回滚保留 retry handle、重复 activate 被拒绝、Store 不提前关闭、解除故障后精确关闭且终态 replay 为 no-op；`8c3a91f66` 收据 SHA-256 为 `7d8ff1bfe02f38f80a57c9ea11b75760d7e112c8685a116ee6c6b0a2c7ffd0fc`。
- [x] 验证 Sidecar 停止隔离：显式空 RuntimeRoot 不再按 18082／18180 全局兜底，主 Obs 与 Nginx PID 均保持不变且健康端点继续为 200。
- [x] 验证分段 TTS 合成失败会结算父命令并取消兄弟分段，打断后队列归零。
- [x] 验证 VTS 人工授权等待不会触发无界自动重连或句柄增长。
- [x] 关闭 OBS 派生 projection journal 写放大。`636252264` 真实 30 秒样本把 2,996,490 bytes 原始公开载荷压到 410,858 bytes，降低 86.3%。一小时检查点首轮如实暴露 7 条同实例 lineage revision 误写，失败报告 SHA-256 为 `ad4d8c178a66e0040b147b1c8fa6c6129be443b954fbc73bfecbdcb544a7b00c`；收窄到只强制 `source_instance_id` 切换后，第二轮 49.084 秒窗口有 192 个 current 文档刷新、31 个 source cursor 推进，既存未到期文档和同实例 lineage 新增历史均为 0，唯一新行是新 TaskGraph 的 6,872-byte 必要首快照，Host／OBS／Monitor 前后均为 200，page count 增量为 0。报告 SHA-256 为 `92baff1b2117e5d95a47e534a2c908642b47be4d7db56e9f144beae663f563e4`；既有历史未做破坏性清理。
- [x] 复验普通聊天最小同步提交与后台连续推进。`55d60a384` 的真实三轮中，第二轮在上一轮后台尚余 3,824 ms 时进入 Host，HostCommand／Graph、Attempt 和 history 均在上一轮后台结束前推进；六类 effect exact-once、末轮 Voice 完成且旧 writer 增量为 0。报告 SHA-256 为 `33eb1871d040578ea50185ee82732c7e37589eb2763182af3dcbda04ac798a75`。
- [x] 关闭 OBS Task 派生投影对普通聊天 canonical SQLite writer 的饥饿。`ead903f66` 已在 Host／OBS／Monitor／Gateway／远端 Edge 与 Docker 同时运行时复验：三轮 worker projection 到后预算落盘为 187／94／49 ms，详情波次上限实测为 16，index 加详情上限为 17；报告 SHA-256 为 `7415951164753cd00c68d55b40b35167e30feefe93d02503f259dee34f2f06ce`。
- [ ] 继续压缩普通聊天模型前固定开销与语义修复比例。上述复验的 TaskGraph 为 4,899／4,903／4,736 ms，Host 终态为 7,040／7,159／6,509 ms；第二轮仍有一次语义修复，首轮 worker projection 到 artifact 仍有 1,054 ms 冷态尾部。`0165e95f9` 的真实复测没有支持“`NO_RECALL` 重复 history IO 独占约 0.5 秒”的原判断：同句检索从 561 ms 降至 194 ms，但 provider 抖动令首句由 2.64 秒变为 2.76 秒，而且旧 helper 本就会先按内容短路。`7c454b6fb` 随后关闭了可证明的无关工作：普通陪伴消息不再读取轻小说最近推荐偏好，也不加载其深读持久化模块；真正的“第几本／详情”追问仍读取一次并完成深读。60 项代码与文档 Gate 通过，真实三条 `NO_RECALL` 检索为 100／104／124 ms、首句为 2.61～3.15 秒，子项关闭，收据 SHA-256 为 `25f23ac04b9a117a8ceb63b3bb19499dcea67420259f87f052f873be1cba5aff`。下一断点是情绪 `MAY_RECALL` 的 514／568 ms 尾部、provider 首 token 波动和语义修复；端到端实时门槛及人工人感仍未通过。
  - [x] `MAY_RECALL` 服务生命周期切片：route ledger 实测 63～125 ms、暖态 embedding 基准约 91 ms，但旧 composition 每轮新建 RecallFusionService／Chroma client，暖态构造仍需约 64～74 ms。`f0aa50a58` 改为 app-local 单例并复用 canonical EpisodicStore client，104 项回归通过；真实三条检索为 316／351／508 ms，中位数从 541 ms 降至 351 ms，route ledger 为 74～91 ms。收据 SHA-256 为 `e1c8a0f9110be096dbb8c2f29fec1a945dd52482b009a805f30f6c85f2fcafd8`。本子项关闭，但 508 ms 调度尾部仍留在父项继续处理。
  - [ ] 重新加载轻量阶段计时，分别读取 embedding、service resolve、route recall、两个领域 reviewer、通用偏好包和 context total；先用数据决定下一处删除或并行化，不以 `retrieval_wait_ms` 总数猜测。计时进入现有 trace，不新增 durable 审计写入；78 项非重复代码、集成与文档 Gate 已通过。
  - [x] 修复普通深情绪回合的模型车道漂移。`d1777f109` 实测一条安静陪伴消息的 provider 首 token 为 24,999 ms，模型错误升级为 `deepseek-v4-pro`，同轮 retrieval 仅 468 ms。`09e828149` 保留双候选与本地确定性选择，同时固定 `realtime_companion` 使用 `CHAT_SURFACE_REALTIME／Tier B`；110 项本地回归通过，真实三条均落到 Flash，provider 首 token 为 1,834～2,157 ms、首个 RENDER 为 3,360～4,263 ms。收据 SHA-256 为 `d383bf20246ed5f701ce72b9a66572bcf9eb9de0a23c62c9b86d683be2a4b9b0`。车道子项关闭；双候选评分与首句总门槛继续留在父项。

## P1：情感生命清零项

- [x] 在真实运行中观察兼容情感 writer 使用量，确认旧 writer 为 0。`4f4008060` 的 v2 收据包含 3 个 accepted Gateway turn；八张旧表前后均为 0，4 个旧 JSON 没有新增、删除或指纹变化；canonical Affect／Relationship／Life／Turn Delivery／Expression 分别 +8／+6／+22／+3／+12。报告 SHA-256 为 `b511144d4fad3a9574e703cd4e33a658d86a7bd4ec0b89dc0acb5daba4b36450`。
- [ ] 验证 profile apply → canary → rollback 会改变并恢复下一轮文本、TTS 和 Avatar。绑定 `e50841192` 的生产 Relationship/Profile/Fusion/Planner/Expression Delivery canary 已证明候选把文本 style policy、Voice rate `1.0 → 1.15`、Avatar motion `0.5 → 0.8` 写入下一轮 Host 协议，五次 canary 后保持并自动激活，rollback 恢复精确 baseline；收据 SHA-256 为 `acdf52ff5cfd6c6e53ff71f268273e2dcbaeb59bd906a1c614afdc5d572f3bde`。真实模型措辞和远端物理效果尚未执行，所以本项暂不勾选。
- [x] 验证长期运行状态有界，原始音视频和用户正文不会进入 affect journal。绑定 `0195d1968` 的隔离真实 SQLite／生产 AffectFusion 收据完成 1001 次时间推进、1026 条 affective event 和 Repository 重启重放；快照由 13,370 bytes 衰减至 9,281 bytes，raw sentinel 与禁止字段命中均为 0，SHA-256 为 `ddc09b5bce7a6974a58205634b0c7518d25e6cc113d7ebd7d53e06283f3ca584`。
- [x] 验证跨用户、workspace、session 泄漏为 0。同一收据使用四组交叉 scope，得到四个不同 aggregate；主 scope 的 1001 次推进没有改变其余三个 digest，scope violation 为 0。

## 完成定义

任务只有同时满足“生产调用点、真实环境、恢复／打断、体验指标、文档同步”才可勾选。静态合同、fixture、scripted replay 或单次样例不得替代目标环境验收。

最终发布状态仍为 `INCOMPLETE`，直到 [执行合同](NEXT_GENERATION_EVOLUTION_EXECUTION.md) 的退出条件全部满足。
