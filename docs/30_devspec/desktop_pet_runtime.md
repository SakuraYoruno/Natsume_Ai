受众：CODEX
标题：Desktop Pet 当前运行边界

# Desktop Pet 当前运行边界

Desktop Pet 是 Edge 电脑上的独立 Electron／Live2D 窗口，与 VTube Studio、Monitor UI 分离。它只执行高层表现动作，不拥有人格、情绪、关系或记忆 authority。

## 拓扑

```text
accepted ExpressionPlan
→ Host durable embodiment outbox
→ Edge /v1/edge/desktop_pet
→ desktop_pet local runtime :18788
→ action result + physical receipt
```

`desktop_pet/` 负责透明窗口、点击穿透、拖拽、本地触摸反馈和 Live2D 渲染。Host 不应通过旧情绪回调直接操作窗口；设备动作必须来自已接受的表达计划。

## 动作

Edge adapter 可转交 `show`、`hide`、`preset`、`touch`、`emote`、`speak`、`click_through` 和 `status`。payload 必须携带当前 Edge session/fence 和 expression synchronization identity。

## 运行约束

- 必须在 Edge 的已登录交互式 Windows session 中运行，非交互 SSH 进程不能显示窗口。
- 模型资产和本机用户路径不写入仓库配置。
- health 与 action receipt 分开；进程存活不等于动作可见。
- Edge 断开后禁止重放旧 session 已执行动作。
- Desktop Pet 失败只降级该显示通道，不修改 canonical self affect。

目标 Edge 当前未启动，Desktop Pet 物理窗口和动作仍为 `NOT_TESTED`。
