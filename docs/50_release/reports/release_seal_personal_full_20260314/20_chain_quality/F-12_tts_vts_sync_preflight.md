# F-12 TTS -> VTS 口型同步（历史 bootstrap 排障记录）

- 执行批次：`20260314_batch02_voice_preflight`
- 调用：`post_speak(text='封测语音最小探针。')`
- 请求 ID：`req_sealtest_tts_probe`
- 结论：`BLOCK`

## 运行结果

1. 通过真实 Edge RPC 调用 `post_speak(...)`。
2. Edge 返回：
   - `status=REJECTED`
   - `reason_code=VOICE_SYNTH_FAILED`
   - `start_reason_code=TTS_MODEL_UNAVAILABLE`
3. 关键失败细节：
   - `GPT-SoVITS model path does not exist: D:\Natsume_Ai\models\gpt_sovits`
4. 由于合成阶段即失败，本轮没有真实可听音频输出，也没有后续 VTS 口型同步可观察。

## 判定

`F-12` 当前阻断点是 TTS 资产未就绪，不是口型同步算法本身。只要 `models/gpt_sovits` 未补齐，本项无法进入白盒验证。

## 证据

- `70_raw_evidence/F-12_tts_probe_failure_20260314.json`
- `runtime/edge/logs/sealtest_edge.stderr.log`
