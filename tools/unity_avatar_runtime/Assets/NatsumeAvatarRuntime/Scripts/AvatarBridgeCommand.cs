using System;
using System.Collections.Generic;

namespace Natsume.AvatarRuntime
{
    [Serializable]
    public sealed class AvatarBridgeCommand
    {
        public string type;
        public string action;
        public string emotion_tag;
        public string hotkey_id;
        public string action_id;
        public string screenshot_path;
        public bool talking;
        public float value;
        public float intensity = 0.65f;
        public float duration_s = 1.8f;
        public Dictionary<string, float> motion_values = new Dictionary<string, float>();
        public double received_at;
    }
}
