# F-03 外部依赖就绪复测

- 执行批次：`20260315_batch03_dependency_retest`
- 执行时间：`2026-03-15 00:03 CST`
- 结论：`BLOCK`

## 本轮改善

1. `host` 已按封测启动脚本覆写为真实语义召回：
   - `SEMANTIC_RECALL_USE_MOCK=false`
   - `CHROMA_HOST=127.0.0.1`
   - `CHROMA_PORT=18000`
2. 最新 `host` 日志已确认：
   - `SemanticRecaller 已初始化（use_mock=False, chroma=127.0.0.1:18000, top_k=5, threshold=0.70）`
   - `Edge RPC 可达: http://127.0.0.1:17800`
3. `pyvts` 已安装到当前 Python 运行环境。

## 仍然阻断

1. `models/gpt_sovits` 仍不存在，TTS 真实合成仍会返回 `TTS_MODEL_UNAVAILABLE`。
2. `VTube Studio` API 端口 `8001` 仍未监听，`talk_start` 仍返回 `VTS_CONNECTION_FAILED`。
3. `appmanifest_1325860.acf` 仍缺失，当前更像残留目录而不是有效 Steam 安装。

## 判定

`semantic recall` 的 mock 风险已经被消掉，但语音白盒主链路仍未恢复，所以 `F-03` 复测后仍是 `BLOCK`。

## 证据

- `10_health_runtime/F-03_dependency_retest_20260315.json`
- `runtime/host/logs/sealtest_host.stdout.log`
- `20_chain_quality/F-11_vts_expression_preflight.md`
- `20_chain_quality/F-12_tts_vts_sync_preflight.md`
