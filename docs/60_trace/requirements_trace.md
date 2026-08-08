受众：PROJECT
标题：七海数字生命需求追溯

# 需求追溯

状态只描述当前 commit 的证据范围。`WIRED` 不等于最终真实体验通过。

| 需求 | 生产实现 | 主要测试／证据 | 状态 |
|---|---|---|---|
| DL-FR-01 自我／用户情绪隔离 | `digital_life/affect_fusion.py`、`affective_turn_planner.py`、life contracts | 四 scope／1001 推进真实 SQLite 收据 | `LOCAL_REAL_SQLITE_PASS` |
| DL-FR-02 Appraisal 与混合情绪 | `digital_life/domain_reducer.py`、`reducer.py` | reducer replay、episode 边界测试 | `WIRED / PARTIAL` |
| DL-FR-03 关系与修复连续性 | `relationship_lifecycle.py`、conversation spine、interaction thread、relationship contracts | 6 次真实 Host 替换、revision／aftereffect 精确关联；user→assistant 4→6 条精确恢复；typed 指代 trace 与人工逐句复核 | `REAL_RESTART_STRUCTURAL_PASS / CONVERSATION_SEQUENCE_REAL_PASS / DEICTIC_THREAD_REAL_PASS / MODEL_WORDING_GENERIC / HUMAN_CONTINUITY_FAIL` |
| DL-FR-04 流式文本与打断 | `surface_runtime/affective_streaming.py` | streaming、cancel、delivery tests | `WIRED / PARTIAL` |
| DL-FR-05 三模态一致 | affective turn／delivery contracts、Edge embodiment | 本机合同；目标设备未测 | `NOT_TESTED_EDGE` |
| DL-FR-06 主动陪伴 | `companion_proactive.py`、permission ingress | policy 与拒绝持久化测试；真实 outbox 需目标 Edge | `WIRED / REAL_DRIVER_NOT_RUN_REMOTE_EDGE_OFF` |
| DL-FR-07 偏好成长／回滚 | `companion_profile_lifecycle.py`、feedback ingress | 真实 SQLite + 生产 Planner／Expression Delivery 收据 | `HOST_BEHAVIOR_PASS / MODEL_EDGE_PENDING` |
| DL-FR-07a NE-06 表达版本激活 | `companion_expression_experiment.py`、`version_activation_feedback.py`、Surface app-local expression projection port、compact prompt directive | `7ab64a246` v3 正式收据 `8431de7c...`：19 项真实 Docker／Review／Host／directive digest／prompt delta／exact rollback 全过；265 项回归 | `LIVE_CAUSAL_PASS_SCOPE_BOUND / HUMAN_EXPERIENCE_UNPROVEN` |
| DL-FR-07b NE-06 Knowledge／Identity 在线行为 | Agent Runtime active Knowledge read port、Surface Identity release directive、V2 activation authority、action-scoped Lab→exact proposal、最终 ModelAttempt Knowledge set evidence | `1e6addc1` 正式收据 `05b95b...`：31/31，8 Docker arm、4 Review、6 真实 DeepSeek Host；Knowledge prompt/deprecate 与 Identity exact rollback 全过 | `LIVE_CAUSAL_PASS_SCOPE_BOUND / HUMAN_EXPERIENCE_UNPROVEN` |
| DL-FR-08 非操控边界 | Surface quality guards、Digital Life policy | 固定安全场景 | `LOCAL_PASS / HUMAN_REVIEW_PENDING` |
| DL-FR-09 轻量普通聊天 | Surface realtime path 与 governed routing | 路由／延迟定向测试 | `WIRED / PERFORMANCE_PENDING` |
| DL-FR-10 真实 TTS／Avatar | GPT-SoVITS V4 B config、Edge runtime | 本机 CUDA语音通过；远端未测 | `LOCAL_PASS / EDGE_PENDING` |
| DL-NFR-01 exact replay | Life journal／reducer | 1001+ event、CAS、restart tests | `LOCAL_PASS` |
| DL-NFR-02 交付幂等 | affective delivery、Task outbox、Gateway transport | duplicate/cancel/replay tests | `LOCAL_PASS / FULL_STACK_PENDING` |
| DL-NFR-03 隐私 | derived observation、bounded journal | raw media／cross-scope／restart 真实 SQLite 收据 | `LOCAL_REAL_SQLITE_PASS` |
| DL-NFR-04 实时指标 | Surface、provider、Edge pipeline | 非缓存端到端性能测试 | `INCOMPLETE` |
| DL-NFR-05 人工体验 | 48 条固定多轮轨迹 | 至少 3 人盲测 | `NOT_RUN` |

## 发布追溯规则

- 每份证据绑定 commit SHA、环境和输入集合。
- mock、fixture 和 scripted replay 只能支持 `CONTRACT_READY` 或局部回归结论。
- 外部模型收益和延迟必须绑定真实 provider、usage 和 transport identity。
- Edge 能力必须由目标设备真实回执支持。
- 历史归档中的完成结论不会自动继承到当前版本。
