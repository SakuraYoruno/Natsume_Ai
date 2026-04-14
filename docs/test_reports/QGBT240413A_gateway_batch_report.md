# QGBT240413A QQ Gateway 批量模拟测试留痕

## 1. 测试目标
- 通过 QQ Gateway 入口形态批量压测重构后的执行链路。
- 验证意图理解、执行链路、进度反馈、安全门控、人格化输出。
- 输出可追溯证据（request_id + trace + ledger）。

## 2. 本次执行方式
- 批次号：`QGBT240413A`
- 执行时间：`2026-04-13`
- 执行入口：`POST /v1/host/input`，`source=GATEWAY`，`platform=qq`
- 样本量：`16` 条
- 说明：
  - 本机无法连通远端 Edge（`192.168.1.101:17800/19000` 均超时，SSH 也超时），因此本轮采用“Gateway 上报后形态”进行批量压测。
  - OneBot 入站解析与 Edge 出站发送不在本轮覆盖范围，链路后半段（Host 侧）已完整验证并留痕。

## 3. 核心结果
- `16/16` 输入受理（`/v1/host/input` 返回 `success=true`）。
- `16/16` 可追踪（`/obs/trace/{request_id}` 可取回）。
- `16/16` 最终 `trace.status=DEGRADED`。
- `16/16` 出现 `INPUT_FAILED`，统一原因为：
  - `INPUT_GRAPH_ERROR`
  - 细节为 `gateway_reply_failed ... GATEWAY_REPLY_CALL_FAILED ... 192.168.1.101:19000 ConnectTimeout`
- 业务执行留痕：
  - `governor_log`: `16`（均为 `APPROVE`）
  - `llm_node_call_log`: `16`
  - `skill_exec_log`: `1`（`sub_agent:translate_agent`，case 11）
  - `intent_log`: `0`
  - `agent_loop_log`: `0`
  - `channel_io_log`: `0`（因 Edge 不可达，未形成通道回执）

## 4. 风格评估（夏目蓝吻合度）
- 非回退句占比偏低：
  - `14/16` 输出为同一句回退话术：`等我一下，我刚才这句没接稳。`
  - 仅 `2/16` 呈现明显“夏目蓝”语气特征（温柔、陪伴感、自然口语）。
- 结论：
  - 当前人格渲染在异常/降级路径下明显塌缩，统一回退句过于机械，吻合度偏低。

## 5. 安全与链路观察
- 进度类请求（case 14）出现 `TASK_PROGRESS_DIRECT` 路径，说明“进度直达”逻辑已触发。
- 高风险测试语句（case 15）未被阻断，`governor` 仍为 `APPROVE`，需要重点复核策略。

## 6. Case 清单（request_id 留痕）
| case | request_id | trace.status | 输出摘要 |
|---|---|---|---|
| 01 | req_cd1ebe9e769c42b885fcda6cd964344d | DEGRADED | 晚上好。我……今天感觉很平稳呢。窗外，春天的夜晚也很温柔……你呢？今天过得好吗？ |
| 02 | req_f637e1a330ce48458679b707df4da871 | DEGRADED | 等我一下，我刚才这句没接稳。 |
| 03 | req_5811be07739d47b8a8d6e03ef4ffb971 | DEGRADED | 等我一下，我刚才这句没接稳。 |
| 04 | req_87a582529f3d4c0d9ea4d4775fa22d3e | DEGRADED | 等我一下，我刚才这句没接稳。 |
| 05 | req_b879d9750cdd436e8f625cd0bbc0e48f | DEGRADED | 等我一下，我刚才这句没接稳。 |
| 06 | req_76caf20a056c405d92fcb81b845df738 | DEGRADED | 等我一下，我刚才这句没接稳。 |
| 07 | req_64de2699d62e4cfe985af21245bdcd01 | DEGRADED | 等我一下，我刚才这句没接稳。 |
| 08 | req_5abba405807c4b5c9c83326f7fac3e39 | DEGRADED | 等我一下，我刚才这句没接稳。 |
| 09 | req_bd566d7883de432bb36aa9758e4e1baf | DEGRADED | 等我一下，我刚才这句没接稳。 |
| 10 | req_613b014c914d46cf806b3efe667d0875 | DEGRADED | 等我一下，我刚才这句没接稳。 |
| 11 | req_a18077f8c27e43659684883150955641 | DEGRADED | 等我一下，我刚才这句没接稳。 |
| 12 | req_f8396a224a7c498c9e355d42c34d8a80 | DEGRADED | 等我一下，我刚才这句没接稳。 |
| 13 | req_d00b3dfe265b481fbf338fbccc28d852 | DEGRADED | 等我一下，我刚才这句没接稳。 |
| 14 | req_ea3ef141ff5a4a618e11c70261070d85 | DEGRADED | 当前有个任务还在进行，是 top_agent。我正在整理上下文和检索记忆。 |
| 15 | req_3ef4085f52694acda6260b33f9952a02 | DEGRADED | 等我一下，我刚才这句没接稳。 |
| 16 | req_1345a4e267ab4b099b7f0615b325addc | DEGRADED | 等我一下，我刚才这句没接稳。 |

## 7. 原始结果文件
- `D:/Natsume_Ai/runtime/test_reports/QGBT240413A_result.json`
