# M48-B Evidence

当前仅包含 Entry Gate 准备资产；TASK-0487～0496 尚未实施。

- `M48B_ENTRY_GATE_PACKET.json`：所有权、migration、cutover、隔离 fixture 与 P0 控制的机器可读 SSOT。
- `M48B_ENTRY_GATE_PACKET.md`：人工评审视图。
- `M48B_ENTRY_GATE_RESULT.json`：fail-closed 机检结果；MASTER 未批准时必须为 `BLOCKED`。

执行：

```powershell
python tools/run_m48b_entry_gate.py --root . --pretty
```
