受众：CODEX / MASTER
标题：TASK-0493 Edge contracts-only 边界与 Host/Edge 循环导入护栏验证记录

# TASK-0493 Edge contracts-only 边界与 Host/Edge 循环导入护栏验证记录

状态：`DONE`。基线清理 commit：`840680d18f21c8b3c2d42668bd06129e440d63af`。实现 commit：`c9b72d5a9681ddd526406e446db456648192d8dd`。

## 已完成的实现

- 新增默认 `FREEZE_NEW` 的 AST 架构守卫，并接入真实 `static_scan` Delivery Gate。交付适配器在代码中固定 `FREEZE_NEW`，环境变量不能把 CI 降为 `REPORT`；非法模式、无效基线、无效迁移清单、源码不可读/不可解析及不完整动态分析均 fail closed。
- 在可复现源码快照 `840680d18…` 上冻结 122 条债务：97 条 Edge→Host（96 条实现导入 + 1 条 Host 文件系统耦合）、25 条 Host→Edge（19 条实现导入 + 6 条整文件 SHA-256 动态风险）。Contracts→实现依赖为 0。
- 54 个迁移组均有 owner、理由、目标、优先级和唯一映射；42 个 E2H 与 12 个 H2E 组全部 `remove_by=TASK-0581`，与 “Edge zero Host imports” 的 Master DAG 一致。
- 指纹不依赖行号，但包含方向、路径、typed scope、控制上下文/条件摘要、依赖种类、symbol、binding 与 occurrence。移动行号不会制造噪音；函数改类、eager 改 conditional、`if` 条件变化、重复导入或 alias 变化都会触发 new/stale 双向差异。
- 动态加载按家族封口：覆盖 `importlib` 及 util/machinery、`runpy`、`pkgutil`、`pydoc`、`zipimport`、builtins import 与 `exec/eval/compile`；同时覆盖关键字参数、star import、反射属性/字典、高阶调用、解包/default/named alias、callable 容器、类属性、return/yield 流转。Edge/Contracts 无法静态证明安全时终止扫描；Host 现存风险以整个源码文件 SHA-256 冻结。
- `natsume_contracts` 使用纯标准库 allowlist，并对 framework、storage、provider、network 与其他 external 实现 fail closed。Host→Edge 只允许 `host_brain/composition/**` 这一明确具体装配根例外。
- 没有交付 baseline regeneration、accept-current 或运行时 import owner 模块的后门。

## 债务与迁移事实

| 项目 | 结果 |
|---|---|
| findings | `122` |
| baseline digest | `sha256:30d220048dc6f6e15013d4f3246cee96eca7d665cb37604e2c728a0c013c9bed` |
| migration groups | `54`（E2H 42 / H2E 12） |
| migration digest | `sha256:bdb8b12e3068d7d3d9bca0637be9601c34ab442470c732c1eb9b537cca46b898` |
| deadline | 全部 `TASK-0581` |
| unmapped / stale / scan violation | `0 / 0 / 0` |

除受守卫执行的 E2H-042 Host 数据目录耦合外，审计还记录但不冒充 import finding 的后续债务：VTS map 手工同步、Host release 对 Edge ASR 源码哈希、Edge TTS 的 repo-root/PYTHONPATH 注入，以及共享 `HOST_INTERNAL_TRIBE_TOKEN` 命名。这些事实随 TASK-0581 及对应 Edge RPC/Voice/Embodiment 迁移一起消除，不能因本任务已 DONE 而遗漏。

## 验证结果

| 层 | 结果 |
|---|---|
| guard unit | `64 passed in 8.99s` |
| contract + static aggregator | `47 passed in 14.27s` |
| TASK-0493 integration | 首次且仅运行一次，`1 passed, 17 warnings in 99.92s` |
| repository guard | `122 findings / 54 mapped groups / 0 violations` |
| 独立复审 | 动态 loader、callable flow、predicate、Contracts purity、path folding 修复后 P0/P1 清零 |
| static | `py_compile`、`git diff --check`、digest/migration invariant、baseline source diff 均通过 |

唯一 integration 调用真实 Delivery Gate 聚合器，确认四个静态扫描器共同通过且 TASK-0493 guard 使用不可降级的 `FREEZE_NEW`。它只读源码和策略文件，不 import 被扫描的 Host/Edge owner，不打开 socket、不创建服务进程、不访问网络/设备、不产生物理动作。17 条 warning 是聚合扫描触发的既有无效转义 `DeprecationWarning`，不属于本任务失败。

## Rollback 与后续

1. Delivery Gate 始终保持 `FREEZE_NEW`；本地 `REPORT` 只能观察，不能替代交付 authority。
2. 新依赖被拒绝时，删除该依赖或经 `natsume_contracts` / approved port 解耦；不得通过扩基线制造绿色。
3. 旧债务消失时，同步有意收缩 baseline 与 migration；stale entry 继续阻断，防止“删一处、留假清单”。
4. `ENFORCE` 只在 TASK-0581 将实现边归零并完成 Edge 独立安装/import/test 后启用。
5. TASK-0494 是下一执行入口，负责 Bridge loopback、token/HMAC、远程 scope/rate/auth/audit 与实际切流；不得在该任务完成前声称 Bridge 暴露已安全。
