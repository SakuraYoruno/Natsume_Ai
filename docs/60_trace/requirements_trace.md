受众：PROJECT
标题：七海数字生命需求追溯

# 需求追溯

状态只描述当前 commit 的证据范围。`WIRED` 不等于最终真实体验通过。

| 需求 | 生产实现 | 主要测试／证据 | 状态 |
|---|---|---|---|
| DL-FR-01 自我／用户情绪隔离 | `digital_life/affect_fusion.py`、`affective_turn_planner.py`、life contracts | affect separation 定向单测与合同测试 | `WIRED` |
| DL-FR-02 Appraisal 与混合情绪 | `digital_life/domain_reducer.py`、`reducer.py` | reducer replay、episode 边界测试 | `WIRED / PARTIAL` |
| DL-FR-03 关系与修复连续性 | `relationship_lifecycle.py`、relationship contracts | restart、duplicate、repair tests | `WIRED / UNPROVEN_LONG_TERM` |
| DL-FR-04 流式文本与打断 | `surface_runtime/affective_streaming.py` | streaming、cancel、delivery tests | `WIRED / PARTIAL` |
| DL-FR-05 三模态一致 | affective turn／delivery contracts、Edge embodiment | 本机合同；目标设备未测 | `NOT_TESTED_EDGE` |
| DL-FR-06 主动陪伴 | `companion_proactive.py`、permission ingress | policy 与拒绝持久化测试 | `WIRED / PARTIAL` |
| DL-FR-07 偏好成长／回滚 | `companion_profile_lifecycle.py`、feedback ingress | version/canary/rollback tests | `WIRED / UNPROVEN_BEHAVIOR` |
| DL-FR-08 非操控边界 | Surface quality guards、Digital Life policy | 固定安全场景 | `LOCAL_PASS / HUMAN_REVIEW_PENDING` |
| DL-FR-09 轻量普通聊天 | Surface realtime path 与 governed routing | 路由／延迟定向测试 | `WIRED / PERFORMANCE_PENDING` |
| DL-FR-10 真实 TTS／Avatar | GPT-SoVITS V4 B config、Edge runtime | 本机 CUDA语音通过；远端未测 | `LOCAL_PASS / EDGE_PENDING` |
| DL-NFR-01 exact replay | Life journal／reducer | 1001+ event、CAS、restart tests | `LOCAL_PASS` |
| DL-NFR-02 交付幂等 | affective delivery、Task outbox、Gateway transport | duplicate/cancel/replay tests | `LOCAL_PASS / FULL_STACK_PENDING` |
| DL-NFR-03 隐私 | derived observation、bounded journal | raw media／cross-scope tests | `CONTRACT_READY / REAL_RUN_PENDING` |
| DL-NFR-04 实时指标 | Surface、provider、Edge pipeline | 非缓存端到端性能测试 | `INCOMPLETE` |
| DL-NFR-05 人工体验 | 48 条固定多轮轨迹 | 至少 3 人盲测 | `NOT_RUN` |

## 发布追溯规则

- 每份证据绑定 commit SHA、环境和输入集合。
- mock、fixture 和 scripted replay 只能支持 `CONTRACT_READY` 或局部回归结论。
- 外部模型收益和延迟必须绑定真实 provider、usage 和 transport identity。
- Edge 能力必须由目标设备真实回执支持。
- 历史归档中的完成结论不会自动继承到当前版本。
