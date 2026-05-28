# Natsume Unity Avatar Runtime

Unity 2022 LTS runtime for the 3D avatar path.

This project is the formal replacement for the temporary Three.js VRM preview.
It keeps the same JSON bridge contract so Edge can switch from the web bridge to
Unity without changing upstream emotion, mouth, or body-action payloads.

## First Open

1. Open Unity Hub and activate a personal/license seat if Unity asks.
2. Open this folder as a Unity project:
   `D:\Natsume_Ai\tools\unity_avatar_runtime`
3. Wait for Package Manager to resolve UniVRM:
   - `com.vrmc.gltf`
   - `com.vrmc.vrm`
4. Run menu item `Natsume > Avatar Runtime > Setup Scene` if the scene is not created automatically.

The runtime reads the VRM path from:

`NATSUME_AIMIS_VRM_PATH`

or falls back to:

`C:\Users\34145\Downloads\鸣潮_爱弥斯1.05(1.0)\鸣潮_爱弥斯1.05(1.0).vrm`

## Bridge

Default HTTP port: `18770`

Endpoints:

- `GET /health`
- `POST /command`

Payload schema is compatible with `tools/vrm_bridge/server.mjs`:

```json
{"type":"expression","emotion_tag":"HAPPY","intensity":0.8}
{"type":"talk","talking":true}
{"type":"mouth","value":0.5,"motion_values":{"FaceAngleY":4.0}}
{"type":"body_action","action_id":"wave","intensity":0.85,"duration_s":1.8}
{"type":"reset"}
```
