受众：PROJECT
标题：Host 与远端 Edge 双机工作流

# Host 与远端 Edge 双机工作流

## 角色

- Host 电脑：代码、canonical SQLite、模型路由、OBS、Monitor、Docker 与 Gateway 验收。
- Edge 电脑：麦克风、ASR、GPT-SoVITS、扬声器、VTS/VRM/MateEngine 和物理 receipt。

两台电脑使用同一已合并 commit 和兼容合同版本。模型、语料、设备配置和密钥不通过 Git 同步。

## 默认流程

1. 在 Host 上完成代码、合同和本机定向测试，合入 `dev`。
2. 记录 commit SHA、配置 digest、需要同步的非仓库模型资产清单。
3. 用户明确允许后，在 Edge 电脑拉取同一 `dev`。
4. 校验模型路径、GPU、ASR、TTS、VTS/VRM 与网络，不直接启动全部服务。
5. 分别启动 Edge health、Voice、Avatar，再连接 Host。
6. 执行固定情感场景、断线、重连、barge-in 和同步偏差测试。
7. 停止 Edge 服务并收集脱敏 receipt；模型和日志留在 Edge 本地。

## 安全边界

- 未经用户当次允许，Host 侧不得远程启动 Edge。
- 不使用仓库默认密码，不把 IP、token、模型绝对路径写进提交。
- Host outbox 入队不等于设备已动作，必须等待 physical receipt。
- Edge 断开时文本链可以降级，不能伪造 Voice/Avatar 成功。

## 同步内容

Git 只同步代码、非密钥配置 schema 和声线 manifest。模型权重、参考音频、训练集、生成样例、用户数据和运行日志通过用户选择的本地方式同步并校验 digest。
