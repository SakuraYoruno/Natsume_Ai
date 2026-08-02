受众：MASTER
标题：Natsume AI 架构图索引

# 架构图索引

| 图 | 用途 | 当前性说明 |
|---|---|---|
| [运行拓扑](01_runtime_topology.md) | Host、OBS、Edge、Gateway 与外部服务 | 辅助图，以部署架构为准 |
| [主链数据流](02_main_chain_flow.md) | 入口到执行与交付 | 辅助图，以当前代码架构和数据流为准 |
| [召回数据流](03_dataflow_recall.md) | Memory／Knowledge 读取 | 专题图 |
| [执行层次](04_executive_layers.md) | 受管任务执行 | 只适用于有副作用路线 |
| [Tribe 拓扑](05_tribe_topology.md) | Agent／Tribe 协作 | 非普通聊天主链 |
| [输入输出路线](06_input_output_routes.md) | Gateway／Surface／Edge | 专题图 |
| [观测拓扑](07_observability_topology.md) | OBS 与 Monitor | 专题图 |

图表不是完成证明。若图与生产代码或 [机器状态](../../../../configs/release/current_architecture_status.json) 冲突，以后两者为准。七海情感主链的最新总图位于 [系统架构](../SYSTEM_ARCHITECTURE.md) 和 [数据流架构](../DATA_FLOW_ARCHITECTURE.md)。
