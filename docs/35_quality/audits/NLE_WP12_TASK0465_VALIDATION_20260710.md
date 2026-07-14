受众：PROJECT
标题：TASK-0465 Canon Apply 唯一写入口验证报告

# TASK-0465 / LTE-WP-12 验证报告

版本：v1.3
日期：2026-07-11
状态：`PASSED`
基线提交：`036958b9c0d6685baf8aacc78f493bb6fa3ad0fb`
实现提交：本报告所在 `TASK-0465` 单任务提交

## 1. 结论

TASK-0465 已建立 WorldCanon 唯一生产 mutation boundary。`WorldCanonStore` 保留读取、提案提交与拒绝；`upsert_document`、`upsert`、`approve_proposal*`、`rollback_proposal` 均无条件 fail-closed。实际文件变更仅由 `CanonApplyService` 通过两个 identity-permit primitive 完成。

runtime composition 与 HTTP API 不装配或暴露 token issuer，也不持有签发私钥；`CanonApprovalTokenIssuer` 的实现位于 governor 模块供离线治理工具使用，静态门禁禁止生产调用。离线 issuer 使用 Ed25519 私钥签署完整 approval attestation；runtime 只加载受信公钥并在任何 replay/target 状态查询前验证签名、authority、decision、nonce、token digest、issued/expiry 与完整 command binding。未配置 public keyring 时 apply fail-closed。

## 2. Durable 边界

- 中央 migration：`0053_canon_apply.sql`；`0052` 保留给 TASK-0467，不由本任务占用。service 与离线 issuer 只读核验中央 `schema_migrations` 记录、冻结 asset checksum 与完整 schema manifest，不执行 inline DDL；额外列、约束/index 漂移及任何引用 apply 表的 persistent/temp trigger/view 均 fail-closed。集成 Gate 固定为先合入本任务 0053、再成对合入 TASK-0467 的 0052+0054；中间态不得发布或执行全 catalog migration，两提交齐备后立即验证空库/旧库/重复执行。
- bearer 仅以 SHA-256 digest 入库；attestation 签名绑定该 digest，数据库不存在可恢复 bearer。
- PREPARE 单事务完成 token once-only burn、subject status CAS、journal insert；失败整体回滚，不释放已成功 PREPARE 的 token。
- journal 状态：`PREPARED / APPLIED / FAILED / RECOVERY_REQUIRED`。
- `uq_canon_apply_active_target` 确保每个目标最多一个 active journal；owner lease、recovery-not-before 与 fence 防 live apply 被 recovery 抢占及 stale finalize。
- PREPARE 在 token burn/subject CAS 前使用 apply-only raw loader 核验现有目标的精确 9 字段 schema/type/hash/path-doc binding；带 Persona 扩展字段、空 hash 或旧宽松类型的既有 Canon 被明确冻结且字节不变，不会被兼容 reader 静默规范化或覆盖。公开只读兼容路径保持不变，Persona Canon 仍在本任务范围外。
- write、recovery claim 与 finalize 前均重验 canonical before/target 完整 payload、磁盘原始 JSON、payload 自身 hash、journal before/after/target 坐标、proposal world domain、promotion current-entry lineage、subject status、command hash、burned token 与已签 attestation 的 immutable binding；任一 tamper 进入带稳定 integrity reason 的安全恢复态，不写 Canon、不得标记 `APPLIED`。
- command、issuer、clock/config、token、journal、proposal、promotion/current source 与 historical lineage 中所有版本、时间、fence 均要求 Python/SQLite exact INTEGER；数据库 `REAL 1.5` 不得被 `int()` 截断后继续。rollback source 的 before snapshot 必须递归连接到同 target/version/hash/full payload 的可信历史 APPLY 链；base-zero 只能是空 before，legacy 断链在 token burn 前 fail-closed。
- Canon 文件使用 per-document lock、同目录 temp、file fsync 与 atomic replace；POSIX 执行目录 fsync，Windows 当前依赖 file flush/atomic replace 与 journal recovery，掉电耐久性不作跨资源原子承诺。SQLite/file 非原子窗口由 before/target payload journal 与 recovery 显式表达。
- rollback-to-zero 直接 unlink + directory fsync，不产生随机 tombstone；replace/delete 后崩溃均可由文件 identity 完成幂等 finalize。

## 3. 绑定与拒绝面

`CanonApplyCommand` 不含 title/body/tags。service 只从 pending proposal 或 same-DB Pentest promotion candidate 加载不可替换 payload，并重算 immutable subject hash。命令与 token 精确绑定 operation、subject type/id/version/hash、target、base version、actor、session、trace 与有界 URI evidence refs。

以下情况稳定拒绝：missing/forged/wrong-type token、unknown authority、invalid signature/base64、future/expired attestation、expiry exact boundary、nonce replay、token replacement、subject/session/version/evidence/base/payload drift、command replay、同 target active conflict、cross-DB promotion、stale fence、relative/URI/memory DB path、relative Canon root、partial/stale 0053 schema。

## 4. 静态闭集

NLE boundary scanner 使用有界、scope-aware 字符串/路径事实传播解析常量拼接/f-string、Path `/` 分段、环境变量下标、`importlib.import_module`/builtin `__import__`、Store constructor/re-export/method alias、`getattr/vars/__dict__/__getattribute__` permit/mutator、offline issuer alias、destination-aware copy/move/link/symlink 以及 Canon-root-tainted 文件写。param→return/sink summary 以有界 fixed-point 处理多层、递归和 repo/module-qualified 跨文件 wrapper。生产 roots 覆盖 `edge_client/host_brain/scripts/skills/tools`；builtin `eval/exec` 直接或 alias 调用稳定拒绝。允许项冻结到精确 path/symbol/line call site；未知动态导入、普通目的路径、只读 open、`self.eval`、local-shadowed eval、只读 wrapper 及 Chroma collection `.upsert()` 不误报。历史 PWK 两个 writer 已改投 Pentest domain store，旧 cleanup 脚本改为只读报告。

该闭集是仓库源码静态约束加同一解释器内的 identity capability，不是进程级安全隔离，也不声称抵御已在 Host Python 进程取得任意代码执行的攻击者。将 Canon writer 下沉独立进程并配套 OS ACL/IPC capability 属于后续架构任务；在此之前，生产依赖/动态执行门禁与运行环境代码完整性仍是必要前提。

## 5. 验证证据

- `T-NLE-03` 对抗合约：115 passed；覆盖 signed apply、digest-only、exact expiry/binding、signature-before-state、single winner、target fence、live lease、before/after-file crash、apply/rollback/promotion journal tamper、raw disk exact schema/type/doc binding、扩展 Persona Canon pre-burn freeze、proposal domain/promotion source lineage、可信 historical APPLY rollback chain、SQLite REAL/type 矩阵、write/finalize/recovery 三段重验、delete recovery、中央 migration attestation、read-only exact schema 与 path 负例。
- 关联矩阵（`tests/contract` 与 `tests/unit` 下全部 `*canon*.py`，加 `test_tokens_contract.py`、`test_nle_boundary_scan.py`、`test_pentest_knowledge_store_contract.py`、`test_pentest_knowledge_domain_cutover.py`、`test_pentest_knowledge_online_learn.py`）：21 个文件，347 passed；1 个既有 invalid-escape warning。独立 Canon loader/consumer 12 文件矩阵 105 passed。
- static boundary unit/contract：44 passed；42 项 unit 场景覆盖动态导入、字符串/f-string/Name/Path/env 传播、`getattr/vars/__dict__/__getattribute__`、callable alias、目标型文件操作、local/repo 级 wrapper fixed-point、builtin `eval/exec` 与 Chroma/read-only/local-shadow 负例；仅 11 个既有 invalid-escape warnings。
- migration runner + NLE boundary contract：7 passed；仅 11 个既有 invalid-escape warnings。
- `tools/static_scan/run_all.py --root .`：`passed`，0 failures / 0 violations。
- changed-scope freshness：32 个变更文件命中 5 条规则，0 failures；其中 `arch-dataflow` 首次真实发现过期报告，补齐 Canon Apply 数据流后复跑通过。8 个变更 Markdown audience/title gate 通过。
- unified delivery gate：`static_scan + doc_gate` 2/2 passed；`py_compile` 与 `git diff --check` 通过。
- MASTER 独立复核：上述 115、347、44、7 四组与全部封板门禁均由集成线程在未提交工作树复跑；最终只读复审发现的所有 P1/P2 均先转成合同再修复，提交前不存在已知未关闭项。

本轮只读复审曾复现并已关闭：PREPARE 后篡改 journal payload 可在 recovery 写入未批准正文；磁盘 extra/missing/type drift 被宽松 loader 当作 TARGET；promotion source 与 proposal domain 在恢复时失去绑定；rollback 信任可自洽改写的 before snapshot；SQLite REAL 被 `int()` 截断；无效签名晚于 replay/binding 状态判断；0053 接受额外列/trigger 且 issuer 会 inline bootstrap；分段路径、builtin import、reflection、目标型文件操作及 local/cross-module wrapper 可绕过静态门禁。对应最小复现均已进入上述 115/44 项合同，未以文档说明替代代码修复。

所有测试只使用临时 SQLite/Canon 根；未读取、复制、修改或清理生产 Canon/data。

## 6. 风险与回滚

- 部署必须安装 `cryptography>=46,<47` 并配置受信 Ed25519 public keyring；缺失时 apply 按设计拒绝。
- 离线 issuer 的私钥生命周期、MASTER/GOVERNOR 身份确认和分发属于部署治理，不得装配到 FastAPI/runtime service。
- identity permit 不替代进程隔离或 OS 文件 ACL；若威胁模型扩展到 Host 进程内任意代码执行，必须先完成独立 writer 进程与最小权限下沉。
- 回滚时关闭 approve/promotion apply 与离线 issuer；保留 0053 journal、raw writer fail-closed 与静态扫描，不恢复任何 direct Canon writer。
