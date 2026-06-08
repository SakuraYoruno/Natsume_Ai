受众：CODEX
标题：MateEngine Bridge

# MateEngine bridge

Status: v1 local bridge.

MateEngine is currently the preferred desktop pet runtime for the local Windows desktop. Natsume Host should treat it as a presentation surface, not as the source of truth for memory, dialogue policy, or personality.

## Current transport

MateEngine exposes a built-in MateSignal/Minecraft event listener over UDP port `32145` when `enableMinecraftMessages` is enabled in:

`C:\Users\34145\AppData\LocalLow\Shinymoon\MateEngineX\settings.json`

The Host-side MateSignal client is:

`host_brain/channels/mateengine.py`

The Host MateSignal route is:

`POST /v1/mateengine/signal`

The local probe is:

```powershell
python tools/mateengine_bridge_probe.py --event-type mob_proximity --name "Natsume Bridge"
```

## Free-text bridge

Steam MateEngine does not load arbitrary C# code from ordinary `.me` mods. The official SDK notes that `.me` mods are asset-oriented and do not allow custom C# assemblies.

Direct Natsume text currently uses an offline patch to MateEngine's `Assembly-CSharp.dll`:

`integrations/mateengine/MateEngineAssemblyPatcher`

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

The Host route is:

`POST /v1/mateengine/say`

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

BepInEx was tested as an alternative plugin path, but BepInEx 5 failed in this MateEngine runtime with `MissingMethodException: Module.GetPEKind`. The installed doorstop hook should stay disabled unless revisited with a compatible loader.

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
- `chibi`: optional transform command for the plugin path if available.
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

### 本机桌宠与 Edge/VTS 情绪镜像隔离

Host 侧 MateEngine 桌宠现在优先由聊天生命周期状态驱动，例如 `focus -> speak -> home`。为避免 Edge/VTS 表情解析的异步结果在对话结束后覆盖本机桌宠最终状态，`HOST_MATEENGINE_VTS_EMOTION_MIRROR_ENABLED` 默认关闭。

如确实需要把 VTS 情绪解析结果同步给本机 MateEngine，可显式设置：

```powershell
$env:HOST_MATEENGINE_VTS_EMOTION_MIRROR_ENABLED="1"
```

### 回复表现层情绪驱动本机说话状态

本机 MateEngine 桌宠的 `speak` 阶段现在从 `/v1/chat` 的 `reply_envelope.performance` / `audit.natsume_emotion_tag` 中提取本轮回复情绪，并作为 `emotion_tag` / `intensity` 发送给 MateEngine。这样桌宠说话时的表情来源是本轮 Natsume 输出本身，而不是 Edge/VTS 的异步表情镜像。

### 桌宠动作验证标准

验证本机桌宠控制时，不能只看命令流水或脸部 blendshape。当前可验证的动作层证据包括 MateEngine 窗口位移：bridge 默认 `MATEENGINE_WINDOW_MOTION_MAX_OFFSET_X=260`、`MATEENGINE_WINDOW_MOTION_MAX_OFFSET_Y=180`，用于让 `happy`、`focus`、`surprised` 等状态在截图中产生肉眼可见的位置差异。

### 连续动作编排入口

Host 侧 `/v1/mateengine/action` 支持 `gesture` / `animation` / `animate`。这些动作会被转换为 bridge 的 `motion_sequence` 命令，由 bridge 按步骤移动 MateEngine 窗口，形成当前可验证的连续桌宠动作。

当前内置动作：

- `bounce` / `happy`：跳一下。
- `jump`：更明显的上跳。
- `nod` / `yes` / `think` / `focus`：点头式上下动作。
- `shake` / `no`：左右摇头式动作。
- `peek` / `listen`：探身式动作。
- `surprised` / `interrupt`：惊讶弹起并轻微左右摆。

该入口是动作层的稳定抽象：当前底层用窗口位移实现可见连续动作；后续如果 MateEngine 插件提供原生骨骼/动画 API，可以在 bridge 的 `motion_sequence` 或新增 native action 中替换实现，而 Host 调用方不需要改协议。

`motion_sequence` 在 bridge 中异步执行：Host HTTP 调用只负责排队并立即返回，bridge 后台线程逐步执行窗口运动。执行完成后可在 `/health.last_motion_sequence` 查看最近一次动作结果。

### 生命周期/情绪自动触发 gesture

Host MateEngine 通道会在现有 `state` / `speak` / `emote` / `touch` 动作后自动追加短 `motion_sequence`，默认开启：

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
