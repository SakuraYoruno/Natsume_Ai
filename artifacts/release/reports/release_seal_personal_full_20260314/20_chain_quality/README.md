# 20_chain_quality — 主链路与质量证据

本目录存放封测计划 **F-08～F-18** 相关证据与执行清单（Chat 主链路、召回、情感、TTS、VTS、压缩、总结、时延）。

## 表情 / 口型 / 语音专项

- **执行清单**：[F-10-F-12_voice_expression_lipsync_checklist.md](F-10-F-12_voice_expression_lipsync_checklist.md)
  按清单在双节点环境执行，并在 Edge 电脑侧白盒观测：是否听到语音、表情是否随 emotion_tag 变化、口型是否与语音同步。

- **单独 speak 诊断**（不经过完整 Chat，用于排查表情/口型）：
  在项目根目录执行：
  ```bash
  # 设置 Edge 节点地址（封测时为局域网 IP）
  set EDGE_BASE_URL=http://<Edge_IP>:17800

  python tools/seal_diag_edge_speak.py --emotion HAPPY
  python tools/seal_diag_edge_speak.py --emotion SAD --text "ちょっと悲しいです。"
  python tools/seal_diag_edge_speak.py --emotion HAPPY --emotion NEUTRAL
  ```
  观测 Edge 电脑侧：听声音、看 VTS 表情与口型，并将响应中的 `vts_expression_result` / `vts_mouth_sync` 保存到本目录作为证据。

## 证据命名

- 建议前缀：`F-10_` / `F-11_` / `F-12_` + 日期 + 结论或类型（如 `F-12_20260315_lipsync_fail.md`）。
- 录屏/截图可放本目录或 `70_raw_evidence/`，文件名注明 `edgenode` 或 `edgepc`。
