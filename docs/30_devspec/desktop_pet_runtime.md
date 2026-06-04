受众：CODEX
标题：Desktop Pet Runtime

# Desktop Pet Runtime

The desktop pet is a dedicated local runtime, not a VTube Studio window and not a Monitor page.

VTube Studio remains the avatar runtime for voice, VTS hotkeys, mouth sync, and OBS-style avatar behavior. The desktop pet is a separate transparent desktop window that renders a Live2D model directly and receives high-level actions from Host through Edge.

## Architecture

- `desktop_pet/`: Electron desktop pet runtime.
- `desktop_pet` HTTP control port: `127.0.0.1:18788`.
- Edge RPC endpoint: `POST /v1/edge/desktop_pet`.
- Host helper: `host_brain.channels.edge_rpc_client.post_desktop_pet()`.
- By default, Host talks directly to the local desktop pet runtime through `HOST_DESKTOP_PET_BASE_URL` or `http://127.0.0.1:18788`.
- Set `HOST_DESKTOP_PET_DIRECT_ENABLED=false` to route through Edge instead.
- Existing Host VTS emotion path bridges to desktop pet touch feedback when `HOST_DESKTOP_PET_EMOTION_BRIDGE_ENABLED` is not disabled.

## Why not VTube Studio

VTube Studio can express, move, talk, and react, but it is not a good desktop-pet container. A real desktop pet needs a transparent frameless window, independent click-through, drag behavior, local hit feedback, and a control protocol that does not depend on the VTS app window shape.

## Runtime commands

`GET /health`

Returns process, window, and renderer state.

`POST /action`

Accepts a JSON payload:

```json
{
  "action": "touch",
  "touch_type": "head",
  "emotion_tag": "HAPPY",
  "expression": "HAPPY"
}
```

Supported actions:

- `show`
- `hide`
- `preset`
- `touch`
- `emote`
- `speak`
- `click_through`
- `status`

## Host emotion mapping

- `HAPPY`, `JOY`, `EXCITED`, `AFFECTION`, `COMFORT`: `head`.
- `PLAYFUL`, `SHY`: `hair`.
- `SURPRISED`, `ANGRY`: `poke`.
- `CONFUSED`, `THINKING`, `SAD`: `body`.
- `NEUTRAL`: `idle`.

## Edge session requirement

The desktop pet must be started in the logged-in Windows interactive session on `edge-lan`. The current task name is expected to be `NatsumeAiDesktopPet`, running as `STRIX\ROG`.

Edge may still be controlled over SSH, but the Electron pet window itself must not run inside a non-interactive SSH session.
