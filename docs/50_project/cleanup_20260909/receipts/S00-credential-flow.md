# S00：Pentest 凭据生命周期与 Vault 取证

状态：`EVIDENCE_ONLY`；结论为 `HOLD`。本收据没有删除、迁移或替换任何运行时代码。

范围是任务卡指定的 Pentest 内核、证据、campaign memory、策略和相邻执行器的静态与
纯内存验证。没有读取真实凭据、启动 Host/Edge/靶场、发起网络请求或调用任何 Pentest
执行器。

## 结论

当前有两个名称相近但职责不同的实现，不能合并为“未使用 Vault”后直接删除：

| 项目 | 已证实的事实 | 清理判断 |
|---|---|---|
| `CredentialVault` 类 | 仅找到类定义（`autonomous_kernel.py:110`），未找到静态构造、`encrypt` 或 `decrypt` 调用。它在未配置主密钥时以 engagement ID 派生可预测默认密钥，并在缺少 `cryptography` 时回退 XOR。 | 不得删除。静态零消费者不能排除反射、历史密文或外部集成；而且它与实际执行路径不是等价实现。 |
| `PentestAgentKernel.credential_vault` 字典 | 从 `context` 接收或新建后写回 context（`autonomous_kernel.py:189-191`）；初始凭据和运行期收集的密码、NTLM hash、SSH 私钥或 ticket 路径都写入其中（`1794-1849`、`1851-1927`）。多个真实工具执行器按 `credential_ref` 从该字典取材料。 | 必须保留能力，不能按“没有 `CredentialVault` 消费者”删除或替换为拒绝壳。 |
| Attack graph / report | 图节点主要保存引用与元数据，但初始和运行期路径会把 `ntlm_hash` 写入图属性（`1840-1841`、`1909-1916`）；最终结果无条件输出 `attack_graph`（`1409-1437`）。 | 这是材料泄露面，不是删除能力的理由；需要单独的兼容迁移设计。 |
| Campaign memory | `EvolutionCore.persist` 将运行期映射交给 `store_attack_memory`（`evolution_core.py:502-520`）。该已审阅路径仅持久化 username、protocol、source target 和 credential-ref 的 SHA-256 摘要，不写 password/NTLM hash/SSH 私钥（`campaign_memory.py:183-238`）。 | 保留；它是经裁剪的经验记忆路径，不是未使用兼容层。 |

## 实际数据流

```text
初始/运行期候选中的材料
  -> Kernel 的 credential_vault（同一 run context 的内存字典）
  -> AttackGraph：credential_ref + 元数据
       └─ 当前还会携带 ntlm_hash / ticket_cache_path
  -> ActionPolicy：按图生成 credential_ref
  -> 已授权工具调用的 _react_context
  -> tool_executors：按 ref 取回材料后执行

同一映射
  -> EvolutionCore.persist
  -> campaign_memory：仅模式元数据 + ref digest
```

运行期映射是工具能力所需的材料源。例如，通用 resolver
`tool_executors._credential_pairs_from_args` 在 `858-897` 直接从 `_react_context` 中的
映射按 ref 取出 username/password/protocol；AD、Kerberos、SSH 等执行器还有各自的同类
读取点。`phase4_executor.py:1101-1140` 也从 context 提取凭据。删去映射会让已授权的
受控执行失去能力。

resolver 本身不做本地 target/protocol 过滤；它信任传入的已批准参数。这不能被误解为
“任意跨目标使用都已获准”：横向移动本来需要跨目标使用凭据。目标、动作和参数必须继续
由上游既有的 manifest/authority/LabCapability 约束；本次没有也不应建立第二套 Pentest
审批或 Skill 体系。P00 已记录统一 Skill 入口通过既有授权适配器到 LabCapability 的链路。

## 脱敏与持久化边界

`redact_secrets` 会遮蔽 `password` 和 `credential_ref` 键，但敏感键正则没有覆盖
`ntlm_hash`。`EngagementManifest.from_dict` 要求提供 `evidence_policy`；不过显式
`"evidence_policy": {}` 是合法的，而内核只在 `redact_secrets` 为真时脱敏
action history、evidence、event、manifest 和 rejected actions（`1021-1032`、
`1178-1185`、`1247-1254`、`2532-2533`、`2599-2610`）。

即使该开关为真，最终结果中的 `attack_graph` 仍直接调用 `self.graph.to_dict()`；不会经过
`redact_secrets`。因此图中的原始 `ntlm_hash` 会进入返回结果。工具规格还允许部分受控
工具以直接 `password`、`ntlm_hash` 或 `ssh_private_key` 参数工作；当显式关闭脱敏时，原始
action history 也可能携带这些参数。

这项发现不等于已经证明所有输出通道都泄露，也不改变实验室开关、授权或实际执行行为；它
只证明了上述已审阅的数据结构和序列化路径无法满足“材料永不进入返回/审计对象”的更强
约束。

## 合成验证与既有测试

所有探针仅使用内存中的合成字符串，且只输出布尔结论：

- `redact_secrets` 对 `password` 与 `credential_ref`：通过。
- `redact_secrets` 对 `ntlm_hash`：未遮蔽。
- `AttackGraph.to_dict()`：保留合成 `ntlm_hash` 属性。
- 显式空 `evidence_policy`：可被 manifest 接受。
- 为另一合成 target 传入 `credential_ref`：resolver 仍可解析，证明它没有独立的
  target/protocol 过滤；这不是授权绕过测试。
- `py -3 -B -m pytest --noconftest -q tests/unit/test_pentest_autonomous_evidence.py tests/unit/test_pentest_domain_runtime_regressions.py tests/unit/test_pentest_agent_loop_contracts.py`：`30 passed`。

## 后续必须单独设计，不能在本卡实施

1. 保留当前能力的前提下，定义“运行期材料存储”契约：图、报告、memory、事件和审计只携带
   opaque ref/存在性/受控摘要；执行器在已密封的授权范围内取得临时材料，并在运行结束或
   取消后的确定时点清理。
2. 将 NTLM hash、票据路径和其他可重放认证材料纳入导出/审计边界的强制脱敏；先证明执行器
   与需要原始结果的内部决策发生在脱敏之前。
3. 在检查反射、外部部署、历史密文和回滚兼容后，才能决定 `CredentialVault` 是迁移到受支持
   存储还是退役。不能让它的 XOR 或可预测默认密钥成为新的正式兼容格式。
4. 为上述变更新建经批准的设计/实现卡，并加入正向能力、拒绝授权、跨 target 的合法横移、
   重放、取消和持久化兼容测试。S00 本身不解锁任何删除。
