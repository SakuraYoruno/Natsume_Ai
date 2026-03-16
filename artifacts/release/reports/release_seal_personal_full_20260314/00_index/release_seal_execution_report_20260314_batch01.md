# 个人全功能封测执行报告（batch-01）

## 1. 基本信息

| 项 | 填写 |
|---|---|
| batch_id | 20260314_batch01_health_gate |
| exec_date | 2026-03-14 |
| operator | Codex |
| environment | historical single-node bootstrap diagnosis only, invalid for formal LAN dual-node seal test |
| scope | 个人全功能版 |
| final_decision | BLOCK |

## 2. 总结

1. 本轮只执行第一批基础门禁：`F-01 ~ F-03`。
2. 本轮适用项数量：`3`
3. 通过数量：`0`
4. 阻断数量：`3`
5. 当前结论：`该批次仅为历史 bootstrap 排障记录，不纳入正式双节点封测结论`

## 3. 阻断项

| ID | 问题 | 影响 | 证据路径 | 修复建议 | 复测结论 |
|---|---|---|---|---|---|
| F-01 | Edge 健康快照为 `degraded`，`tts=UNLOADED`，`vts=DISCONNECTED`，`host-scheduler.running=false` | 无法进入真实语音/VTS 白盒验证 | `10_health_runtime/F-01_health_summary.md` | 先恢复语音运行时与 VTS 连接，再重跑 F-01 | pending |
| F-02 | 历史 bootstrap 排障阶段曾依赖本机覆写，且启动仍报调度器注册异常 | 启动口径不稳定，不能纳入正式双节点封测 | `10_health_runtime/F-02_config_keys.md` | 仅保留为排障记录；正式封测只看 LAN 双节点结果 | pending |
| F-03 | 容器依赖已修复，但真实召回和语音依赖仍未就绪 | 后续 Chat/Recall/Voice 链路证据不可信 | `10_health_runtime/F-03_dependency_readiness.md` | 关闭 semantic recall mock，补齐 `models/gpt_sovits`、`pyvts` 与 VTube Studio 可执行文件后再重跑 | pending |

## 4. 质量与时延摘要

| 项 | 结果 | 结论 |
|---|---|---|
| 文本质量 | 未执行 | 被 F-01~F-03 阻断 |
| 召回质量 | 未执行 | semantic recall 仍是 mock 风险 |
| 压缩质量 | 未执行 | 需先恢复真实主链路 |
| 总结质量 | 未执行 | 需先恢复真实主链路 |
| Chat 时延 | 未执行 | 需先恢复真实主链路 |
| 场景矩阵时延 | 未执行 | 需先恢复真实主链路 |

## 5. 白盒观察摘要

| 项 | 观察结论 | 证据路径 |
|---|---|---|
| TTS 实际可听 | 未开始 | 被 `F-01` 阻断 |
| VTS 表情真实可见 | 未开始 | 被 `F-01` 阻断 |
| VTS 口型同步真实可见 | 未开始 | 被 `F-01` 阻断 |
| WebUI 关键操作真实可见 | 未开始 | 待后续批次 |
| Vision 自动化动作真实可见 | 未开始 | 待后续批次 |

## 6. 最终放行意见

1. 当前不满足 `F-01 ~ F-33` 的起跑条件。
2. 当前存在未解释降级：`edge.health=degraded`。
3. 当前存在隐式短路风险：`SEMANTIC_RECALL_USE_MOCK` 未显式关闭。
4. 当前不存在可接受的语音/VTS 白盒证据。
5. 本批次属于历史 bootstrap 排障记录，不构成正式双节点放行证据。
6. 结论：`BLOCK`。
