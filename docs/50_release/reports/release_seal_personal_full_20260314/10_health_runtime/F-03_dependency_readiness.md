# F-03 外部依赖就绪

- 执行批次：`20260314_batch01_health_gate`
- 执行时间：`2026-03-14 23:51 CST`
- 结论：`BLOCK`

## 已修复并确认可达

1. `redis`：`docker exec natsume-redis redis-cli ping` 返回 `PONG`。
2. `neo4j`：使用容器内真实凭据 `neo4j / NatsuNeo4j!20260306` 执行 `RETURN 1 AS ok;` 成功。
3. `chroma`：已重建为本机 `18000` 暴露，`/api/v2/heartbeat` 返回成功。

## 仍然阻断

1. `.env` 基线仍是 `CHROMA_ENDPOINT=http://127.0.0.1:8000`，当前依赖封测启动脚本将运行时改到 `18000`。
2. `SemanticRecaller` 在代码中对 `SEMANTIC_RECALL_USE_MOCK` 的默认值是 `true`；当前 `.env` 未显式关闭 mock，说明真实召回链路尚未被严格证明。
3. `edge` 语音依赖未就绪：
   - `ASR model state = UNLOADED`
   - `TTS state = UNLOADED`
   - `VTS state = DISCONNECTED`
4. 本机白盒语音前置缺失：
   - `models/gpt_sovits` 目录不存在
   - 当前 Python 运行时未安装 `pyvts`
   - `D:\Program Files (x86)\Steam\steamapps\common\VTube Studio` 目录存在，但 `appmanifest_1325860.acf` 缺失，且未找到本地可执行文件
   - `17801`、`17802`、`8001` 当前均无监听

## 判定

基础容器依赖已恢复，但“个人全功能封测”要求的不只是容器活着，还要实际已启用能力能工作、没有隐式降级。当前真实召回与语音依赖仍未达标，`F-03` 判定为 `BLOCK`。

## 证据

- `10_health_runtime/F-03_dependency_probe.json`
- `runtime/edge/logs/sealtest_edge.stderr.log`
- `runtime/host/logs/sealtest_host.stdout.log`
