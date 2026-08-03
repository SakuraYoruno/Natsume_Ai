受众：MASTER
标题：Natsume AI 部署架构与运行边界

# 部署架构与运行边界

当前形态是 Windows 原生多进程应用加 Docker 外部依赖，不是完整容器化 SaaS。

## 1. 服务

| 服务 | 入口 | 默认地址 | 验收要求 |
|---|---|---|---|
| Host | `python -m host_brain` | `127.0.0.1:18080` | health、readiness、真实对话与任务 |
| OBS | `python -m host_brain.obs_main` | `127.0.0.1:18082` | health、metrics、真实 projection |
| Monitor | Nginx + UI build | `127.0.0.1:18180` | 静态资源与真实 OBS 数据 |
| Edge | `python -m edge_client` | `:17800` | health、session、lease、TTS、Avatar |
| Gateway | Edge 内受管服务 | `:19000` | health、stats、入站去重与外发回执 |
| Neo4j | Docker | `:7474` / `:17687` | 建图、查询、重启读取 |
| Chroma | Docker | `:18000` | collection、upsert、query、重启读取 |
| Redis | Docker | `:6379` | PING、SET/GET、锁／限流、重启读取 |

## 2. 启停入口

```powershell
pwsh scripts/runtime/start_docker_services.ps1
pwsh scripts/runtime/start_host_side.ps1
pwsh scripts/runtime/start_edge_side.ps1
```

```powershell
pwsh scripts/runtime/stop_edge_side.ps1
pwsh scripts/runtime/stop_host_side.ps1
pwsh scripts/runtime/stop_docker_services.ps1
```

Edge 位于另一台电脑。远端启动、停止和模型资产变更必须由用户当次明确授权；本轮双端验收已获得授权并使用隔离运行根执行。

## 3. Monitor 构建

在 `host_brain/app/monitor_ui` 执行：

```text
npm ci
npm run check:operator-client
npm run lint
npm run typecheck
npm run test:read-model
npm run build
```

最终验收必须由真实 Nginx 提供构建产物，并读取运行中的 OBS；文件存在或前端 fixture 不算通过。

## 4. 数据隔离

正式验收使用独立 acceptance 数据根、SQLite 路径、Neo4j namespace、Chroma collection 和 Redis prefix。不得写入用户现有 Memory、Knowledge、Persona、Gateway 会话或默认 Docker 数据目录。

Docker volume 默认保留以便复核；只在明确清理 acceptance project 时删除。密钥运行时注入，不能写入仓库或日志。

## 5. 声音配置

Host 默认将中文文本映射为日语 TTS 请求。Edge 启动脚本在启用语言约束时设置中文 ASR、日语 TTS、GPT-SoVITS backend 和 V4 voice bank。模型权重、参考音频和语料只存在于目标机器的受控目录。

## 6. 最终验收边界

必须真实启动并验证 Docker、Host、OBS、Monitor 和 Gateway；目标 Edge、ASR、TTS、VTS/VRM 在用户允许后启动。任一本地强制服务失败时不能使用内存 fallback 冒充 `PASS`。外部平台账号或专用硬件缺失可逐项 `NOT_TESTED`，但必须如实影响最终状态。

## 7. 当前部署状态

目标 Edge、Unified Gateway 和真实消息平台已运行并完成核心往返；ASR、GPT-SoVITS TTS、音频播放、Edge RPC 和 Gateway 为 `UP`。VTS 已认证且口型／身体动作可用，但缺兼容情绪热键，状态为 `DEGRADED`；barge-in 关闭，状态为 `NOT_TESTED`。

Host、OBS 与 Nginx Monitor 已在隔离 acceptance 数据根启动，真实后端 Playwright 验证通过。固定版本 Neo4j、Chroma 与 Redis 已启动；最终提交仍需绑定 commit SHA 重跑写入、读取和容器重启持久性收口。GitHub Actions 未运行的原因为账户额度，状态为 `NOT_RUN_ACCOUNT_QUOTA`。
