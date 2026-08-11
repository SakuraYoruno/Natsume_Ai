受众：MASTER
标题：A22 自动化验收收口报告

# A22 自动化验收收口报告

## 1. 结论规则

A22 只关闭本机、远端 Edge 和当前已登录测试平台能在本次运行内真实闭环的自动化范围。最终结论不由本文中的静态 SHA 自引用，而由以下机器报告决定：

```text
runtime/acceptance/a22-final-20260810/evidence-manifest.json
```

该 manifest 只有在以下条件同时成立时才是 `PASS`：tracked worktree 干净；所有带 `repository_commit` 的报告与最终 `HEAD` 一致；migration head 为 153；证据文件全部存在且逐文件计算 SHA-256。任何 `f1181ae` 报告都只是提交前预检，不得冒充最终提交证据。

## 2. 提交前真实预检

| 范围 | 预检结论 | 证据 |
|---|---|---|
| 本地 Docker→Host→OBS→Monitor→Gateway 与七路 Runtime | `PASS_SCOPE_BOUND` | `runtime/acceptance/a22-fullstack-preflight-f1181ae04/reports/`；skill、gateway_chat、delegate、plan、scheduler、internal、operator 均有真实终态 |
| Gemini 3.6 Flash 多模态 | 6/6 `PASS` | `runtime/acceptance/a22-fullstack-preflight-f1181ae04/reports/a22-real-drivers-multimodal.json`；文件 SHA-256 `8227386c2835a6c45be25428a25033aa5675a1737e50f28c1029e065daf0395b` |
| canonical Life 主动陪伴 | 6/6 `PASS` | `runtime/acceptance/a22-proactive-preflight-v2-f1181ae04/proactive6.json`；文件 SHA-256 `8c5659def6c1335436a674a84fd01a145c0b66364a3c2685cf63fd95da5a277f`，内含报告 SHA-256 `d924a77d307b112e00283dc89eb7edc417617f725a41d106c1ac71f2bb5c7c39` |
| Edge 故障／打断 | 6/6 `PASS` | `runtime/acceptance/a22-edge-fault-barge-preflight-f1181ae04/edge-fault-barge6.json`；文件 SHA-256 `2a664798b0d0d1f24bca5465a7d8fd9924d11d43ec1612506703158273de7148`，内含报告 SHA-256 `c519fbba3371f3c2e5f333a0fd694311f498eb99a034bc3427c33ee9c64c8dae` |
| NE-05 Skill 生命周期 | `PASS` | `runtime/acceptance/a22-ne05-host-online-preflight-v6-f1181ae04/ne05-real-skill-lifecycle.json`；文件 SHA-256 `5552a4cb814bcc9f28280532d37762a8f02ee0579feac241179795c84f88ee3f` |
| 远端 Edge ASR GPU | `PASS` | ASR `device=cuda`、`state=READY`，最终运行需重新索引报告 |
| 远端 Edge GPT-SoVITS／Voice／同步 ID／手动 barge | `PASS` | 真实播放开始与中断事件保留相同 synchronization ID；报告不声明真人听音 |
| 远端 Edge 声学 barge-in | `PASS` | 校准真实声学输入触发自动打断；最终运行需重新索引报告 |
| 真实平台 ACK | `PASS_SCOPE_BOUND` | 当前已登录 QQ 账号完成明确标注的自发自收 ACK 并精确读回 message id；最终证据只保存必要元数据，不保存密钥 |
| 针对性回归 | `PASS` | 561 项核心改动回归通过；另有 A22 runner／ASR DLL 增量回归通过 |

主动陪伴报告只证明 canonical Life policy hold、真实 SQLite 收据、持久 Governor 授权、原子 proactive outbox 和幂等重放。它明确写入 `gateway_dispatch_attempted=false`、`delivery_receipts_claimed=0`，不伪造外部送达。

Edge 故障／打断报告使用真实 HMAC HTTP 传输和生产 GPT-SoVITS／播放组件，不使用 in-process Edge 或替身收据。物理播放边界只声明 Edge 回报 `TTS_PLAYBACK_STARTED`；没有真人听音声明。

## 3. 最终冻结运行

最终隔离根固定为：

```text
D:\Natsume_Ai\runtime\acceptance\a22-final-20260810
```

最终运行必须在创建提交后重新启动服务并完成：

1. Docker 三库、Host、OBS、Nginx Monitor、Gateway 和七路 Runtime。
2. 多模态 6、主动陪伴 6、Edge 故障／打断 6。
3. Edge ASR GPU、GPT-SoVITS、文本／Voice、同步 ID、手动及真实声学 barge-in。
4. NE-05 Docker Lab、外部 canary、在线 Host activate/review/degrade/rollback。
5. full、高风险和 changed-code coverage Gate。
6. 证据 manifest、逐文件 SHA-256、A22 资源清理记录。

最终覆盖率未达既定门槛时必须如实保留 `FAIL_BELOW_GATE`；不得通过降低阈值、扩大排除范围或伪造执行来改写结果。

## 4. 明确不属于本 Goal 的自动完成条件

- 至少 3 人的人感盲评。
- MiniMax 余额恢复后的模型对照。
- 专属七海 checkpoint 训练和留出盲听。
- 至少 3 个自然日的连续性证据。
- 需要用户在 VTube Studio 点击的 VTS 授权。
- Pentest、A09、A12 和 npm security audit。

A21 继续保持 `FAIL_EXTERNAL_LATENCY_BOUND`。自动化代理不得伪造真人、平台 ACK、物理听音、训练、账户余额或跨日证据。
