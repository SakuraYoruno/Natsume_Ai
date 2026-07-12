# M48-B Evidence

当前包含已批准的 Entry Gate 资产与 TASK-0487～0495 交付证据；TASK-0487、TASK-0488、TASK-0493 已 `DONE`，TASK-0489～TASK-0492、TASK-0494～0495 为 `IMPLEMENTED_AWAITING_PROTECTED_HANDOFF`。TASK-0496 Safety Seal `BLOCKED` 于 protected production handoff、TASK-0486 Release Seal 与 MASTER 批准。

- `M48B_ENTRY_GATE_PACKET.json`：所有权、migration、cutover、隔离 fixture 与 P0 控制的机器可读 SSOT。
- `M48B_ENTRY_GATE_PACKET.md`：人工评审视图。
- `M48B_ENTRY_GATE_MASTER_APPROVAL.md`：用户委托的本地实施审批与明确未授权边界。
- `M48B_ENTRY_GATE_RESULT.json`：fail-closed 机检结果；五项条款满足时为 `OPEN`。
- `task0487/`：Secure Ingress identity/security state/catalog compatibility 的实现与验证证据。
- `task0488/`：Gateway raw-body verification、durable inbox/outbox/ack/DLQ/reconciliation 与生命周期验证证据。
- `task0489/`：Obs Query-only boundary、16-family Secure command plane、outcome journal/owner/drain、隔离验证与 protected runtime handoff 证据。
- `task0490/`：Skill/Provider 严格签名验证、content-addressed physical quarantine、0060 durable state、STAGED-only intake、legacy import/install 封口与 protected production handoff 证据。
- `task0491/`：真实 0058 principal/evidence、Governor token intersection、Lab-only choke points、0061 strict RunStore、legacy Pentest 封口与 protected route/composition handoff 证据。
- `task0492/`：17-endpoint typed Service Catalog、18770 双 owner/public bind fail-closed、candidate-only startup gate、one-shot reservation composition 与 protected startup handoff 证据。
- `task0493/`：Edge contracts-only AST guard、122 条内容寻址 finding、54 个 TASK-0581 迁移组、动态 loader/callable flow 封口与不可降级 Delivery Gate 证据。
- `task0494/`：Mate/Desktop/WeChat Bridge default-off、专属 HMAC/scope/rate/replay/audit、bounded admission/queue、remote approval 与 protected producer/startup/verifier handoff 证据。
- `task0495/`：五域 `ISOLATED_ONLY` golden/fault matrix、no-real-I/O guard、provider fail-closed construction、精确 durable claim/result/audit 语义与 protected Safety Seal handoff 证据。

执行：

```powershell
python tools/run_m48b_entry_gate.py --root . --pretty
```
