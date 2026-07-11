# M48-B Evidence

当前包含已批准的 Entry Gate 资产；TASK-0487 已解锁为 `PLANNED`，TASK-0488～0496 尚未实施。

- `M48B_ENTRY_GATE_PACKET.json`：所有权、migration、cutover、隔离 fixture 与 P0 控制的机器可读 SSOT。
- `M48B_ENTRY_GATE_PACKET.md`：人工评审视图。
- `M48B_ENTRY_GATE_MASTER_APPROVAL.md`：用户委托的本地实施审批与明确未授权边界。
- `M48B_ENTRY_GATE_RESULT.json`：fail-closed 机检结果；五项条款满足时为 `OPEN`。

执行：

```powershell
python tools/run_m48b_entry_gate.py --root . --pretty
```
