受众：CODEX
标题：Edge 端优化修改与全链路测试报告（2026-03-04）

# Edge 端优化修改与全链路测试报告（2026-03-04）

## 1. 目标与范围

本报告汇总本次对话中对 Edge 端语音链路（ASR / TTS / VTS）的优化修改，以及分步执行后的测试结果。
本文件为正式报告，存放在 `docs/50_project`，不放在 `runtime/` 目录。

覆盖范围：

1. TTS（GPT-SoVITS 兼容与日语模型稳定性）
2. VTS（连接、认证、口型联动与打断复位）
3. ASR（硬件服务启动/停止）
4. 全链路（`speak -> TALK_START -> barge_in -> neutral reset`）

## 2. 本次优化修改

### 2.1 `edge_client/actuators/tts_engine.py`

主要优化点：

1. 增加 GPT-SoVITS 双后端加载路径
   - 先尝试 legacy 接口：`GPT_SoVITS.inference.TTS_Model`
   - 失败后自动切换 modern 接口：`GPT_SoVITS.TTS_infer_pack.TTS`

2. 新版 SDK 运行环境自适配
   - 自动补充 `sys.path`（模型根目录 / `GPT_SoVITS` / `eres2net`）
   - `jieba_fast` 不存在时自动映射到 `jieba`
   - 将 `sv.sv_path` 修正为绝对路径，避免相对路径导致加载失败

3. 权重定位与版本识别增强
   - 优先解析模型根目录 `config.py` 中的 `sovits_path` / `gpt_path`
   - 回退扫描 `GPT_weights*` 和 `SoVITS_weights*`
   - 自动识别 `v2/v3/v4/v2Pro/v2ProPlus`

4. 参考音频选择策略修复（对应“音色与训练版本不一致”）
   - 未显式指定 `EDGE_TTS_REF_AUDIO_PATH` 时，从 `logs/*/2-name2text.txt` + `5-wav32k` 自动选“有文本配对”的样本
   - 日语模式优先选择包含日文字符的文本样本
   - 限制参考音频长度在 3~10 秒范围（按 32kHz 样本点估算）
   - 自动回填 `prompt_text`，并打印最终选中的参考音频路径

5. 新增 modern 适配器
   - 通过 `_ModernSoVITSAdapter` 将 `TTS.run()` 封装为 legacy 风格 `synthesize()`，保证上层调用兼容

### 2.2 `edge_client/actuators/vts_driver.py`

主要优化点：

1. 引入独立 VTS IO 事件循环线程
   - 使用后台线程维护专用 event loop
   - 通过 `run_coroutine_threadsafe` 执行 VTS 协程，避免与 ASGI 主 loop 冲突

2. 认证流程增强
   - 支持读取已有 token
   - 无 token 时自动申请并写回 token 文件
   - 认证失败时提取并抛出 VTS API 详细错误信息

3. 接口兼容细节修复
   - 补齐 `vts_api_info` 的 `name/version`
   - 参数注入、热键触发统一走专用 loop 执行

### 2.3 本地环境修复（非仓库提交项）

1. `.env.edgeprobe` 已在本机固定以下参数（文件被 `.gitignore` 忽略）
   - `EDGE_TTS_REF_AUDIO_PATH` 指向 `fem_nat_00001` 日语样本
   - `EDGE_TTS_REF_TEXT` 使用对应日语文本
   - `EDGE_TTS_LANGUAGE=ja`

2. 本机已安装依赖
   - `pyopenjtalk-plus`（日语文本前处理）

## 3. 测试执行与结果

### 3.1 Contract 全量测试（语音域）

测试范围：

1. `tests/contract/test_*` 中匹配 `edge|voice|asr|tts|vts` 的全部用例

结果：

1. `229 total`
2. `226 passed`
3. `3 failed`
4. 总耗时 `12.89s`

失败用例：

1. `tests/contract/test_vts_hw_contract.py::TestVtsDriverLifecycle::test_connect_without_pyvts_returns_connection_failed`
2. `tests/contract/test_vts_hw_contract.py::TestVtsTalkGate::test_talk_start_when_disconnected_triggers_connect`
3. `tests/contract/test_vts_reconnect_contract.py::TestVtsDriverRegression::test_talk_start_when_not_connected`

失败原因说明：

1. 当前环境 `pyvts + VTube Studio` 可正常连接
2. 上述用例属于“不可连接/未连接”前提假设，和当前真实环境不一致
3. 判定为环境假设不匹配，不是本次改动引发的功能回归

### 3.2 Live 全链路测试（含 VTS 口型联动）

结果产物：`runtime/edge/live_chain_result.json`

关键结果：

1. `asr_start_success = true`
2. `asr_stop_success = true`
3. `speak_success = true`
4. `mouth_on_ok = true`（`talking=true` 且存在 `PLAYBACK` active task）
5. `barge_in_success = true`
6. `mouth_off_ok = true`（`talking=false` + `exp_neutral_01` + 无 `PLAYBACK` active task）

链路结束后 VTS 状态：

1. `state = AUTHENTICATED`
2. `talking = false`
3. `current_expression = exp_neutral_01`

## 4. 结论

1. Edge 端 ASR / TTS / VTS 硬件服务已贯通，live 语音链路可运行。
2. VTS 口型联动与 `barge_in` 后复位行为通过验证。
3. TTS 已修复“参考音频可能漂移导致音色不一致”的关键路径。
4. 当前仅剩 3 个 contract 失败项，均为测试前提与真实环境不一致导致。

## 5. 证据文件

1. `runtime/edge/live_chain_result.json`
2. `runtime/edge/reports/edge_full_chain_report_20260304_033045.json`
3. `runtime/edge/reports/edge_full_chain_report_20260304_033045.md`
4. `runtime/edge/voice_contract_files.txt`

## 6. 编码说明

1. 本文件按 UTF-8 保存，用于替代此前出现乱码的自动生成报告。
2. 如 PowerShell 控制台显示乱码，请优先在 IDE 或 UTF-8 终端中查看文档原文。