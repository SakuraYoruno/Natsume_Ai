受众：CODEX / MASTER
标题：TASK-0489 Obs command relocation 验证与 protected handoff 记录

# TASK-0489 Obs command relocation 验证与 protected handoff 记录

状态：`IMPLEMENTED_AWAITING_PROTECTED_HANDOFF`。实现 commit：`3bc7eb7309f757dc40a6bc15167eac67e3ea17d6`。

## 已完成的本地实现

- Obs 的 18 个基线 mutation route 已全部撤下。实际应用审计为 111 个 route entry、104 个唯一 method/path、0 个 mutation；3 个 POST 是 endpoint 身份固定的纯查询例外，7 个重复 GET 不含 writer。
- 16 个具有真实 owner 的动作已进入 typed manifest、SecureIngress、server authority binding、domain port 与 Host command HTTP adapter。`capability_forge.runtime-match` 留在 Host 内部治理调用，不伪装为 operator command；仓库不存在真实 owner 的 `wechat.channel.reset` 已从 active manifest/handler 显式退役。
- Outcome journal 具备 auth/preauth 域隔离、checksum chain、durable `.head`、容量追加前检查、文件/目录 fsync、线程/进程锁、crash recovery 和 immutable terminal receipt。未认证/限流请求不能写 WAL；claim gap 只会保守封为 `UNCERTAIN`，不会重放 domain。
- Composition 在 recovery 前取得跨进程 lifetime owner。同步关闭若仍有 in-flight 会拒绝释放；ASGI 生命周期必须调用 `aclose()`，先关 admission、drain，再关 store/owner。

## 验证结果

| 层 | 结果 |
|---|---|
| 较宽 focused unit/contract 波 | `249 passed in 9.04s` |
| 最终 security/journal/composition/domain/query 波 | `113 passed in 3.78s` |
| TASK-0489 integration 首次波 | `1 failed in 2.29s`；旧夹具用 `principal:<id>` 签 transport key，而 hardened HTTP 正确使用服务端观察到的 `http-peer:testclient`，因此 401 |
| 仅失败项夹具修正重跑 | 未放宽产品策略，只修签名夹具；`1 passed in 2.70s` |
| py_compile / diff / runtime artifact | PASS；未暂存 DB、WAL、head 或 lock |

集成测试只使用临时 SQLite、TestClient 和 recording-only domain fake；没有启动 live/prod 服务，没有网络外发、真实 Pentest、窗口、设备或物理动作。

## 为什么还不能标 DONE

生产 Host 目前没有调用 `build_obs_command_composition()` 或 `create_obs_command_http_router()`。`host_brain/app/router_registry.py` 是 Entry Gate 明确冻结的 protected WIP，原工作区还含 `reader_auto_translate_router` 的未提交 import/include；本任务没有覆盖或绕开该 owner。

明确 handoff 合并要求：

1. 保留原工作区 reader-auto-translate WIP 和集成分支已有 provider/route 接线。
2. 在唯一 Host route owner 中 mount 一次 command HTTP router，provider 只读取 `app.state` 上的单一 composition ingress。
3. 在 Host post-fork lifespan 的 base runtime 启动后构造 composition，再开始接收 command traffic。
4. shutdown 先关闭 command admission，`await composition.aclose()` 排空，清理 `app.state`，最后退出 base runtime。
5. 增加 production construction test，验证默认/非法配置 fail closed、单 router/owner、startup recovery 和 drain-before-release。

handoff 前应保持 `HOST_OBS_COMMAND_RELOCATION_MODE=LEGACY_AUDIT`；旧 mutation 已移除且不得恢复。因此当前是安全不可写，而不是已完成 production command cutover。

## 已登记债务

- JSONL outcome journal 当前每次操作全量校验，且 128 MiB 后 fail closed；validated index、segment rollover/archive 交给 `TASK-0541/TASK-0551`。
- 7 个重复 GET 与版本化 read projection 交给 `TASK-0595/TASK-0596/TASK-0608`。

这些债务和 protected handoff 均已显式记录，不冒充 TASK-0489 已完成能力。
