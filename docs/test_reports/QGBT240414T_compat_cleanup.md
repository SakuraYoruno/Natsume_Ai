# QGBT240414T 兼容层清理留痕（Graph Runtime / Checkpoint）

- 日期: 2026-04-14
- 目标: 清理 LangGraph 迁移后残留的旧兼容壳，减少“双路径”维护成本与误导。

## 本次清理内容

1. 移除 `CheckpointManager` 兼容类，改为函数式 helper：
   - 新增 `build_interrupt_signal(...)`
   - 新增 `check_resume_allowed(...)`
   - 保留 `HostPolicyGuard` 与 LangGraph `SqliteSaver` 单例能力

2. `GraphExecutor` 不再接收 `checkpoint_manager`：
   - 直接调用 `build_interrupt_signal(...)` 写入 airlock 中断元信息
   - 移除对 `_checkpoint_manager` 的内部状态依赖

3. `GraphRuntimeContract` 不再维护 `_checkpoint_manager`：
   - 改为 `self._policy_guard = HostPolicyGuard()`
   - `resume_graph` 使用 `check_resume_allowed(...)`
   - 删除创建 `GraphExecutor` 时的 `checkpoint_manager` 传参

4. 删除 `graph_runtime` 残留旧别名：
   - `_build_edge_map`
   - `_pick_model_profile_id`
   - `_build_model_param_hash`

5. 收紧 `graph_subgraph` 的僵尸兼容接口：
   - 删除 `compile_governed_subgraph(..., fallback_map=...)` 的无效参数
   - 删除未被引用的 `write_agent_artifact_to_payload` 兼容别名

6. 注释同步：
   - `graph_contract` 与 `obs_routes_stats` 去掉 `CheckpointManager` 描述

## 影响文件

- `host_brain/integrations/graph_checkpoint.py`
- `host_brain/integrations/graph_executor.py`
- `host_brain/integrations/graph_runtime.py`
- `host_brain/integrations/graph_subgraph.py`
- `host_brain/integrations/graph_contract.py`
- `host_brain/app/obs_routes_stats.py`

## 验证结果

1. 全局引用回扫：
   - `CheckpointManager`：0 处
   - `write_agent_artifact_to_payload`：0 处
   - `_build_edge_map/_pick_model_profile_id/_build_model_param_hash`：0 处

2. 编译级自检（通过）：

```powershell
python -m compileall host_brain/integrations/graph_checkpoint.py `
  host_brain/integrations/graph_executor.py `
  host_brain/integrations/graph_runtime.py `
  host_brain/integrations/graph_subgraph.py `
  host_brain/integrations/graph_contract.py `
  host_brain/app/obs_routes_stats.py
```

3. 结论：
   - 旧 Checkpoint 兼容壳已移除，执行路径统一为 `LangGraph SqliteSaver + HostPolicyGuard`。
   - 清理范围为“代码路径级”清理，未改变业务路由策略本身。
