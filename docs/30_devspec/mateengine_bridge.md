受众：CODEX
标题：MateEngine Bridge

# MateEngine bridge

状态：历史 MateEngine 协议参考；生产 Host 直接写接口已退役。

## 当前 authority 边界（DL-06）

本文件后续的 UDP、文本和动作格式保留为 Edge/bridge 协议参考，**不再是 Host HTTP 写 authority**。当前生产
Host 的边界如下：

- `GET /v1/mateengine/capabilities`：只读能力目录。
- `GET /v1/mateengine/health`：只读 Host outbox projection；它不会联系 Edge、不会创建 command，且在没有
  Edge receipt 时明确返回 `REMOTE_EDGE_UNVERIFIED` 和 `physical_effect_verified=false`。
- `POST /v1/mateengine/action`、`/signal`、`/say`：统一返回
  `410 MATEENGINE_DIRECT_COMMAND_RETIRED`，不解析为设备 command。
- Input Runtime 的 `ReplyEnvelope.performance` callback 与 Gateway 同步 voice adapter：统一返回
  `RETIRED`，不解析 embodiment runtime、不创建 command/outbox、不调用 TTS、不联系 Edge、也不发送平台语音。
- 唯一可写路径是已接受的 `ExpressionPlanV1` 经受治理的 TaskGraph、Action Preview 与 Host durable outbox，随后由
  Edge 独占执行并返回可核验 receipt；Host 入队从不等于实体设备完成。

远端 Edge 当前在另一台电脑，本波没有启动或连接。下面的历史 probe 只能用于该 Edge/bridge 自身的隔离调试，不能
绕过上述 Host authority 或作为三模态验收证据。

MateEngine is currently the preferred desktop pet runtime for the local Windows desktop. Natsume Host should treat it as a presentation surface, not as the source of truth for memory, dialogue policy, or personality.

## Current transport

MateEngine exposes a built-in MateSignal/Minecraft event listener over UDP port `32145` when `enableMinecraftMessages` is enabled in:

`C:\Users\34145\AppData\LocalLow\Shinymoon\MateEngineX\settings.json`

The Host-side MateSignal client is:

`host_brain/channels/mateengine.py`

历史 Host MateSignal route `POST /v1/mateengine/signal` 已退役；它现在返回 `410`。

The local probe is:

```powershell
python tools/mateengine_bridge_probe.py --event-type mob_proximity --name "Natsume Bridge"
```

## Free-text bridge

Steam MateEngine does not load arbitrary C# code from ordinary `.me` mods. The official SDK notes that `.me` mods are asset-oriented and do not allow custom C# assemblies.

Direct Natsume text currently uses an offline patch to MateEngine's `Assembly-CSharp.dll`:

`tools/mateengine/assembly_patcher`

The patch adds a `NATSUME_TEXT::` escape path to `AvatarMinecraftMessages.InjectEntity`. Host sends a normal `mob_proximity` event, but sets `name` to:

`NATSUME_TEXT::<model-generated text>`

Patched assembly target:

`D:\Program Files (x86)\Steam\steamapps\common\MateEngine\MateEngineX_Data\Managed\Assembly-CSharp.dll`

Backup target:

`D:\Program Files (x86)\Steam\steamapps\common\MateEngine\MateEngineX_Data\Managed\Assembly-CSharp.dll.natsume-bak`

The wire payload still goes to MateEngine UDP port `32145`:

```json
{
  "type": "mob_proximity",
  "phase": "enter",
  "name": "NATSUME_TEXT::Natsume generated text",
  "id": "natsume_say",
  "distance": 1.0
}
```

历史 Host text route `POST /v1/mateengine/say` 已退役；它现在返回 `410`。

The local probe is:

```powershell
python tools/mateengine_bridge_probe.py --say "Natsume Bridge online."
```

## Supported v0 events

- `mob_proximity`
- `day_start`
- `night_start`
- `low_health`
- `low_hunger`
- `death`
- `rain_start`
- `drowning`
- `sleep`
- `crafting`
- `eat`
- `kill_confirm`
- `biome_discovery`

## Limits

The MateSignal UDP path triggers MateEngine's built-in event bubble renderer. The `NATSUME_TEXT::` patch changes only the entity text injection path so Host can provide direct free text.

BepInEx was tested as an alternative plugin path, but BepInEx 5 failed in this MateEngine runtime with `MissingMethodException: Module.GetPEKind`. That unused plugin project has been retired; the installed doorstop hook should stay disabled unless a compatible loader is proven in a new implementation.

Expression blending and rich action control are not complete yet.

`Sync/avatar_dance_play_bus.json` is not the right first bridge. MateEngine uses that file for multi-instance dance synchronization; the leader instance writes commands, while follower instances consume them.

## Freeze motion bridge

Natsume can freeze MateEngine avatar idle/body animation for visual validation and fine-grained mouth/expression tuning.

Host action:

```json
{
  "action": "freeze",
  "enabled": true
}
```

Bridge payload:

```json
{
  "type": "natsume_freeze",
  "phase": "set",
  "name": "NATSUME_FREEZE",
  "id": "natsume_bridge_freeze",
  "distance": 1.0
}
```

The `Assembly-CSharp.dll` patch maps `distance > 0.49` to `NatsumeMateEngineRuntime.FreezeMotionEnabled = true`, then forces Unity `Animator.speed` to `0` each `UniversalBlendshapes.LateUpdate()` frame. Blendshape writes remain active, so mouth and expression validation can continue while body motion is frozen.

## Host lifecycle state machine

Host maps dialogue lifecycle states to deterministic local desktop pet cues:

- `idle / waiting / done`: use `pose=home`, close mouth, clear known forms, return window home.
- `listening`: use `pose=listen`, close mouth, hold an attentive frozen pose.
- `thinking / memory_recall`: use `pose=think`; `tool_running / processing` use `pose=focus`.
- `speaking / tts_start / playback_start`: use `pose=speak`, then drive mouth by text envelope or real TTS RMS frames.
- `interrupted / barge_in`: use `pose=surprised`.
- `error / failed`: use `pose=anxious`.

The chat route allows state transitions instead of locking on the first cue. A request can move from `thinking` to `tool_running/focus`, then to `speaking`, and finally back to `home` after the speech mouth driver closes. The auto-home behavior is controlled by:

- `HOST_MATEENGINE_AUTO_HOME_AFTER_SPEECH_ENABLED`: default `true`.
- `HOST_MATEENGINE_AUTO_HOME_AFTER_SPEECH_DELAY_S`: default `0.18`.

State cue bubbles are disabled by default via `HOST_MATEENGINE_STATE_BUBBLES_ENABLED=false`, so lifecycle control does not spam green debug bubbles. Speech bubbles remain separately controlled by `HOST_MATEENGINE_SPEECH_BUBBLES_ENABLED`.

## Mouth energy drive

The Host mouth driver accepts optional `energy_frames`/`rms_frames` from the TTS playback lifecycle. Each frame is normalized to `[0,1]`, smoothed, then forwarded as `natsume_mouth`.

When real audio RMS frames are not available from Edge, Host falls back to a deterministic text-derived energy envelope. This avoids the old mechanical `high/low/high/low` pulse while keeping the same public lifecycle API.

Relevant environment knobs:

- `HOST_MATEENGINE_MOUTH_FRAME_INTERVAL_S`: mouth frame interval, default `0.08`.
- `HOST_MATEENGINE_MOUTH_MIN_VALUE`: low non-silent mouth value, default `0.06`.
- `HOST_MATEENGINE_MOUTH_MAX_VALUE`: upper mouth value, default `0.95`.
- `HOST_MATEENGINE_MOUTH_ENERGY_GAIN`: frame energy multiplier, default `0.92`.
- `HOST_MATEENGINE_MOUTH_SMOOTHING`: previous-frame smoothing, default `0.45`.
- `HOST_MATEENGINE_SPEECH_FORM_ENABLED`: whether speech form assist is enabled, default `true`.
- `HOST_MATEENGINE_SPEECH_FORM_BLENDSHAPE`: speech assist blendshape, default `Face:MouthSmile`.
- `HOST_MATEENGINE_SPEECH_FORM_VALUE`: speech assist value, default `0.45`.

## Deterministic pose control

Host exposes a bundled desktop-pet `pose` action for local MateEngine control. A pose is a small deterministic sequence of bridge commands: reset stale mouth/form state, apply one visible blendshape, nudge the MateEngine window far enough to be visually distinguishable, then keep motion frozen unless explicitly using `pose=live`.

The bridge `reset` command clears the mouth and all known Natsume-controlled Face blendshapes instead of sending only an empty neutral form. This prevents stale `happy/cozy/thinking` forms from leaking into later `home` or `idle` poses.

Example:

```json
{
  "action": "pose",
  "preset": "listen"
}
```

Useful presets include:

- `home / normal / idle`: clear expression/mouth and return to the stable home position.
- `listen`: light attentive face and a small deterministic lean.
- `think / focus`: stronger thinking face, still frozen.
- `happy / cozy / surprised / anxious`: explicit reaction poses.
- `speak`: speech-ready face without owning mouth timing; real mouth timing still comes from TTS RMS frames.
- `live / unfreeze`: explicitly unfreeze animation when manual motion is desired.

## Next bridge layer

The next useful bridge layer should expose:

- `say(text, emotion, ttl)`
- `emote(blendshape, weight, duration)`
- `freeze_motion(enabled)`
- `gesture(name)`
- `attention(target)`
- `state_hint(kind, payload)`

Host remains responsible for deciding when and why an event happens. MateEngine remains responsible for rendering the desktop body.

### 本机桌宠与 Edge/VTS 表达边界

`/v1/chat` 不再以聊天生命周期的 `focus -> speak -> home`、最终文本或 `reply_envelope.performance`
直接驱动 MateEngine。那类 callback 没有冻结的 `ExpressionPlanV1`、同步编号或 Acceptance Preview，且缺失
标签时曾可能伪造 `NEUTRAL`；它已被硬退役。遗留 `HOST_MATEENGINE_*` 配置键仅为回滚窗口保持可解析，不能
重新打开 Chat→MateEngine 写 authority。

Gateway 的同步语音与 Input Runtime 的 ReplyEnvelope 表演 adapter 也不能把文字回包当作 Voice/Avatar 的写授权。
它们不再“先尝试入队、再由 Preview 拒绝”，而是以 `RETIRED` 无副作用结果结束；真正的声音、表情和动作必须从同轮
accepted `ExpressionPlanV1` 的 `LifeDeliveryTransport` receipt 链启动。

面向陪伴的桌宠/Avatar 表达必须由同轮 accepted `ExpressionPlanV1` 经 Task Acceptance 写入 Host durable
outbox；Host admission 仍不等于设备播放或动作完成。设备调试也必须经受治理的 Operator/TaskGraph command，
不得被聊天回调、模型输出、关系/情绪标签或历史直连 HTTP API 自动调用。

### 桌宠动作验证标准

验证本机桌宠控制时，不能只看命令流水或脸部 blendshape。当前可验证的动作层证据包括 MateEngine 窗口位移：bridge 默认 `MATEENGINE_WINDOW_MOTION_MAX_OFFSET_X=260`、`MATEENGINE_WINDOW_MOTION_MAX_OFFSET_Y=180`，用于让 `happy`、`focus`、`surprised` 等状态在截图中产生肉眼可见的位置差异。

### 连续动作编排协议（历史格式）

`gesture` / `animation` / `animate` 的 `motion_sequence` 格式仍可由受治理的 Edge command 使用；Host 侧
`/v1/mateengine/action` 已退役，不能再把 HTTP 调用转换为 bridge 命令。

当前内置动作：

- `bounce` / `happy`：跳一下。
- `jump`：更明显的上跳。
- `nod` / `yes` / `think` / `focus`：点头式上下动作。
- `shake` / `no`：左右摇头式动作。
- `peek` / `listen`：探身式动作。
- `surprised` / `interrupt`：惊讶弹起并轻微左右摆。

该协议是动作层的历史抽象：当前底层用窗口位移实现可见连续动作；后续如果 MateEngine 插件提供原生骨骼/动画 API，可以在 Edge 的 `motion_sequence` 或新增 native action 中替换实现，但仍须保留 TaskGraph/Preview/outbox/receipt 边界。

`motion_sequence` 在 bridge 中异步执行；只有 Edge receipt 才能说明结果，Host 不再通过 HTTP 直连排队，也不能把 Host outbox receipt 写成设备成功。

### 生命周期/情绪自动触发 gesture

受治理的 Edge command 可在已批准的 `state` / `speak` / `emote` / `touch` 动作后追加短 `motion_sequence`；它不是
Host 自动调用、也不能由旧 HTTP route 默认开启：

```powershell
$env:HOST_MATEENGINE_AUTO_GESTURES_ENABLED="1"
$env:HOST_MATEENGINE_AUTO_GESTURE_DURATION_S="1.2"
```

当前映射：

- `listening` / `hearing` / `user_speaking` -> `peek`
- `thinking` / `memory_recall` / `tool_running` / `processing` / `focus` -> `nod`
- `interrupted` / `barge_in` / `surprised` -> `surprised`
- `error` / `failed` / `anxious` -> `shake`
- `HAPPY` / `JOY` / `LOVE` / `LOVING` / `PROUD` -> `bounce`
- `EXCITED` -> `jump`
- `SURPRISED` -> `surprised`
- `CONFUSED` / `THINKING` -> `nod`
- `ANXIOUS` / `ANGRY` / `FRUSTRATED` -> `shake`

### 真正的 timeline 动作编排

`action_sequence` / `choreography` 是 timeline 级编排，不等同于单个 `motion_sequence`。每个 step 带 `at_ms` 和 `command`，bridge 按时间顺序异步执行，可组合：

- `expression`
- `mouth`
- `motion_sequence`
- `form`
- `reset`
- `freeze`
- `move_model`

自动 gesture 现在也会被包装为 `action_sequence`，例如 `HAPPY -> bounce` 会排入口型打开、bounce motion、口型关闭；`thinking -> nod` 会排入 nod motion。执行结果可在 `/health.last_action_sequence` 查看。
