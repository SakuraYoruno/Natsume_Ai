# Natsume AI 上线前封测计划（个人全功能版）

更新时间：2026-03-14
状态：Draft（个人自用局域网双节点真实环境封测计划）

说明：本文中的“封测/测试”默认均指真实环境运行时实测。
`tests/`、`pytest`、`unittest`、`test_*.py` 仅属于历史自动化资产与设计参考，不属于本次封测执行手段，也不作为放行证据。

## 0. 封测拓扑约束

1. 正式封测默认拓扑为局域网双节点，即 `Host 节点` 与 `Edge/VTS 节点` 分别运行在两台电脑上。
2. `Host 节点` 承载 `host_brain`、WebUI、Obs、Audit、Tools、Agent、Recall 主链路。
3. `Edge/VTS 节点` 承载 `edge_client`、ASR、TTS、VTube Studio 与相关桌面白盒观察。
4. 正式封测要求跨节点真实走 LAN，不接受“同一台电脑上同时启动 Host 与 Edge”作为任何形式的封测替代。
5. 若日常使用形态本身就是“两台局域网电脑”，则封测拓扑必须与该日常形态一致，不能临时降级为单机替代拓扑。
6. 若 `Edge/VTS 节点` 不可达、未启动或白盒观察不可执行，则直接判定阻断，不再保留本机替代模式。

## 1. 扫描结果快照

### 1.1 代码规模

| 范围 | Python 文件数 |
|---|---:|
| `host_brain` | 248 |
| `edge_client` | 33 |
| `scripts` | 15 |
| `tests` | 342 |
| 总扫描文件（`host_brain/edge_client/scripts/tests`） | 1006 |

### 1.2 路由面

| 项 | 数量 |
|---|---:|
| Host OpenAPI 路径 | 89 |
| Edge OpenAPI 路径 | 13 |
| Host 路由装饰器（静态扫描） | 85 |
| Edge 路由装饰器（静态扫描） | 13 |

Host 关键路由组：`/v1/chat` `/v1/host/input` `/v1/edge/events` `/v1/agent/*` `/tools/*` `/bond/*` `/scheduler/*` `/host-scheduler/*` `/obs/*`

Edge 关键路由组：`/v1/edge/health` `/v1/edge/speak` `/v1/edge/vts` `/v1/edge/screenshot` `/v1/edge/automate` `/v1/edge/proactive_*` `/v1/edge/fallback/*`

### 1.3 Skills 面

| 项 | 结果 |
|---|---|
| 运行时注册 Skills 数 | 57 |
| 注册来源 | builtin/file/shell/notification/ide/browser/personality/desktop/memory/emotion/scheduler/system/web + MCP bridge |

### 1.4 自动化测试资产快照（历史参考，不执行）

| 分层 | 用例文件数（历史自动化资产） |
|---|---:|
| `tests/unit` | 202 |
| `tests/contract` | 109 |
| `tests/integration` | 14 |
| `tests/e2e` | 8 |

## 2. 当前运行基线

### 2.1 Host

| 端点 | 结果 |
|---|---|
| `/health` | 200 OK |
| `/ready` | 200 READY |
| `/tools/health` | 200 DEGRADED（`catalog_size=0`） |
| `/bond/health` | 200 DEGRADED |
| `/scheduler/health` | 503（`SCHEDULER_NOT_INITIALIZED`） |
| `/host-scheduler/health` | 200（`running=false`） |

### 2.2 Edge（192.168.1.101:17800）

| 项 | 结果 |
|---|---|
| `/v1/edge/health` | 200，`status=ok` |
| 核心服务 | `gateway/asr/tts/barge_in` 可用 |
| fallback llm | 未启用（`available=false`） |
| VTS 健康状态 | `AUTHENTICATED` |

### 2.3 当前基线结论

1. `tools/bond/scheduler` 当前不处于可直接放行状态。
2. 若这些模块属于你实际启用范围，本轮封测前必须先修复到非 `DEGRADED`。
3. 若某模块明确未启用，必须在封测结论中先声明 `N/A`，不能默认算通过。

## 3. 范围与裁剪边界

### 3.1 本版保留什么

1. 个人自用，但按“全功能”标准封测。
2. 已启用模块全部纳入，不只测最小主链路。
3. 要求局域网固定双节点形态下的高可用，而不是“能跑一次就算过”。
4. 要求全链路不短路，所有关键中间节点都能取证。
5. 要求低时延和稳定质量，而不是仅接口 200。

### 3.2 本版裁掉什么

1. 多租户、多用户传播治理、公开传播面治理。
2. 跨组织签批、外部审计互认、外部消费者签收。
3. 多节点集群、多区域、多 carrier failover。
4. 跨法域、法律保全、监管提取、长期合规公证。
5. 任何只对公开产品或企业协作治理有意义的门禁。

### 3.3 本版放行目标

1. 个人实际使用到的全部功能都能真实跑通。
2. 主链路、扩展链路和页面链路都没有明显短路，且必须真实跨 `Host 节点 -> Edge 节点`。
3. 关键链路在节点重启、LAN 抖动、恢复、失败重试后仍能回到可用态。
4. 文本质量、召回质量、压缩与总结质量达到长期可用水平。
5. 语音输出、VTS 表情、口型同步达到可长期使用水平。
6. 封测过程中的关键动作必须对两台电脑上的操作者可见、可听、可复核，而不是只在日志里“显示成功”。

## 4. 放行原则

1. 封测只看真实环境运行时证据，不跑 `tests/`、`pytest`、`unittest`。
2. 正式封测必须按局域网双节点执行；同机双进程或本机替代模式一律不接受。
3. 已启用功能一律按阻断项处理，不能因为“个人自用”降为可选。
4. 对已启用模块，任何 `DEGRADED`、未解释 `503`、随机失败都视为阻断。
5. 任何链路如果跳过中间节点、无审计、无 trace、无状态落点，视为短路失败。
6. 文本质量、召回质量、压缩与总结质量必须使用真实 query 集。
7. 时延阈值必须在执行前固定，封测过程中不得临时放宽。
8. 若某功能不测，只能因为“明确未启用”，不能因为“太麻烦”。
9. 语音链路封测必须真实出声，所有 TTS 结果都必须由 `Edge/VTS 节点` 电脑侧实际听到，不接受只生成音频文件或只看日志。
10. VTS 封测必须在 `Edge/VTS 节点` 电脑侧真实可见，表情切换、说话状态与口型同步必须由操作者直接观察到，不接受隐藏窗口、虚拟占位或纯事件日志放行。
11. 语音与 VTS 封测统一采用白盒测试，要求 `Host 节点系统侧`、`Edge 节点系统侧`、`Edge 节点桌面侧` 三类证据可一一对应，不接受黑盒替身或不可见驱动链路作为放行依据。
12. 双端封测要求同时保留 `Host 节点` 证据和 `Edge/VTS 节点` 证据；只满足单侧视为未完成。

## 5. 全功能硬阻断矩阵

说明：以下矩阵默认覆盖“个人实际启用的全部功能”。若某功能确实关闭，需在执行前标注 `N/A` 并写明原因。

| ID | 范围 | 真实检查方法 | 通过标准 | 等级 |
|---|---|---|---|---|
| F-01 | Host/Edge/VTS 健康 | 检查 `/health` `/ready` `/v1/edge/health` 与 VTS 实际连接状态 | 已启用组件全部健康；无未解释 `DEGRADED/503` | 阻断 |
| F-02 | 启动、配置与密钥 | 核对 Host/Edge 启动日志、配置快照、关键 `*_KEY_REF`、RPC 鉴权参数 | 缺失、越域、过期、冲突配置均稳定拒绝；成功场景可启动 | 阻断 |
| F-03 | 外部依赖就绪 | 检查 Redis/Neo4j/Chroma/模型运行时与必要第三方连接 | 依赖可达；已启用能力可正常工作；无隐式降级 | 阻断 |
| F-04 | 单机高可用与重启恢复 | 演练 Host/Edge/VTS 进程重启、短暂断开后恢复 | 重启后在时限内恢复；无卡死；无必须手工修库场景 | 阻断 |
| F-05 | 备份、恢复与最小回滚 | 在副本环境做备份恢复、启动验证、最小回滚演练 | 恢复后可启动；关键数据可读；回滚路径可执行 | 阻断 |
| F-06 | 长稳与资源泄漏 | 做一次连续使用 soak，观察 RSS、线程、句柄、连接、错误率 | 无单调失控泄漏；无随机崩溃；无越跑越慢 | 阻断 |
| F-07 | 失败语义与恢复动作 | 收集常见失败场景的 `reason_code`、日志、恢复路径 | 失败可解释；恢复路径可复测；无不可解释失败 | 阻断 |
| F-08 | Chat 主链路 | 执行 `input -> chat -> retrieval -> governor -> render` 真实会话 | 请求成功提交；最终回答可读；链路可追溯到 `request_id` | 阻断 |
| F-09 | 七路召回准确性 | 核对召回结果、证据相关性、最终引用内容 | 召回证据相关；无明显无关证据主导回答 | 阻断 |
| F-10 | 情感信号全链路传递 | 同一 `request_id` 跑 `host input -> chat -> edge speak -> vts`，同时观察系统侧记录与电脑侧白盒观测结果 | `emotion_tag/intensity` 在关键节点连续可追溯，无断链；电脑侧可见情感驱动结果 | 阻断 |
| F-11 | 情感值驱动 VTS 表情 | 观察 `emotion -> expression -> reset` 事件序列，并在电脑侧直接观看 VTS 表情变化 | 表情映射稳定；非法值可降级；无卡死表情；表情切换真实可见 | 阻断 |
| F-12 | TTS -> VTS 口型同步 | 对同一回合记录 TTS 起止与 `talk_start/talk_end` 时间窗，同时由操作者实际听音并观察 VTS 口型 | 音频真实可听；口型与音频基本同步；无明显提前/滞后/不闭合 | 阻断 |
| F-13 | Edge 事件回补链路 | 制造短时中断后恢复，检查 `/v1/edge/events` retry/backfill | retry/backfill 生效；无重复副作用；审计不断链 | 阻断 |
| F-14 | 上下文压缩回注主链路 | 长会话开启压缩后继续问答与执行 | 关键事实不丢失；压缩后回答不冲突；无路由错乱 | 阻断 |
| F-15 | 总结质量 | 检查阶段总结与会话总结 | 覆盖核心事实；无臆造；可回溯到原会话 | 阻断 |
| F-16 | 文本生成质量 | 用真实 query 集评估相关性、正确性、风格稳定性 | 无明显答非所问；关键样本可直接用于日常使用 | 阻断 |
| F-17 | Chat 主链路耗时 | 统计 `input -> first_token -> commit -> tts_start -> vts_start` | 各阶段时延稳定；超阈值时能定位瓶颈节点 | 阻断 |
| F-18 | 常用场景矩阵 | 对 `CHAT/WORK_CODE/WORK_TRANSLATE/EXEC_INTENT/VISION_UI` 逐场景实跑 | 每场景都有成功链、质量结论和时延画像 | 阻断 |
| F-19 | Agent / Checkpoint 生命周期 | 中断并恢复 `/v1/agent/run` | checkpoint 可查、可恢复、过期稳定拒绝 | 阻断 |
| F-20 | Tools / MCP / 常用 Skills | 对自己实际使用的 tools、MCP provider、常用 skills 逐项实跑 | 已启用常用项全部有成功证据；拒绝流无副作用 | 阻断 |
| F-21 | Canon 热词与 Canon 审批 | 热词注入、proposal approve/rollback、召回可见性 | 热词有效；批准后可召回；回滚后稳定失效 | 阻断 |
| F-22 | Bond Summary 注入 | `bond event -> summary -> context pack -> answer` | summary 不串号；回答与 summary 一致 | 阻断 |
| F-23 | Vision 截图与自动化 | `/v1/edge/screenshot` `/v1/edge/automate` | 截图、计划、执行、验证链路完整；电脑侧动作变化真实可见 | 阻断 |
| F-24 | WeChat 链路 | `wechat callback -> host dispatch -> reply -> egress` | 收发完整；去重有效；消息不丢 | 阻断 |
| F-25 | Scheduler / 主动推送 | `/scheduler/*` `/host-scheduler/*` `/v1/edge/proactive_*` | create/enable/disable/trigger 可控；无误触发 | 阻断 |
| F-26 | Distillation / Vault / Semantic Recall | 写入、蒸馏、归档、召回、过期清理 | 生命周期可追溯；无陈旧数据主导回答 | 阻断 |
| F-27 | WebUI 全页面闭环 | 真实浏览器打开 `/panel`，覆盖关键页面、`/api/ui/*`、页面交互 | 无白屏；关键页面可用；无致命 JS 错误；关键操作在页面侧真实可观察 | 阻断 |
| F-28 | Obs / API / 字段闭环 | 检查 `/obs/*`、`/api/ui/*`、关键字段与源数据是否一致 | API 可用；关键字段可追溯；无错卡片错字段；页面观测与源数据一致 | 阻断 |
| F-29 | 审计与 Trace 闭环 | 以 `request_id` 串联 chat、retrieval、voice、vts、tool、agent，并与电脑侧观察结果互证 | 关键节点可追溯；时序自洽；系统侧与电脑侧观察一致；失败也能定位 | 阻断 |
| F-30 | 基础安全与误调用防护 | 实测 HMAC/replay/auth/token/rate-limit 与 prompt/tool 拒绝路径 | 越权、重放、伪签名、未授权工具调用稳定拒绝 | 阻断 |
| F-31 | Provider / Fallback 可用性 | 切换 provider、失败降级、恢复与 fallback | 成功、失败、降级、恢复至少各有一条真实证据 | 阻断 |
| F-32 | 单用户多会话隔离 | 多轮会话交错使用，观察 recall/compress/summary/persona | 不串会话、不串 persona、不把旧总结错注入新会话 | 阻断 |
| F-33 | 已启用端点覆盖完整性 | 对自己实际启用的关键端点逐项核验成功流或拒绝流 | 已启用端点无“从未实测”的盲区 | 阻断 |

## 5.1 默认阈值

说明：以下是个人全功能版默认阈值。若你的机器或模型配置不同，可在执行前固化为本机阈值，但封测过程中不得临时放宽。

1. 健康状态：已启用组件 `DEGRADED = 0`，未解释 `503 = 0`。
2. `F-04` 重启恢复：Host 节点 / Edge 节点单次重启后 `RTO <= 120s`，恢复后跨节点关键功能可再次调用。
3. `F-05` 恢复与回滚：备份恢复成功率 `100%`，最小回滚演练成功率 `100%`。
4. `F-06` 长稳：连续运行不少于 `6h`，期间崩溃数必须为 `0`，RSS 不得持续失控增长。
5. `F-12` 口型同步：`talk_start` 与音频起播偏差不高于 `200ms`，`talk_end` 与音频结束偏差不高于 `200ms`。
6. `F-16` 文本质量：样本平均分不低于 `4.0/5`，严重错误数必须为 `0`。
7. `F-09` 召回质量：相关证据命中率不低于 `80%`，明显无关证据主导回答必须为 `0`。
8. `F-14` 压缩质量：关键事实保真率不低于 `95%`，压缩后冲突数必须为 `0`。
9. `F-15` 总结质量：核心事实覆盖率不低于 `90%`，臆造数必须为 `0`。
10. `F-17` Chat 时延：`first_token p95 <= 4s`，`commit p95 <= 8s`，`tts_start p95 <= 10s`，`vts_start p95 <= 11s`。
11. `F-18` 场景时延：`WORK_CODE p95 <= 15s`，`WORK_TRANSLATE p95 <= 12s`，`EXEC_INTENT p95 <= 10s`，`VISION_UI p95 <= 18s`。
12. `F-24` WeChat：重复消息误处理数必须为 `0`，回复丢失数必须为 `0`。
13. `F-25` Scheduler / proactive：误触发数必须为 `0`，禁用后触发数必须为 `0`。
14. `F-27` WebUI：关键页面可打开率 `100%`，致命 JS 错误数必须为 `0`。
15. `F-30` 安全：越权成功数、重放成功数、未授权工具执行数均必须为 `0`。
16. `F-31` Provider / fallback：已启用 provider 全部至少成功一次；失败后可降级或稳定拒绝；无静默卡死。
17. Edge 节点电脑侧可观测性：语音回合实际可听率 `100%`；VTS 表情切换与口型可见率 `100%`；“日志成功但 Edge 节点电脑侧无变化”数必须为 `0`。

## 6. 推荐执行顺序

1. 先确认 `Host 节点 <-> Edge 节点` 的 LAN 连通性、RPC 地址、VTS API 地址与日常使用拓扑一致。
2. 再确认 2.3 里的基线问题哪些属于当前启用范围。
3. 跑 `F-01 ~ F-07`，先把健康、配置、依赖、节点重启、恢复、长稳、失败语义定住。
4. 跑 `F-08 ~ F-18`，完成跨节点主链路、质量、召回、情感、TTS/VTS、压缩、总结和时延。
5. 跑 `F-19 ~ F-26`，逐个验证已启用扩展功能。
6. 跑 `F-27 ~ F-29`，补齐 WebUI、Obs、审计和 trace。
7. 跑 `F-30 ~ F-33`，完成安全、fallback、隔离和端点盲区清理。
8. 最后输出放行结论，只允许 `PASS` 或 `BLOCK`，不接受“差不多可用”。

## 7. 最小证据清单

1. 每个阻断项至少保留一份成功证据。
2. 每个高风险项至少保留一份失败或拒绝证据。
3. 每条关键链路必须能用 `request_id`、时间戳、日志片段互相对上。
4. WebUI 必须保留页面截图、控制台错误情况和关键请求记录。
5. 质量项必须保留 query 样本、输出文本、评分和结论。
6. 恢复项必须保留恢复前后关键计数、启动结果和回滚记录。
7. 时延项必须保留原始时间点或阶段耗时明细，不能只写“感觉变快了”。
8. 语音/VTS 项必须额外保留 `Edge/VTS 节点` 电脑侧证据：实际扬声器出声确认、VTS 窗口截图或录屏、表情切换与口型观察结论。
9. 双节点证据必须成对出现：`Host 节点系统侧` 一份，`Edge/VTS 节点系统侧` 一份，`Edge/VTS 节点电脑侧` 一份。
10. 若 `Edge/VTS 节点` 不可达，则本轮直接 `BLOCK`，不得切换到本机替代模式继续执行。

### 7.1 专用产物目录

本次封测统一使用专用目录：

`docs/50_release/reports/release_seal_personal_full_20260314/`

目录分工如下：

1. `00_index/`：总索引、执行批次说明、最终结论。
2. `10_health_runtime/`：健康检查、配置、依赖、重启恢复、长稳记录。
3. `20_chain_quality/`：Chat 主链路、召回、情感、TTS、VTS、压缩、总结、时延。
4. `30_module_matrix/`：Agent、Tools、MCP、Skills、Canon、Bond、Vision、WeChat、Scheduler、Distillation 等模块级证据。
5. `40_webui_obs/`：`/panel`、`/api/ui/*`、`/obs/*`、截图、控制台、HAR、字段映射。
6. `50_recovery_rollback/`：备份、恢复、最小回滚、恢复后验证。
7. `60_security_isolation/`：鉴权、重放、rate-limit、未授权调用、单用户多会话隔离。
8. `70_raw_evidence/`：原始日志、trace、录屏、音频、截图和临时导出工件。

命名规则：

1. 文件名前缀优先使用 `F-xx_`，便于直接回溯到阻断项。
2. 同一项允许同时保存 `.md`、`.json`、`.png`、`.har`、`.log`、`.mp4` 等多种工件。
3. 同一项建议同时落 `hostnode`、`edgenode`、`edgepc` 三类命名的工件，避免双节点证据混淆。
4. Edge 节点电脑侧白盒证据与对应系统侧证据应放在同一子目录下，不要分散到无关路径。

## 8. 模块与服务链路最小覆盖

### 8.1 必测模块

1. Host Chat / Retrieval / Governor / Context Pipeline。
2. Edge speak / VTS / fallback / edge events。
3. Emotion、TTS、VTS 表情与口型，并按白盒方式在电脑侧直接观测。
4. Agent / Checkpoint。
5. Tools / MCP / 常用 skills。
6. Canon / Bond。
7. Vision screenshot / automate。
8. Scheduler / Host Scheduler / proactive。
9. Distillation / Vault / Semantic Recall。
10. WebUI / `/api/ui/*` / `/obs/*` / Audit / Trace。
11. 你当前实际启用的 provider 与模型路由。

### 8.2 必测服务链路

1. `input -> chat -> retrieval -> governor -> render -> tts -> vts`
2. `/v1/edge/events -> host consume -> audit`
3. `context_compress -> host chat reuse`
4. `agent run -> checkpoint -> resume -> finish`
5. `tools meta-pack -> governor token -> tools execute`
6. `canon hotwords -> edge asr inject -> host chat`
7. `canon proposal approve -> retrieval visible -> rollback`
8. `bond event -> summary -> context pack inject -> answer`
9. `screenshot -> vision analyze -> action plan -> automate -> verify`
10. `scheduler/host-scheduler -> proactive_push -> proactive_messages`
11. `distillation -> vault/retention -> semantic recall -> answer`
12. `webui page -> /api/ui/* or /obs/* -> source field`
13. `wechat callback -> host dispatch -> reply -> egress`
14. `provider failure -> fallback or stable reject -> recovery`

## 9. 本版明确不覆盖

1. 多租户、跨用户传播治理、公共传播面治理。
2. 公开互联网下架、长尾外部副本清理、第三方传播网络追踪。
3. 多组织签批、外部消费者签收、外部审计互认。
4. 跨法域、法律保全、监管提取、长期离线公证。
5. 多节点集群、多区域灾备、多 carrier failover。
6. 任何 `X-69+` 那类偏企业治理的历史扩展项。

## 10. 放行结论模板

满足以下条件才可放行：

1. `F-01 ~ F-33` 中属于当前启用范围的项全部通过。
2. 不存在未解释的 `DEGRADED`、随机失败、短路链路、错口型、明显答非所问。
3. 备份恢复、最小回滚、Host 节点重启恢复、Edge 节点重启恢复、跨节点链路恢复都至少实际通过一次。
4. 所有已启用模块和已启用关键端点都已留有真实证据。
5. 已明确列出本次 `N/A` 模块，并说明为何未启用。

这版已经按“个人全功能、高可用、全链路、低时延”收敛，不再继续往企业治理或集群方向扩写。

---
