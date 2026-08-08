受众：PROJECT
标题：Natsume AI 当前架构缺口矩阵

# 当前架构缺口矩阵

整体状态：`INCOMPLETE`。本表只列当前仍影响数字生命体验或真实交付的缺口。

| 优先级 | 能力 | 当前状态 | 剩余缺口 | 完成证据 |
|---|---|---|---|---|
| P0 | 仓库与文档收口 | `CLOSED` | 无；#194 与 #209 已合入，临时分支已清理 | clean `dev` + PR #209 + 文档 Gate |
| P0 | 日语情感语音 | `EDGE_LIVE_REFERENCE_CONDITIONED` | V4 B 已在远端 CUDA Edge 完成真实合成与播放；仍缺专属微调 checkpoint 和完整情绪覆盖 | Edge terminal receipt + 音频试听 |
| P0 | Voice／Avatar 同步 | `NOT_TESTED` | VTS/VRM、barge-in、断线与时间偏差 | 真实 synchronization receipt |
| P0 | 真实对话延迟 | `PARTIAL` | Provider 首字和首段 TTS 指标待优化 | 非缓存 provider performance run |
| P0 | 人工体验 | `NOT_RUN` | 48 条轨迹、3 人盲测未完成 | 冻结样本与匿名评分 |
| P1 | 长期关系连续性 | `WIRED / UNPROVEN` | 多日、重启、裂痕／修复自然度不足 | 真实多日轨迹 |
| P1 | Profile 真实激活 | `WIRED / UNPROVEN` | apply／canary／rollback 对三模态的真实影响 | 行为 diff + exact rollback |
| P1 | NE-06 版本化表达偏好 | `LIVE_CAUSAL_PASS_SCOPE_BOUND / HUMAN_AND_OTHER_NE06_PENDING` | `7ab64a246` 的 Docker Lab→Review→真实 Host apply→最终 prompt→exact rollback 19 项全过，正式收据 `8431de7c...`；265 项回归通过，单样本人感与 Knowledge／Identity 在线行为另验 | 扩大表达偏好样本盲测；Knowledge／Identity 真实 apply／rollback；远端 Voice／Avatar |
| P1 | NE-06 Knowledge／Identity 在线行为 | `IDENTITY_LIVE_CAUSAL_PASS / KNOWLEDGE_FINAL_PROMPT_RETEST_PENDING` | `c495d0934` 第三轮中 Identity 与两类 authority 终态全过；Knowledge 唯一红项是 runner 误读生成前 reply plan。当前改为最终 ModelAttempt 的 secret-free entry-set digest／count，收据 `c42529...` 保留为失败证据 | 新提交联合重跑并绑定正式收据；人工体验与远端 Voice／Avatar 另验 |
| P1 | 旧情感 writer 清零 | `METERED` | 需目标运行量证明使用为 0 | 运行计数与删除 PR |
| P1 | 完整服务栈 | `PARTIAL` | Docker、Host、OBS、Monitor、Gateway 同时验收 | full-stack acceptance receipt |
| P1 | 故障恢复 | `PARTIAL` | Edge／网络／未知 effect／回滚故障联合注入 | restart and reconciliation receipt |
| P1 | 隐私与隔离 | `CONTRACT_READY` | 真实数据和多 session 检查待执行 | leak/tamper/delete tests |
| P2 | 外部消息平台 | `OPTIONAL` | 凭据可用时逐项测试 | platform-specific receipt |

## 2026-08-04 已关闭的真实观测缺口

| 能力 | 状态 | 真实证据 | 仍不代表 |
|---|---|---|---|
| Life Body 人格上下文 | `CLOSED_LOCAL_REAL_STACK` | SQLite canonical `hard_state` → OBS Sidecar → Nginx Monitor；真实浏览器无“待连接／待恢复” | 长期人格自然度已通过 |
| Gateway 当前闭环指标 | `CLOSED_LOCAL_REAL_STACK` | 三条签名真实请求最新有效桶 Reply Outbox／Closed Loop／Completion 均为 100% | 外部平台物理 ACK 或全历史无失败 |
| Capability Forge 顶部错误横幅 | `CLOSED_LOCAL_REAL_STACK` | 真实 backend browser E2E 与生产构建通过 | 通用 Skill/Lab 全部完成 |

## 已经收敛的关键方向

- 普通聊天不再强制走重型 TaskGraph 审计。
- 用户观察不再直接成为七海情绪。
- canonical Life journal 成为情感与关系的主要写 authority。
- 纯文本路径不再把情绪重置为 `NEUTRAL`。
- 声线从猎奇的中文跨语种方案转为用户确认的日语 GPT-SoVITS V4 B。

这些结果仍不等于完整体验完成；最终结论由 PRD 指标和真实验收决定。
