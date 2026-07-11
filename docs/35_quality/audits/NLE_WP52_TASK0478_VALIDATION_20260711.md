受众：PROJECT
标题：TASK-0478 Appraisal 与 Bond 单写收敛验证报告

# TASK-0478 / LTE-WP-52 验证报告

版本：v1.0
日期：2026-07-11
状态：`PASSED`
基线提交：`a285983`

## 1. 结论

TASK-0478 已新增 `Natsume.appraisal.record.v1` 纯适配器与 `Natsume.bond.projection.v1` 只读 read model。既有 `EmotionalProactiveEpisode` / `EmotionalProactiveAftereffect` 可确定性回放为 `event → appraisal → affect/drive → episode/expression → optional bond signal` 引用链；adapter 不写 Life、VAD、inner weather 或 Bond，也不持久化第二套情绪状态。

BondProjection 从 detached `BondStageEngine.peek_state()`、既有 texture/rift、SocialBrain vector/repair 与可选 Appraisal boundary signal 构建。score/stage 明确标记为 `projection_only`、`authoritative=false`；未知用户读取不创建 INIT state。用户 boundary 只投影为 `WITHDRAW_PROACTIVE_EXPRESSION`，`permission_effect=NONE`，不会自动改变关系权限。

既有 `POST /bond/event` 和两个真实闭环验证器已从直接 `BondStageEngine.apply_event()` 切到 `BondDynamicsWriter.apply_update()`。T-SSOT-01 static scan 现同时拒绝生产源码绕过 command 调用 stage write，以及在 BondDynamics/BondStageEngine 之外直接修改 `total_score`、`current_stage` 或 `rift`。Neo4j runtime 仍校验唯一 `BOND_WRITER_ID` 与非空 evidence refs。

## 2. 决策边界

- 本任务没有创建平行 BondState、Affect 或关系权限真源；Appraisal/Bond 都是只读适配或 projection。
- BondDynamics 仍是 M48 唯一受治理 command path；stage/score、rift/repair 只在该命令成功且 evidence 非空后推进。
- Appraisal boundary 是表达收紧信号，不是关系权限 mutation；任何后续权限语义变化仍需 MASTER Review。
- `BondStageEngine.peek_state()` 返回 deep copy，未知用户不初始化；legacy GET stage/summary API 的既有初始化语义未在本任务重写。
- 未新增 schema 或迁移，未执行生产数据库、Neo4j 或 Chroma 写入。

## 3. 变更范围

| 路径 | 动作 | 说明 |
|---|---|---|
| `host_brain/persona/appraisal_adapter.py` | ADD | AppraisalRecord、确定性 digest/id、episode/aftereffect adapters、reference-chain replay validator |
| `host_brain/persona/emotional_proactive.py` | MODIFY | episode/aftereffect 增加只读 `to_appraisal_record()` 适配入口 |
| `host_brain/bond/bond_projection.py` | ADD | score/stage 兼容、texture/rift/repair/boundary/vector 只读 projection 与 facade |
| `host_brain/bond/bond_stage_engine.py` | MODIFY | 非空 evidence fail-closed；detached、无 INIT side effect 的 `peek_state()` |
| `host_brain/tribe/social_brain_agent.py` | MODIFY | 显式 snapshot 的纯 vector adapter 与 BondProjection read facade；兼容 `CONFIDANT` stage |
| `host_brain/app/bond_routes.py`、`dependencies.py`、`router_registry.py` | MODIFY | `POST /bond/event` 统一经受治理的持久 writer provider |
| `tools/static_scan/bond_ssot_scan.py` | MODIFY | 扩展 stage command 与直接 score/stage/rift mutation 边界扫描 |
| `tools/doc_lint/doc_freshness_gate.py` | MODIFY | scoped task gate 只 timestamp 本次触发源；`--check-all` 仍保持全源扫描，消除逐文件 `git log` 延迟 |
| `tools/validate_*loop.py` | MODIFY | fixture 验证器也经 BondDynamics command，不保留旁路样例 |
| `tests/contract/test_nle_appraisal_bond_contract.py` | ADD | T-NLE-16 replay、单写、rift/repair/boundary、read-only/disabled/detached 对抗合同 |

## 4. 验证证据

- T-NLE-16 聚焦合同：`6 passed`。
- M48-A NLE 合同族：`187 passed`，仅既有 invalid-escape DeprecationWarning。
- Bond SSOT/static contracts：`9 passed`；production `bond_ssot_scan.py`：`0 violations`。
- Bond routes/dynamics/vector integration：`37 passed`；Host Bond API 集成：`12 passed`。
- SocialBrain/Bond stage/emotion unit：`44 passed`；Emotional Proactive：`20 passed`；NLE boundary gates：`44 passed`。
- Surface Memory → Emotion → Bond fixture 闭环：`passed=true`，`bond_update_applied=true`。
- 文档 audience/delivery scoped gates：通过；freshness scoped gate：`3 checked / 3 skipped / 0 failures`，对应单元测试 `8 passed`。
- `py_compile`：所有变更 Python 文件通过；`git diff --check`：通过。

环境依赖诊断未伪装为绿色：

- `tests/integration/test_bond_queue_summarizer_prod_env.py`：`1 passed, 2 failed`；失败原因是当前 prod 配置缺少 `BOND_NEO4J_AUTH_REF`，未获授权创建或读取生产密钥。
- `tests/unit/test_bond_phase5.py`：`58 passed, 11 failed, 1 teardown error`；失败均来自当前 `.env` 指向的 Chroma `http://127.0.0.1:8000` 返回 502，不是本任务代码断言差异。
- Inner World fixture 的 Bond 更新检查通过，但一个既有 `surface_uses_recalled_memory` 文本检查为 false；不属于 T-NLE-16 Gate，已保留原始失败而未改写测试口径。
- 全仓 audience/delivery 扫描仍报告 3 个未触碰的历史文档缺少合法 audience header（`CODE_FACTS_BASELINE_2026-06-30.md`、旧 superpowers plan、Pentest report）；本任务 7 个变更文档的 scoped audience/delivery gate 为 0 failure。
- 本解释器未安装 `ruff`；以 `py_compile`、契约测试、static scan 与 diff gate 作为本任务可用静态证据。

## 5. 回滚

停止调用 `AppraisalAdapter` / `BondProjectionReadModel`，或以 `enabled=false` 返回 disabled Bond snapshot，即可关闭新增 read model。`POST /bond/event` 必须继续保留 BondDynamics command 路径，不得回滚为 stage direct call；T-SSOT-01 门禁也应保留。由于没有新增表、没有生产迁移、没有关系权限 mutation，回滚不需要数据修复。
