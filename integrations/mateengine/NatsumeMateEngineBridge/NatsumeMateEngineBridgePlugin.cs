using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using BepInEx;
using BepInEx.Logging;
using UnityEngine;
using UnityEngine.UI;

namespace Natsume.MateEngineBridge;

[BepInPlugin("ai.natsume.mateengine.bridge", "Natsume MateEngine Bridge", "0.1.0")]
public sealed class NatsumeMateEngineBridgePlugin : BaseUnityPlugin
{
    private const int MaxCommandsPerFrame = 4;
    private readonly ConcurrentQueue<QueuedBridgeCommand> _queue = new();
    private UdpClient? _udp;
    private Thread? _thread;
    private volatile bool _running;
    private int _queuedCount;
    private ManualLogSource? _log;
    private BridgeSecurityGate? _security;
    private GameObject? _fallbackCanvas;
    private Text? _fallbackText;
    private float _fallbackUntil;
    private readonly Dictionary<string, float> _managedBlendshapes = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<Transform, Vector3> _originalScales = new();
    private readonly HashSet<string> _allowedAccessoryPaths = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, DateTime> _lastRejectionLogUtc = new(StringComparer.Ordinal);
    private float _formUntil;
    private bool _chibiActive;

    private void Awake()
    {
        _log = Logger;
        if (!BridgeSecurityGate.TryCreate(out var security, out var reason) || security == null)
        {
            _log?.LogWarning($"Natsume bridge UDP remains disabled: {reason}");
            return;
        }

        _security = security;
        LoadAccessoryAllowlist();
        StartUdp(security.Port);
    }

    private void OnDestroy()
    {
        StopUdp();
    }

    private void Update()
    {
        var processedThisFrame = 0;
        while (processedThisFrame < MaxCommandsPerFrame && _queue.TryDequeue(out var queued))
        {
            processedThisFrame++;
            Interlocked.Decrement(ref _queuedCount);
            try
            {
                HandleCommand(queued.Command);
                _security?.AuditCompleted(queued.Authorization, queued.CommandType, true);
            }
            catch (Exception ex)
            {
                _security?.AuditCompleted(queued.Authorization, queued.CommandType, false);
                _log?.LogWarning($"Authorized Natsume bridge command failed: {ex.Message}");
            }
        }

        if (_fallbackCanvas != null && Time.unscaledTime > _fallbackUntil)
        {
            _fallbackCanvas.SetActive(false);
        }

        if (_formUntil > 0f && Time.unscaledTime > _formUntil)
        {
            ResetForm();
        }

        ApplyManagedBlendshapes();
    }

    private void StartUdp(int port)
    {
        try
        {
            _udp = new UdpClient(new IPEndPoint(IPAddress.Loopback, port));
            _running = true;
            _thread = new Thread(ListenLoop)
            {
                IsBackground = true,
                Name = "NatsumeMateEngineBridgeUdp",
            };
            _thread.Start();
            _log?.LogInfo($"Natsume bridge signed UDP ready on 127.0.0.1:{port}");
        }
        catch (Exception ex)
        {
            _log?.LogError($"Failed to start Natsume bridge UDP: {ex}");
            StopUdp();
        }
    }

    private void StopUdp()
    {
        _running = false;
        try { _udp?.Close(); } catch { }
        _udp = null;
        try { _thread?.Join(250); } catch { }
        _thread = null;
    }

    private void ListenLoop()
    {
        var endpoint = new IPEndPoint(IPAddress.Loopback, 0);
        while (_running)
        {
            try
            {
                var udp = _udp;
                if (udp == null) break;
                var bytes = udp.Receive(ref endpoint);
                var security = _security;
                if (security == null)
                {
                    continue;
                }

                if (!security.TryAuthenticate(
                        bytes,
                        endpoint,
                        out var payloadJson,
                        out var authorization,
                        out var rejectionReason))
                {
                    if (ShouldLogRejection(rejectionReason))
                    {
                        _log?.LogWarning($"Natsume bridge datagram rejected: {rejectionReason}");
                    }
                    continue;
                }

                BridgeCommand? command;
                try
                {
                    command = JsonUtility.FromJson<BridgeCommand>(payloadJson);
                }
                catch (Exception)
                {
                    command = null;
                }

                if (command == null)
                {
                    security.AuditRejected(authorization, "invalid_command_json", "");
                    continue;
                }

                var commandType = (command.type ?? "say").Trim().ToLowerInvariant();
                if (Interlocked.Increment(ref _queuedCount) > security.QueueCapacity)
                {
                    Interlocked.Decrement(ref _queuedCount);
                    security.AuditRejected(authorization, "queue_capacity_exceeded", commandType);
                    continue;
                }

                if (!security.CommitAccepted(authorization, commandType))
                {
                    Interlocked.Decrement(ref _queuedCount);
                    _log?.LogWarning("Natsume bridge command rejected: durable audit unavailable");
                    continue;
                }

                _queue.Enqueue(new QueuedBridgeCommand(command, authorization, commandType));
            }
            catch (ObjectDisposedException)
            {
                break;
            }
            catch (SocketException)
            {
                if (_running)
                {
                    Thread.Sleep(50);
                }
            }
            catch (Exception ex)
            {
                _log?.LogWarning($"Natsume bridge packet ignored: {ex.Message}");
            }
        }
    }

    private void HandleCommand(BridgeCommand command)
    {
        var type = (command.type ?? "say").Trim().ToLowerInvariant();
        if (type == "say")
        {
            ShowSay(command);
            return;
        }

        if (type == "expression" || type == "expression_transition" || type == "form" || type == "shape")
        {
            ApplyForm(command);
            return;
        }

        if (type == "blendshape" || type == "morph")
        {
            SetManagedBlendshape(command.blendshape ?? command.target ?? command.preset, command.value);
            ArmFormTimeout(command.duration > 0f ? command.duration : command.ttl);
            return;
        }

        if (type == "accessory")
        {
            ToggleAccessory(command.accessory ?? command.target ?? command.preset, command.enabled);
            return;
        }

        if (type == "chibi")
        {
            ApplyChibiMode(command.enabled, command.scale);
            return;
        }

        if (type == "reset" || type == "reset_form" || type == "neutral")
        {
            ResetForm();
            return;
        }

        _log?.LogWarning($"Unsupported Natsume bridge command: {command.type}");
    }

    private void ApplyForm(BridgeCommand command)
    {
        var preset = FirstNonEmpty(command.preset, command.emotion_tag, command.emotion, command.target, "neutral")
            .Trim()
            .ToUpperInvariant();
        if (preset == "RESET" || preset == "NEUTRAL" || preset == "NONE")
        {
            ResetForm();
            return;
        }

        if (preset == "CHIBI")
        {
            ApplyChibiMode(true, command.scale);
            return;
        }

        _managedBlendshapes.Clear();
        switch (preset)
        {
            case "HAPPY":
            case "HAPPINESS":
            case "JOY":
            case "PLAYFUL":
                AddBlendshape("Face:Joy1", 70f);
                AddBlendshape("Face:Joy2", 38f);
                AddBlendshape("Face:MouthSmile", 48f);
                AddBlendshape("Face:FaceRed", 14f);
                break;
            case "EXCITED":
            case "EXCITEMENT":
            case "PROUD":
                AddBlendshape("Face:Joy1", 72f);
                AddBlendshape("Face:FaceStar", 58f);
                AddBlendshape("Face:MouthSmile", 42f);
                break;
            case "LOVE":
            case "LOVING":
            case "AFFECTION":
            case "COQUETTISH":
                AddBlendshape("Face:FaceHeart", 100f);
                AddBlendshape("Face:FaceRed", 42f);
                AddBlendshape("Face:MouthSmile", 40f);
                break;
            case "EMBARRASSED":
            case "SHY":
                AddBlendshape("Face:FaceRed", 72f);
                AddBlendshape("Face:LookDown", 36f);
                AddBlendshape("Face:MouthTrouble", 28f);
                break;
            case "SURPRISED":
            case "SURPRISE":
                AddBlendshape("Face:EyeWideLeft", 72f);
                AddBlendshape("Face:EyeWideRight", 72f);
                AddBlendshape("Face:O", 55f);
                AddBlendshape("Face:FaceStar", 36f);
                break;
            case "SAD":
            case "SADNESS":
            case "NOSTALGIC":
                AddBlendshape("Face:Sorrow1", 64f);
                AddBlendshape("Face:Sorrow2", 35f);
                AddBlendshape("Face:FaceCry", 42f);
                AddBlendshape("Face:MouthTrouble", 26f);
                break;
            case "ANGRY":
            case "FRUSTRATED":
                AddBlendshape("Face:Angry", 76f);
                AddBlendshape("Face:BrowDownLeft", 42f);
                AddBlendshape("Face:BrowDownRight", 42f);
                AddBlendshape("Face:MouthStraight", 34f);
                break;
            case "ANXIOUS":
                AddBlendshape("Face:FaceSweat1", 58f);
                AddBlendshape("Face:FaceRed", 24f);
                AddBlendshape("Face:MouthTrouble", 34f);
                break;
            case "CONFUSED":
            case "THINKING":
                AddBlendshape("Face:LookUp", 36f);
                AddBlendshape("Face:LookRight", 24f);
                AddBlendshape("Face:MouthStraight", 26f);
                break;
            case "DARK":
            case "LOW":
                AddBlendshape("Face:FaceDark", 100f);
                AddBlendshape("Face:MouthStraight", 24f);
                break;
            case "NEKO":
            case "CAT":
                AddBlendshape("Face:MouthNeko", 76f);
                AddBlendshape("Face:Joy1", 36f);
                break;
            default:
                SetManagedBlendshape(command.blendshape ?? command.target ?? command.preset, command.value > 0f ? command.value : 100f);
                break;
        }

        ArmFormTimeout(command.duration > 0f ? command.duration : command.ttl);
        _log?.LogInfo($"Applied Natsume form preset: {preset}");
    }

    private void AddBlendshape(string name, float value)
    {
        _managedBlendshapes[name] = Mathf.Clamp(value, 0f, 100f);
    }

    private void SetManagedBlendshape(string? name, float value)
    {
        var cleanName = (name ?? "").Trim();
        if (string.IsNullOrEmpty(cleanName))
        {
            return;
        }

        _managedBlendshapes[cleanName] = Mathf.Clamp(value <= 1.01f ? value * 100f : value, 0f, 100f);
        _log?.LogInfo($"Set Natsume blendshape: {cleanName}={_managedBlendshapes[cleanName]:0.0}");
    }

    private void ArmFormTimeout(float seconds)
    {
        _formUntil = seconds > 0.1f ? Time.unscaledTime + Mathf.Clamp(seconds, 0.2f, 60f) : 0f;
    }

    private void ApplyManagedBlendshapes()
    {
        if (_managedBlendshapes.Count == 0)
        {
            return;
        }

        if (!TryResolveSingleAvatar(out var animator) || animator == null)
        {
            return;
        }

        var renderers = animator.GetComponentsInChildren<SkinnedMeshRenderer>(true);
        foreach (var renderer in renderers)
        {
            var mesh = renderer.sharedMesh;
            if (mesh == null)
            {
                continue;
            }

            for (var i = 0; i < mesh.blendShapeCount; i++)
            {
                var name = mesh.GetBlendShapeName(i);
                if (string.IsNullOrEmpty(name))
                {
                    continue;
                }

                if (_managedBlendshapes.TryGetValue(name, out var value))
                {
                    renderer.SetBlendShapeWeight(i, value);
                }
            }
        }
    }

    private void ResetForm()
    {
        _managedBlendshapes.Clear();
        _formUntil = 0f;
        RestoreChibiScales();
        _log?.LogInfo("Reset Natsume form state.");
    }

    private void ToggleAccessory(string? name, bool enabled)
    {
        var cleanName = (name ?? "").Trim();
        if (string.IsNullOrEmpty(cleanName) || !_allowedAccessoryPaths.Contains(cleanName))
        {
            throw new InvalidOperationException("Accessory path is not explicitly allowlisted.");
        }

        if (!TryResolveSingleAvatar(out var animator) || animator == null)
        {
            throw new InvalidOperationException("A unique humanoid avatar is required.");
        }

        Transform? selected = null;
        foreach (var transform in animator.GetComponentsInChildren<Transform>(true))
        {
            if (!string.Equals(RelativePath(animator.transform, transform), cleanName, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            if (selected != null)
            {
                throw new InvalidOperationException("Accessory path is ambiguous.");
            }
            selected = transform;
        }

        if (selected == null || selected.gameObject == null)
        {
            throw new InvalidOperationException("Accessory path was not found under the active avatar.");
        }
        selected.gameObject.SetActive(enabled);
        _log?.LogInfo($"Accessory toggle '{cleanName}' enabled={enabled}");
    }

    private void ApplyChibiMode(bool enabled, float scale)
    {
        if (!enabled)
        {
            RestoreChibiScales();
            return;
        }

        if (!TryResolveSingleAvatar(out var animator) || animator == null)
        {
            throw new InvalidOperationException("Chibi form requires one unique humanoid avatar.");
        }

        var headScale = scale > 0.1f ? scale : 2.4f;
        ScaleBone(animator, HumanBodyBones.Head, new Vector3(headScale, headScale, headScale));
        ScaleBone(animator, HumanBodyBones.LeftUpperLeg, new Vector3(0.68f, 0.68f, 0.68f));
        ScaleBone(animator, HumanBodyBones.RightUpperLeg, new Vector3(0.68f, 0.68f, 0.68f));
        _chibiActive = true;
        _log?.LogInfo("Applied Natsume chibi form.");
    }

    private void ScaleBone(Animator animator, HumanBodyBones bone, Vector3 factor)
    {
        var transform = animator.GetBoneTransform(bone);
        if (transform == null)
        {
            return;
        }

        if (!_originalScales.ContainsKey(transform))
        {
            _originalScales[transform] = transform.localScale;
        }

        var original = _originalScales[transform];
        transform.localScale = new Vector3(original.x * factor.x, original.y * factor.y, original.z * factor.z);
    }

    private void RestoreChibiScales()
    {
        if (!_chibiActive && _originalScales.Count == 0)
        {
            return;
        }

        foreach (var row in _originalScales)
        {
            if (row.Key != null)
            {
                row.Key.localScale = row.Value;
            }
        }

        _originalScales.Clear();
        _chibiActive = false;
    }

    private void ShowSay(BridgeCommand command)
    {
        var text = (command.text ?? command.message ?? "").Trim();
        if (string.IsNullOrEmpty(text))
        {
            return;
        }

        if (TryShowNativeRandomMessage(text, command.ttl))
        {
            return;
        }

        ShowFallbackBubble(text, command.ttl);
    }

    private bool TryShowNativeRandomMessage(string text, float ttl)
    {
        try
        {
            var handlers = FindObjectsOfType<AvatarRandomMessages>();
            if (handlers.Length != 1)
            {
                return false;
            }
            var handler = handlers[0];

            if (ttl > 0.1f)
            {
                handler.despawnTime = Mathf.Clamp(Mathf.RoundToInt(ttl), 5, 20);
            }

            handler.enableRandomMessages = true;
            var message = new AvatarMessage
            {
                text = text,
                locKey = "",
                state = "Idle",
                onActive = false,
                isHusbando = false,
            };
            var method = typeof(AvatarRandomMessages).GetMethod(
                "ShowSpecificMessage",
                BindingFlags.Instance | BindingFlags.NonPublic
            );
            if (method == null)
            {
                return false;
            }

            method.Invoke(handler, new object[] { message });
            _log?.LogInfo("Displayed Natsume text through AvatarRandomMessages.");
            return true;
        }
        catch (Exception ex)
        {
            _log?.LogWarning($"Native MateEngine bubble failed, using fallback: {ex.Message}");
            return false;
        }
    }

    private void ShowFallbackBubble(string text, float ttl)
    {
        EnsureFallbackBubble();
        if (_fallbackCanvas == null || _fallbackText == null)
        {
            return;
        }

        _fallbackText.text = text;
        _fallbackCanvas.SetActive(true);
        _fallbackUntil = Time.unscaledTime + Mathf.Clamp(ttl > 0.1f ? ttl : 8f, 3f, 30f);
    }

    private void EnsureFallbackBubble()
    {
        if (_fallbackCanvas != null && _fallbackText != null)
        {
            return;
        }

        _fallbackCanvas = new GameObject("NatsumeBridgeFallbackCanvas");
        DontDestroyOnLoad(_fallbackCanvas);
        var canvas = _fallbackCanvas.AddComponent<Canvas>();
        canvas.sortingOrder = 32000;

        var scaler = _fallbackCanvas.AddComponent<CanvasScaler>();
        scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
        scaler.referenceResolution = new Vector2(1920, 1080);

        var panel = new GameObject("NatsumeBridgeBubble");
        panel.transform.SetParent(_fallbackCanvas.transform, false);
        var panelRect = panel.AddComponent<RectTransform>();
        panelRect.anchorMin = new Vector2(1f, 0f);
        panelRect.anchorMax = new Vector2(1f, 0f);
        panelRect.pivot = new Vector2(1f, 0f);
        panelRect.anchoredPosition = new Vector2(-80f, 120f);
        panelRect.sizeDelta = new Vector2(520f, 150f);

        var image = panel.AddComponent<Image>();
        image.color = new Color(0.08f, 0.10f, 0.13f, 0.86f);

        var textObject = new GameObject("Text");
        textObject.transform.SetParent(panel.transform, false);
        var textRect = textObject.AddComponent<RectTransform>();
        textRect.anchorMin = Vector2.zero;
        textRect.anchorMax = Vector2.one;
        textRect.offsetMin = new Vector2(24f, 18f);
        textRect.offsetMax = new Vector2(-24f, -18f);

        _fallbackText = textObject.AddComponent<Text>();
        _fallbackText.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        _fallbackText.fontSize = 24;
        _fallbackText.color = new Color(0.96f, 0.98f, 1f, 1f);
        _fallbackText.alignment = TextAnchor.MiddleLeft;
        _fallbackText.horizontalOverflow = HorizontalWrapMode.Wrap;
        _fallbackText.verticalOverflow = VerticalWrapMode.Truncate;

        _fallbackCanvas.SetActive(false);
    }

    private void LoadAccessoryAllowlist()
    {
        _allowedAccessoryPaths.Clear();
        var raw = Environment.GetEnvironmentVariable("NATSUME_MATEENGINE_PLUGIN_ACCESSORY_ALLOWLIST") ?? "";
        foreach (var item in raw.Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries))
        {
            var value = item.Trim().Trim('/');
            if (!string.IsNullOrEmpty(value)
                && value.Length <= 256
                && value.IndexOf("..", StringComparison.Ordinal) < 0)
            {
                _allowedAccessoryPaths.Add(value);
            }
        }
    }

    private bool ShouldLogRejection(string reason)
    {
        var key = string.IsNullOrWhiteSpace(reason) ? "unknown" : reason;
        var now = DateTime.UtcNow;
        if (_lastRejectionLogUtc.TryGetValue(key, out var previous)
            && (now - previous).TotalSeconds < 1.0)
        {
            return false;
        }
        _lastRejectionLogUtc[key] = now;
        return true;
    }

    private bool TryResolveSingleAvatar(out Animator? selected)
    {
        selected = null;
        foreach (var animator in FindObjectsOfType<Animator>())
        {
            if (animator == null || animator.avatar == null || !animator.avatar.isHuman)
            {
                continue;
            }
            if (selected != null)
            {
                _log?.LogWarning("MateEngine bridge effect rejected: multiple humanoid avatars detected.");
                selected = null;
                return false;
            }
            selected = animator;
        }
        if (selected == null)
        {
            _log?.LogWarning("MateEngine bridge effect rejected: no humanoid avatar detected.");
            return false;
        }
        return true;
    }

    private static string RelativePath(Transform root, Transform target)
    {
        if (target == root)
        {
            return "";
        }
        var segments = new Stack<string>();
        var current = target;
        while (current != null && current != root)
        {
            segments.Push(current.name);
            current = current.parent;
        }
        return current == root ? string.Join("/", segments) : "";
    }

    [Serializable]
    private sealed class BridgeCommand
    {
        public string? type;
        public string? text;
        public string? message;
        public string? emotion;
        public string? emotion_tag;
        public string? preset;
        public string? target;
        public string? blendshape;
        public string? accessory;
        public bool enabled;
        public float value;
        public float duration;
        public float scale;
        public float ttl = 8f;
    }

    private readonly struct QueuedBridgeCommand
    {
        internal QueuedBridgeCommand(
            BridgeCommand command,
            BridgeAuthorization authorization,
            string commandType)
        {
            Command = command;
            Authorization = authorization;
            CommandType = commandType;
        }

        internal BridgeCommand Command { get; }
        internal BridgeAuthorization Authorization { get; }
        internal string CommandType { get; }
    }

    private static string FirstNonEmpty(params string?[] values)
    {
        foreach (var value in values)
        {
            if (!string.IsNullOrWhiteSpace(value))
            {
                return value;
            }
        }

        return "";
    }
}
