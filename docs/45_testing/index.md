受众：PROJECT
标题：Natsume AI 测试证据索引

# 测试证据索引

本目录保存局部测试、集成、性能和验收报告。报告只证明其中绑定的 commit、环境、命令和范围；不得凭一份历史报告把当前整体状态提升为 `PASS`。

## 阅读顺序

1. 先看 [当前机器状态](../../configs/release/current_architecture_status.json)。
2. 再看 [当前测试计划](../../tests/TESTPLAN.md)。
3. 按能力和日期查阅本目录中的具体证据。
4. 最终发布以最新 `FullStackAcceptanceReceipt` 和人工体验评审为准。

## 当前证据摘要

| 范围 | 状态 | 说明 |
|---|---|---|
| 情感生命单元／合同测试 | `LOCAL_PASS` | 覆盖观察、自我状态、关系、表达、兼容边界等定向测试 |
| GPT-SoVITS V4 方案 B | `LOCAL_PASS` | 本机 CUDA 真实生成已通过，配置已进入仓库 |
| Surface 全文件测试 | `PARTIAL` | 115 项通过；4 项现有时间敏感 fallback 断言失败，未伪报通过 |
| 文档门禁 | `PENDING_CURRENT_COMMIT` | 当前文档收口后重新执行 |
| Docker／Host／OBS／Monitor／Gateway | `PARTIAL` | 有局部历史实测，不构成当前完整服务栈验收 |
| 远端 Edge／TTS／VTS/VRM | `NOT_TESTED` | Edge 在另一台电脑上，尚未获准启动 |
| 三人盲测 | `NOT_RUN` | 尚未完成 48 条真实多轮轨迹评审 |
| GitHub Actions | `NOT_RUN_ACCOUNT_QUOTA` | 账户额度不足，未执行步骤 |

## 证据要求

有效证据至少包含 commit SHA、执行时间、环境摘要、命令、输入集合、结果和明确边界。涉及外部模型时还需记录 provider/model、usage、延迟和非缓存 transport identity；涉及 Edge 时需记录实际设备与链路。

fixture、mock、scripted replay 和静态类型检查可以证明合同与回归，但不能替代真实 Provider、Docker 数据服务、TTS、Avatar 或人工体验验收。

临时日志和大体积输出放入忽略的 `artifacts/local-gates/<sha>/`，不得提交密钥、用户正文、语料、模型或原始音视频。
