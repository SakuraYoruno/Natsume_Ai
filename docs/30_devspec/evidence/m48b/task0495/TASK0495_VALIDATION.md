受众：CODEX / MASTER
标题：TASK-0495 P0 平台 golden、provider 构造负例矩阵与故障注入验证记录

# TASK-0495 验证记录

状态：`IMPLEMENTED_AWAITING_PROTECTED_HANDOFF`。实现 commit：`beb20c46ea4fe952577aa299d52b6ce6e48a7897`。

## 已完成

- 建立不可变的 `ISOLATED_ONLY` 五域验证 profile：Gateway、OBS、Skill/Provider、Pentest、Bridge 各自使用测试 principal、测试 secret、非生产 channel 和受 harness root 约束的状态目录。
- `NoRealIOGuard` 封锁 socket/DNS/send、subprocess/multiprocessing、shell/spawn/exec/fork 与 Windows `CreateProcess`；只允许登记后的 asyncio self-pipe 控制 socket。所有期望效果均为 `executed=false` 的 recording-only receipt，任何逃逸立即失败。
- Gateway→Host 使用真实 raw-body verifier、0059 durable inbox、Edge signed reporter、Host Secure Ingress 与进程内 HTTP；实际 payload identity/source/platform 被验证，重复与篡改均无第二效果。
- OBS 使用真实 migration、composition、HMAC ingress、outcome journal 与重启 replay；调用方伪造 authority 被覆盖。首次、重复、重启必须精确复用 `SUCCEEDED/EXECUTED` 终态和 result receipt，缺字段或未知值即失败。
- Skill/Provider 使用 0060 durable quarantine/STAGED intake；重复幂等、篡改拒绝，import/install/marker effect 均为零。Pentest 使用 0058/0061、真实 token intersection 与 RunStore，只有 Lab recording executor 执行一次。
- Bridge golden 只走 Mate 与 WeChat 的已拥有安全路径，各记录一次；replay/tamper 不产生第二效果，不启动 Desktop Node、监听器、窗口、游戏或微信。
- Host provider factory 与 LLM Gateway 改为显式 mock-only。真实 OpenAI/Gemini/DeepSeek/MiniMax/Codex 构造成功或返回稳定 unavailable reason；缺 key、disabled、unknown、constructor exception、call exception、malformed fallback 均不再静默伪造模型输出。
- OBS composition 与 Mate Bridge construction 的异常只暴露稳定 reason，不泄漏原始异常或 secret。

## 每个 fresh cycle 的精确投影

| 域 | recording effect | durable claim | audit | 结果 |
|---|---:|---:|---:|---|
| Gateway→Host | 1 | 2 | 2 | `GATEWAY_HOST_GOLDEN_COMPLETED` |
| OBS | 1 | 1 | 5 | `OBS_COMMAND_COMPLETED` |
| Skill/Provider | 0 | 1 | 2 | `SUPPLY_CHAIN_INTAKE_STAGED` |
| Pentest | 1 | 2 | 3 | `PENTEST_SECURE_EXECUTION_COMPLETED` |
| Bridge | 2 | 2 | 7 | `BRIDGE_GOLDEN_COMPLETED` |

同一原子测试在两个全新状态根运行，两个投影完全相等，所有 `uncertain=false`。Gateway/OBS 不只校验数量：claim 必须为 `COMMITTED`、result receipt 必须被重复/重启复用，audit 的 claim/decision/reason/stage/`side_effects_executed=0` 必须匹配精确多重集。

## 验证结果

| 层 | 结果 |
|---|---|
| focused unit/model/security | `172 passed in 5.04s` |
| 最终 changed-boundary atomic E2E | `1 passed in 13.82s` |
| static | 全部 changed Python 编译通过；最后两个 closure 文件再次编译；`git diff --check` 通过 |
| 独立复审 | 初审 7 个 P1、终审 2 个 P1 均已修复；最终三路 closure review 无剩余 P0/P1 |

原子 E2E 共执行 8 次，3 次完整通过，第一次没有通过。前四次失败依次暴露并修正：0059 只持久化 idempotency digest 的测试误期望、OBS surface id 误用、Windows quarantine 长路径、Bridge `audit_ready` 计数遗漏。第 5 次完整通过。终审新增 fail-closed terminal/certainty 与 durable semantic 断言后，第 6 次通过；第 7 次发现新断言未按生产 `sorted(set(scopes))` canonical 顺序比较；修正后第 8 次最终通过。所有失败都停留在隔离 fixture/expectation，未产生真实或不确定外部效果。

另有一次受 guard 保护的 recording-only 临时诊断，用于读取 Gateway/OBS 测试数据库并冻结 claim/result/audit 的真实语义；不计作原子 E2E，不访问生产状态。

## 为什么不能标 DONE

TASK-0495 已证明 owned 安全内核与 provider 构造在隔离环境中可重复 fail closed，但生产接线仍由受保护 owner 持有：

1. `host_brain/app/router_registry.py` 尚未 handoff OBS 与 Pentest production route/lifespan composition。
2. `host_brain/skills/provider_catalog.py` 的 production dynamic import 封口与 signed intake route 尚未 handoff。
3. Host Mate unsigned producer、raw `32145`、startup；Edge signed WeChat producer；Gateway 专属 callback credential仍未合并。
4. Service Catalog 的真实 startup/reservation、live cutover，以及 M48-A Release Seal 的 identity/live/prod/MASTER 条件仍缺失。

因此本任务保持 `IMPLEMENTED_AWAITING_PROTECTED_HANDOFF`；TASK-0496 Platform Safety Seal 也不能完成，M49 不得解锁。隔离通过不代表 production cutover。

## Rollback

1. 保持 Secure Ingress 与 Bridge production mode/kill switch 为现有 default-off/fail-closed 值。
2. 不从本测试任务 mount protected route、导入真实 provider、启动 listener、消费真实队列或操作窗口/设备/channel。
3. 如需回退测试实现，只回退 `beb20c46ea4fe952577aa299d52b6ce6e48a7897`，不得删除生产 durable data 或用户 WIP。
4. 保留 handoff findings 和验证证据；不得把删除 claim 当作重试可能已执行效果的手段。
