using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using UniGLTF;
using UniVRM10;
using UnityEngine;
using UnityEngine.Rendering;

namespace Natsume.AvatarRuntime
{
    public sealed class NatsumeVrmAvatarRuntime : MonoBehaviour
    {
        private const string DefaultAimisPath =
            @"C:\Users\34145\Downloads\鸣潮_爱弥斯1.05(1.0)\鸣潮_爱弥斯1.05(1.0).vrm";

        [SerializeField] private string vrmPath;
        [SerializeField] private Transform avatarParent;
        [SerializeField] private float idleGain = 1.55f;
        [SerializeField] private bool autoFrameCamera = true;
        [SerializeField] private bool useHumanoidPose = true;
        [SerializeField] private float fullBodyCameraMargin = 0.68f;
        [SerializeField] private float cameraVerticalLiftRatio = 0.07f;
        [SerializeField] private float cameraHorizontalOffset = -0.06f;
        [SerializeField] private bool enableAmbientMotion = true;

        private readonly Dictionary<HumanBodyBones, Quaternion> _baseRotations = new Dictionary<HumanBodyBones, Quaternion>();
        private readonly Dictionary<string, float> _motionValues = new Dictionary<string, float>();
        private readonly Dictionary<string, int> _blendShapeIndex = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, int> _muscleIndex = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        private readonly List<SkinnedMeshRenderer> _renderers = new List<SkinnedMeshRenderer>();

        private Vrm10Instance _vrm;
        private Animator _animator;
        private Camera _runtimeCamera;
        private GameObject _avatarRoot;
        private HumanPoseHandler _poseHandler;
        private HumanPose _humanPose;
        private Vector3 _baseHumanBodyPosition;
        private Quaternion _baseHumanBodyRotation = Quaternion.identity;
        private float _mouthTarget;
        private float _mouthValue;
        private float _emotionIntensity;
        private string _emotionTag = "NEUTRAL";
        private bool _talking;
        private float _externalMotionUntil;
        private string _bodyAction = "";
        private float _bodyActionStartedAt;
        private float _bodyActionDuration;
        private float _bodyActionIntensity;
        private string _ambientAction = "";
        private float _ambientActionStartedAt;
        private float _ambientActionDuration;
        private float _nextAmbientActionAt;
        private CancellationTokenSource _loadCancellation;

        public bool IsAvatarLoaded => _avatarRoot != null && _animator != null;
        public string VrmPath => ResolveVrmPath();
        public string CurrentBodyAction => _bodyAction;
        public string CurrentAmbientAction => _ambientAction;

        private async void Start()
        {
            await LoadAvatarAsync();
        }

        private void OnDestroy()
        {
            _loadCancellation?.Cancel();
            _loadCancellation?.Dispose();
        }

        private void LateUpdate()
        {
            if (!IsAvatarLoaded)
            {
                return;
            }

            var t = Time.time;
            LockAvatarRoot();
            UpdateAmbientAction(t);
            _mouthValue = Mathf.Lerp(_mouthValue, _mouthTarget, Time.deltaTime * 16.0f);
            ApplyPose(t);
            LockAvatarRoot();
            ApplyExpression();
        }

        public async Task LoadAvatarAsync()
        {
            var path = ResolveVrmPath();
            if (!File.Exists(path))
            {
                Debug.LogError($"VRM file not found: {path}");
                return;
            }

            _loadCancellation?.Cancel();
            _loadCancellation = new CancellationTokenSource();
            var instance = await Vrm10.LoadPathAsync(
                path,
                canLoadVrm0X: true,
                controlRigGenerationOption: ControlRigGenerationOption.None,
                showMeshes: true,
                awaitCaller: new RuntimeOnlyAwaitCaller(),
                materialGenerator: new BuiltInVrm10MaterialDescriptorGenerator(),
                ct: _loadCancellation.Token);

            _vrm = instance;
            _avatarRoot = instance.gameObject;
            if (avatarParent != null)
            {
                _avatarRoot.transform.SetParent(avatarParent, false);
            }
            _avatarRoot.transform.localPosition = Vector3.zero;
            _avatarRoot.transform.localRotation = Quaternion.identity;
            _avatarRoot.transform.localScale = Vector3.one;

            var gltfInstance = _avatarRoot.GetComponent<RuntimeGltfInstance>();
            if (gltfInstance != null)
            {
                gltfInstance.ShowMeshes();
                gltfInstance.EnableUpdateWhenOffscreen();
            }

            _animator = _avatarRoot.GetComponentInChildren<Animator>();
            CacheBones();
            CacheBlendShapes();
            CacheHumanoidPose();
            ConfigureSceneLook();
            ConfigureAvatarMaterials();
            await new RuntimeOnlyAwaitCaller().NextFrame();
            await new RuntimeOnlyAwaitCaller().NextFrame();
            ScheduleNextAmbientAction(Time.time);
            if (autoFrameCamera)
            {
                FrameCameraToAvatar();
            }
            Debug.Log($"Loaded VRM avatar: {path}");
        }

        public void ApplyCommand(AvatarBridgeCommand command)
        {
            var type = (command.type ?? command.action ?? "").Trim().ToLowerInvariant();
            switch (type)
            {
                case "expression":
                case "expression_transition":
                    _emotionTag = string.IsNullOrWhiteSpace(command.emotion_tag) ? "NEUTRAL" : command.emotion_tag.Trim().ToUpperInvariant();
                    _emotionIntensity = Mathf.Clamp01(command.intensity <= 0 ? 0.65f : command.intensity);
                    break;
                case "talk":
                    _talking = command.talking;
                    if (!_talking)
                    {
                        _mouthTarget = 0;
                    }
                    break;
                case "mouth":
                    _mouthTarget = Mathf.Clamp01(command.value);
                    _motionValues.Clear();
                    if (command.motion_values != null)
                    {
                        foreach (var item in command.motion_values)
                        {
                            _motionValues[item.Key] = item.Value;
                        }
                    }
                    _externalMotionUntil = Time.time + 0.65f;
                    break;
                case "body_action":
                    _bodyAction = string.IsNullOrWhiteSpace(command.action_id) ? "idle_shift" : command.action_id.Trim().ToLowerInvariant();
                    _bodyActionIntensity = Mathf.Clamp01(command.intensity <= 0 ? 0.7f : command.intensity);
                    _bodyActionDuration = Mathf.Max(0.4f, command.duration_s <= 0 ? 1.8f : command.duration_s);
                    _bodyActionStartedAt = Time.time;
                    _ambientAction = "";
                    break;
                case "reset":
                    _emotionTag = "NEUTRAL";
                    _emotionIntensity = 0;
                    _mouthTarget = 0;
                    _talking = false;
                    _motionValues.Clear();
                    _bodyAction = "";
                    _ambientAction = "";
                    ScheduleNextAmbientAction(Time.time);
                    break;
                case "reframe":
                    FrameCameraToAvatar();
                    break;
                case "screenshot":
                case "capture_screenshot":
                    CaptureScreenshot(command.screenshot_path);
                    break;
            }
        }

        private string ResolveVrmPath()
        {
            if (!string.IsNullOrWhiteSpace(vrmPath))
            {
                return vrmPath;
            }
            var env = Environment.GetEnvironmentVariable("NATSUME_AIMIS_VRM_PATH");
            return string.IsNullOrWhiteSpace(env) ? DefaultAimisPath : env;
        }

        private void CacheBones()
        {
            _baseRotations.Clear();
            if (_animator == null)
            {
                return;
            }
            foreach (HumanBodyBones bone in Enum.GetValues(typeof(HumanBodyBones)))
            {
                if (bone == HumanBodyBones.LastBone)
                {
                    continue;
                }
                var tx = _animator.GetBoneTransform(bone);
                if (tx != null)
                {
                    _baseRotations[bone] = tx.localRotation;
                }
            }
        }

        private void CacheBlendShapes()
        {
            _renderers.Clear();
            _blendShapeIndex.Clear();
            if (_avatarRoot == null)
            {
                return;
            }
            _avatarRoot.GetComponentsInChildren(true, _renderers);
            foreach (var renderer in _renderers)
            {
                var mesh = renderer.sharedMesh;
                if (mesh == null)
                {
                    continue;
                }
                for (var i = 0; i < mesh.blendShapeCount; i++)
                {
                    _blendShapeIndex[mesh.GetBlendShapeName(i)] = i;
                }
            }
        }

        private void CacheHumanoidPose()
        {
            _poseHandler = null;
            _muscleIndex.Clear();
            if (!useHumanoidPose || _animator == null || _animator.avatar == null || !_animator.avatar.isValid || !_animator.avatar.isHuman)
            {
                return;
            }
            _poseHandler = new HumanPoseHandler(_animator.avatar, _animator.transform);
            _humanPose = new HumanPose();
            _poseHandler.GetHumanPose(ref _humanPose);
            _baseHumanBodyPosition = _humanPose.bodyPosition;
            _baseHumanBodyRotation = _humanPose.bodyRotation;
            for (var i = 0; i < HumanTrait.MuscleCount; i++)
            {
                _muscleIndex[HumanTrait.MuscleName[i]] = i;
            }
        }

        private void LockAvatarRoot()
        {
            if (_avatarRoot == null)
            {
                return;
            }
            _avatarRoot.transform.localPosition = Vector3.zero;
            _avatarRoot.transform.localRotation = Quaternion.identity;
            _avatarRoot.transform.localScale = Vector3.one;
        }

        private struct AmbientPose
        {
            public float faceYaw;
            public float facePitch;
            public float faceRoll;
            public float spineFrontBack;
            public float chestFrontBack;
            public float chestLeftRight;
            public float leftArmDownUp;
            public float rightArmDownUp;
            public float leftArmFrontBack;
            public float rightArmFrontBack;
            public float leftHandDownUp;
            public float rightHandDownUp;
        }

        private void UpdateAmbientAction(float t)
        {
            if (!enableAmbientMotion)
            {
                _ambientAction = "";
                return;
            }

            if (IsBodyActionActive(t))
            {
                _ambientAction = "";
                _nextAmbientActionAt = Mathf.Max(_nextAmbientActionAt, t + 1.2f);
                return;
            }

            if (!string.IsNullOrEmpty(_ambientAction))
            {
                if (t - _ambientActionStartedAt >= _ambientActionDuration)
                {
                    _ambientAction = "";
                    ScheduleNextAmbientAction(t);
                }
                return;
            }

            if (t >= _nextAmbientActionAt)
            {
                StartAmbientAction(t);
            }
        }

        private void StartAmbientAction(float t)
        {
            var talkingActions = new[] { "listening_nod", "soft_shift", "curious_glance" };
            var idleActions = new[] { "soft_shift", "look_around", "self_adjust", "breath_reset", "curious_glance" };
            var actions = _talking ? talkingActions : idleActions;
            _ambientAction = actions[UnityEngine.Random.Range(0, actions.Length)];
            _ambientActionDuration = UnityEngine.Random.Range(1.4f, _talking ? 2.6f : 3.8f);
            _ambientActionStartedAt = t;
        }

        private void ScheduleNextAmbientAction(float t)
        {
            _nextAmbientActionAt = t + UnityEngine.Random.Range(_talking ? 0.9f : 1.2f, _talking ? 2.4f : 3.2f);
        }

        private bool IsBodyActionActive(float t)
        {
            return !string.IsNullOrEmpty(_bodyAction) && t - _bodyActionStartedAt < _bodyActionDuration;
        }

        private AmbientPose EvaluateAmbientPose(float t)
        {
            var pose = new AmbientPose();
            if (enableAmbientMotion)
            {
                var bodyScale = IsBodyActionActive(t) ? 0.25f : 1.0f;
                var breath = Mathf.Sin(t * 1.18f);
                var slowSway = Mathf.Sin(t * 0.44f + 0.8f);
                var counterSway = Mathf.Sin(t * 0.61f + 2.1f);
                pose.faceYaw += (slowSway * 5.2f + Mathf.Sin(t * 0.23f) * 2.4f) * bodyScale;
                pose.facePitch += Mathf.Sin(t * 0.73f + 0.4f) * 3.0f * bodyScale;
                pose.faceRoll += Mathf.Sin(t * 0.39f + 1.1f) * 2.8f * bodyScale;
                pose.spineFrontBack += breath * 0.030f * bodyScale;
                pose.chestFrontBack += breath * 0.058f * bodyScale;
                pose.chestLeftRight += slowSway * 0.038f * bodyScale;
                pose.leftArmDownUp += counterSway * 0.050f * bodyScale;
                pose.rightArmDownUp -= counterSway * 0.050f * bodyScale;
                pose.leftArmFrontBack += Mathf.Sin(t * 0.52f) * 0.044f * bodyScale;
                pose.rightArmFrontBack += Mathf.Sin(t * 0.50f + 1.7f) * 0.044f * bodyScale;
                pose.leftHandDownUp += Mathf.Sin(t * 0.84f + 0.5f) * 0.060f * bodyScale;
                pose.rightHandDownUp += Mathf.Sin(t * 0.78f + 2.4f) * 0.060f * bodyScale;
            }

            if (string.IsNullOrEmpty(_ambientAction) || _ambientActionDuration <= 0)
            {
                return pose;
            }

            var p = Mathf.Clamp01((t - _ambientActionStartedAt) / _ambientActionDuration);
            var ease = Mathf.Sin(Mathf.PI * p);
            var sway = Mathf.Sin(p * Mathf.PI * 2.0f);
            switch (_ambientAction)
            {
                case "soft_shift":
                    pose.spineFrontBack = -0.012f * ease;
                    pose.chestLeftRight = 0.045f * sway;
                    pose.faceRoll = 2.2f * sway;
                    pose.leftArmDownUp = -0.035f * ease;
                    pose.rightArmDownUp = 0.025f * ease;
                    break;
                case "look_around":
                    pose.faceYaw = 9.0f * sway * ease;
                    pose.facePitch = -1.4f * ease + 1.2f * Mathf.Sin(p * Mathf.PI * 4.0f) * ease;
                    pose.faceRoll = -1.2f * sway;
                    pose.chestLeftRight = 0.025f * sway;
                    break;
                case "curious_glance":
                    pose.faceYaw = -7.0f * ease;
                    pose.facePitch = -2.8f * ease;
                    pose.chestFrontBack = -0.028f * ease;
                    pose.leftArmFrontBack = -0.035f * ease;
                    pose.rightArmFrontBack = -0.035f * ease;
                    break;
                case "self_adjust":
                    pose.leftArmDownUp = -0.20f * ease;
                    pose.leftArmFrontBack = -0.12f * ease;
                    pose.leftHandDownUp = 0.12f * ease;
                    pose.faceYaw = 3.5f * ease;
                    pose.chestLeftRight = -0.018f * ease;
                    break;
                case "breath_reset":
                    pose.spineFrontBack = 0.018f * ease;
                    pose.chestFrontBack = 0.034f * ease;
                    pose.facePitch = 1.1f * ease;
                    break;
                case "listening_nod":
                    pose.facePitch = Mathf.Sin(p * Mathf.PI * 4.0f) * 3.2f * ease;
                    pose.chestFrontBack = 0.018f * ease;
                    break;
            }
            return pose;
        }

        private void ConfigureSceneLook()
        {
            ConfigureRenderQuality();
            RenderSettings.ambientMode = AmbientMode.Flat;
            RenderSettings.ambientLight = new Color(0.34f, 0.36f, 0.42f);
            RenderSettings.ambientIntensity = 0.62f;
            RenderSettings.reflectionIntensity = 0.04f;
            RenderSettings.fog = false;
            var camera = EnsureRuntimeCamera();
            if (camera != null)
            {
                camera.clearFlags = CameraClearFlags.SolidColor;
                camera.backgroundColor = new Color(0.17f, 0.18f, 0.22f);
                camera.allowHDR = false;
                camera.allowMSAA = true;
                camera.useOcclusionCulling = false;
            }
            foreach (var light in FindObjectsOfType<Light>())
            {
                if (light.type == LightType.Directional)
                {
                    light.intensity = 0.46f;
                    light.color = new Color(0.94f, 0.95f, 1.0f);
                    light.shadows = LightShadows.None;
                    light.transform.rotation = Quaternion.Euler(28, -38, 0);
                }
                else
                {
                    light.enabled = false;
                }
            }
        }

        private static void ConfigureRenderQuality()
        {
            Application.targetFrameRate = 60;
            QualitySettings.antiAliasing = 8;
            QualitySettings.anisotropicFiltering = AnisotropicFiltering.ForceEnable;
            QualitySettings.masterTextureLimit = 0;
            QualitySettings.globalTextureMipmapLimit = 0;
            QualitySettings.lodBias = 2.0f;
            QualitySettings.maximumLODLevel = 0;
            QualitySettings.pixelLightCount = 2;
            QualitySettings.shadowResolution = ShadowResolution.Medium;
        }

        private void ConfigureAvatarMaterials()
        {
            foreach (var renderer in _renderers)
            {
                if (renderer == null)
                {
                    continue;
                }
                renderer.updateWhenOffscreen = true;
                foreach (var material in renderer.materials)
                {
                    if (material == null)
                    {
                        continue;
                    }

                    DampColor(material, "_Color", 0.94f);
                    DampColor(material, "_ShadeColor", 0.88f);
                    DampColor(material, "_EmissionColor", 0.10f);
                    DampColor(material, "_RimColor", 0.18f);
                    SetFloatIfPresent(material, "_GiEqualization", 0.50f);
                    SetFloatIfPresent(material, "_RimLightingMix", 0.10f);
                    SetFloatIfPresent(material, "_OutlineWidth", 0.0f);
                    ImproveTexture(material, "_MainTex");
                    ImproveTexture(material, "_ShadeTex");
                    ImproveTexture(material, "_BumpMap");
                    ImproveTexture(material, "_EmissionMap");
                    ImproveTexture(material, "_RimTex");
                    ImproveTexture(material, "_MatcapTex");
                }
            }
        }

        private static void DampColor(Material material, string propertyName, float factor)
        {
            if (!material.HasProperty(propertyName))
            {
                return;
            }
            var color = material.GetColor(propertyName);
            color.r = Mathf.Clamp01(color.r * factor);
            color.g = Mathf.Clamp01(color.g * factor);
            color.b = Mathf.Clamp01(color.b * factor);
            material.SetColor(propertyName, color);
        }

        private static void SetFloatIfPresent(Material material, string propertyName, float value)
        {
            if (material.HasProperty(propertyName))
            {
                material.SetFloat(propertyName, value);
            }
        }

        private static void ImproveTexture(Material material, string propertyName)
        {
            if (!material.HasProperty(propertyName))
            {
                return;
            }
            var texture = material.GetTexture(propertyName);
            if (texture == null)
            {
                return;
            }
            texture.filterMode = FilterMode.Trilinear;
            texture.anisoLevel = 8;
            texture.mipMapBias = -0.35f;
        }

        private void FrameCameraToAvatar()
        {
            var camera = EnsureRuntimeCamera();
            if (camera == null || _renderers.Count == 0)
            {
                return;
            }
            var bounds = CalculateAvatarBounds();
            if (bounds.size == Vector3.zero)
            {
                return;
            }
            var center = bounds.center;
            var height = Mathf.Max(0.2f, bounds.size.y);
            var width = Mathf.Max(0.2f, bounds.size.x);
            var bodyCenter = ResolveBodyCenter(center);
            camera.orthographic = true;
            var aspect = Mathf.Max(0.1f, camera.aspect);
            var halfHeightByBody = height * fullBodyCameraMargin;
            var halfHeightByWidth = width / aspect * 0.68f;
            camera.orthographicSize = Mathf.Clamp(Mathf.Max(halfHeightByBody, halfHeightByWidth, 1.18f), 1.18f, 4.2f);
            var target = new Vector3(bodyCenter.x + cameraHorizontalOffset, center.y + height * cameraVerticalLiftRatio, center.z);
            camera.transform.position = target + new Vector3(0, 0, -3.2f);
            camera.transform.LookAt(target);
            camera.nearClipPlane = 0.03f;
            camera.farClipPlane = 20.0f;
            Debug.Log(
                $"Natsume avatar camera framed: center={center}, bodyCenter={bodyCenter}, size={bounds.size}, aspect={aspect:F3}, ortho={camera.orthographicSize:F3}, target={target}, camera={camera.name}");
        }

        private Camera EnsureRuntimeCamera()
        {
            if (_runtimeCamera == null)
            {
                var named = GameObject.Find("Main Camera");
                _runtimeCamera = named != null ? named.GetComponent<Camera>() : null;
                if (_runtimeCamera == null)
                {
                    _runtimeCamera = Camera.main;
                }
                if (_runtimeCamera == null)
                {
                    var cameraObject = new GameObject("Main Camera");
                    _runtimeCamera = cameraObject.AddComponent<Camera>();
                }
                _runtimeCamera.gameObject.name = "Main Camera";
                _runtimeCamera.tag = "MainCamera";
            }

            foreach (var camera in FindObjectsOfType<Camera>())
            {
                if (camera == _runtimeCamera)
                {
                    continue;
                }
                camera.enabled = false;
            }

            _runtimeCamera.enabled = true;
            _runtimeCamera.depth = 100.0f;
            return _runtimeCamera;
        }

        private void CaptureScreenshot(string requestedPath)
        {
            var path = string.IsNullOrWhiteSpace(requestedPath)
                ? Path.Combine(Application.persistentDataPath, "natsume_avatar_runtime.png")
                : requestedPath;
            var directory = Path.GetDirectoryName(path);
            if (!string.IsNullOrWhiteSpace(directory))
            {
                Directory.CreateDirectory(directory);
            }
            StartCoroutine(CaptureScreenshotCoroutine(path));
        }

        private IEnumerator CaptureScreenshotCoroutine(string path)
        {
            yield return new WaitForEndOfFrame();
            var texture = new Texture2D(Screen.width, Screen.height, TextureFormat.RGB24, false);
            texture.ReadPixels(new Rect(0, 0, Screen.width, Screen.height), 0, 0);
            texture.Apply();
            File.WriteAllBytes(path, texture.EncodeToPNG());
            Destroy(texture);
            Debug.Log($"Natsume avatar screenshot requested: {path}");
        }

        private Vector3 ResolveBodyCenter(Vector3 fallback)
        {
            if (_animator == null)
            {
                return fallback;
            }
            var hips = _animator.GetBoneTransform(HumanBodyBones.Hips);
            var head = _animator.GetBoneTransform(HumanBodyBones.Head);
            if (hips != null && head != null)
            {
                return Vector3.Lerp(hips.position, head.position, 0.52f);
            }
            var chest = _animator.GetBoneTransform(HumanBodyBones.Chest);
            if (chest != null && head != null)
            {
                return Vector3.Lerp(chest.position, head.position, 0.35f);
            }
            return fallback;
        }

        private Bounds CalculateAvatarBounds()
        {
            var initialized = false;
            var bounds = new Bounds(Vector3.zero, Vector3.zero);
            foreach (var renderer in _renderers)
            {
                if (renderer == null)
                {
                    continue;
                }
                if (!initialized)
                {
                    bounds = renderer.bounds;
                    initialized = true;
                }
                else
                {
                    bounds.Encapsulate(renderer.bounds);
                }
            }
            return bounds;
        }

        private void ApplyPose(float t)
        {
            var faceYaw = Mathf.Sin(t * 0.42f) * 4.0f * idleGain + Motion("FaceAngleY");
            var facePitch = Mathf.Sin(t * 0.74f) * 2.0f * idleGain + Motion("FaceAngleX");
            var faceRoll = Mathf.Sin(t * 0.31f + 0.5f) * 1.6f * idleGain + Motion("FaceAngleZ");
            var leftLift = Motion("HandLeftPositionY");
            var rightLift = Motion("HandRightPositionY");
            var activeAction = !string.IsNullOrEmpty(_bodyAction) && Time.time - _bodyActionStartedAt < _bodyActionDuration;
            var liftScale = activeAction ? 1.2f : 0.42f;
            var ambient = EvaluateAmbientPose(t);
            faceYaw += ambient.faceYaw;
            facePitch += ambient.facePitch;
            faceRoll += ambient.faceRoll;

            if (_poseHandler != null)
            {
                ApplyHumanoidIdlePose(t, facePitch, faceYaw, faceRoll, leftLift, rightLift, activeAction, liftScale, ambient);
                ApplyBodyAction(t);
                return;
            }

            SetBone(HumanBodyBones.Head, facePitch, faceYaw, faceRoll);
            SetBone(HumanBodyBones.Neck, facePitch * 0.22f, faceYaw * 0.22f, faceRoll * 0.22f);
            SetBone(HumanBodyBones.Chest, Motion("MocopiBodyAngleX") * 0.35f + ambient.chestFrontBack * 75.0f, Motion("MocopiBodyAngleY") * 0.35f, Motion("MocopiBodyAngleZ") * 0.35f + ambient.chestLeftRight * 75.0f);
            SetBone(HumanBodyBones.LeftUpperArm, -10 + leftLift * -18, -14 + Motion("MocopiUpperArmLeftAngleY") * 0.5f + ambient.leftArmFrontBack * 70.0f, 38 - leftLift * liftScale * 55 + Motion("HandLeftAngleZ") * 0.2f - ambient.leftArmDownUp * 65.0f);
            SetBone(HumanBodyBones.RightUpperArm, -10 + rightLift * -18, 14 + Motion("MocopiUpperArmRightAngleY") * 0.5f + ambient.rightArmFrontBack * 70.0f, -38 + rightLift * liftScale * 55 + Motion("HandRightAngleZ") * 0.2f + ambient.rightArmDownUp * 65.0f);
            SetBone(HumanBodyBones.LeftLowerArm, 8 + Motion("MocopiLowerArmLeftAngleX") * 0.45f, 0, Motion("HandLeftAngleZ") * 0.18f);
            SetBone(HumanBodyBones.RightLowerArm, 8 + Motion("MocopiLowerArmRightAngleX") * 0.45f, 0, Motion("HandRightAngleZ") * 0.18f);

            ApplyBodyAction(t);
        }

        private void ApplyRelaxedArmBones(float t, float leftLift, float rightLift, bool activeAction, float liftScale)
        {
            var idleSway = Mathf.Sin(t * 0.58f) * 2.2f * idleGain;
            var actionLift = activeAction ? liftScale : 0.32f;
            SetBone(HumanBodyBones.LeftUpperArm, -7 + idleSway * 0.2f, -18, 68 - leftLift * actionLift * 42);
            SetBone(HumanBodyBones.RightUpperArm, -7 - idleSway * 0.2f, 18, -68 + rightLift * actionLift * 42);
            SetBone(HumanBodyBones.LeftLowerArm, 10 + idleSway * 0.15f, 4, 8 + Motion("HandLeftAngleZ") * 0.12f);
            SetBone(HumanBodyBones.RightLowerArm, 10 - idleSway * 0.15f, -4, -8 + Motion("HandRightAngleZ") * 0.12f);
        }

        private void ApplyHumanoidIdlePose(float t, float facePitch, float faceYaw, float faceRoll, float leftLift, float rightLift, bool activeAction, float liftScale, AmbientPose ambient)
        {
            _poseHandler.GetHumanPose(ref _humanPose);
            var muscles = _humanPose.muscles;
            SetMuscle(muscles, "Spine Front-Back", Mathf.Sin(t * 0.53f) * 0.018f * idleGain + Motion("MocopiBodyAngleX") * 0.006f + ambient.spineFrontBack);
            SetMuscle(muscles, "Chest Front-Back", Mathf.Sin(t * 0.47f + 1.0f) * 0.026f * idleGain + Motion("MocopiBodyAngleX") * 0.006f + ambient.chestFrontBack);
            SetMuscle(muscles, "Chest Left-Right", Motion("MocopiBodyAngleZ") * 0.01f + ambient.chestLeftRight);
            SetMuscle(muscles, "Neck Nod Down-Up", facePitch / 55.0f);
            SetMuscle(muscles, "Head Nod Down-Up", facePitch / 45.0f);
            SetMuscle(muscles, "Head Turn Left-Right", faceYaw / 65.0f);
            SetMuscle(muscles, "Head Tilt Left-Right", faceRoll / 55.0f);

            var leftArmUp = 0.58f - leftLift * liftScale * 0.85f;
            var rightArmUp = 0.58f - rightLift * liftScale * 0.85f;
            var armSway = Mathf.Sin(t * 0.66f) * 0.025f * idleGain;
            SetMuscle(muscles, "Left Shoulder Down-Up", 0.10f);
            SetMuscle(muscles, "Right Shoulder Down-Up", 0.10f);
            SetMuscle(muscles, "Left Arm Down-Up", leftArmUp + armSway + ambient.leftArmDownUp);
            SetMuscle(muscles, "Right Arm Down-Up", rightArmUp - armSway + ambient.rightArmDownUp);
            SetMuscle(muscles, "Left Arm Front-Back", -0.08f + Motion("MocopiUpperArmLeftAngleY") * 0.004f + ambient.leftArmFrontBack);
            SetMuscle(muscles, "Right Arm Front-Back", -0.08f + Motion("MocopiUpperArmRightAngleY") * 0.004f + ambient.rightArmFrontBack);
            SetMuscle(muscles, "Left Forearm Stretch", 0.24f);
            SetMuscle(muscles, "Right Forearm Stretch", 0.24f);
            SetMuscle(muscles, "Left Hand Down-Up", -0.05f + Motion("HandLeftAngleZ") * 0.004f + ambient.leftHandDownUp);
            SetMuscle(muscles, "Right Hand Down-Up", -0.05f + Motion("HandRightAngleZ") * 0.004f + ambient.rightHandDownUp);

            _humanPose.muscles = muscles;
            _humanPose.bodyPosition = _baseHumanBodyPosition;
            _humanPose.bodyRotation = _baseHumanBodyRotation;
            _poseHandler.SetHumanPose(ref _humanPose);
        }

        private void SetMuscle(float[] muscles, string muscleName, float value)
        {
            if (_muscleIndex.TryGetValue(muscleName, out var index) && index >= 0 && index < muscles.Length)
            {
                muscles[index] = Mathf.Clamp(value, -1.0f, 1.0f);
            }
        }

        private void ApplyBodyAction(float t)
        {
            if (string.IsNullOrEmpty(_bodyAction))
            {
                return;
            }
            var p = Mathf.Clamp01((t - _bodyActionStartedAt) / Mathf.Max(0.1f, _bodyActionDuration));
            if (p >= 1)
            {
                _bodyAction = "";
                ScheduleNextAmbientAction(t);
                return;
            }
            var ease = Mathf.Sin(Mathf.PI * p) * Mathf.Lerp(0.7f, 1.3f, _bodyActionIntensity);
            if (_bodyAction == "wave" || _bodyAction == "soft_wave" || _bodyAction == "greet")
            {
                var wave = Mathf.Sin(p * Mathf.PI * 6.0f) * 11.0f * ease;
                SetBone(HumanBodyBones.Chest, -2.0f * ease, 0, -3.5f * ease);
                SetBone(HumanBodyBones.Head, -2.0f * ease, -4.0f * ease, 1.8f * ease);
                SetBone(HumanBodyBones.RightUpperArm, -18.0f, 24.0f, -64.0f + wave);
                SetBone(HumanBodyBones.RightLowerArm, 34.0f, 0, -28.0f + Mathf.Sin(p * Mathf.PI * 8.0f) * 15.0f * ease);
                SetBone(HumanBodyBones.LeftUpperArm, -7.0f, -15.0f, 48.0f - 5.0f * ease);
            }
            else if (_bodyAction == "happy_bounce")
            {
                SetBone(HumanBodyBones.Chest, 4 * ease, 0, Mathf.Sin(p * Mathf.PI * 2.0f) * 8.0f * ease);
                SetBone(HumanBodyBones.LeftUpperArm, -18, -18, 28 - 22 * ease);
                SetBone(HumanBodyBones.RightUpperArm, -18, 18, -28 + 22 * ease);
            }
            else if (_bodyAction == "idle_shift")
            {
                SetBone(HumanBodyBones.Chest, -2.4f * ease, 0, Mathf.Sin(p * Mathf.PI * 2.0f) * 5.5f * ease);
                SetBone(HumanBodyBones.Head, 1.6f * ease, Mathf.Sin(p * Mathf.PI * 2.0f) * 4.0f * ease, -2.2f * ease);
                SetBone(HumanBodyBones.LeftUpperArm, -6, -16, 45 + 4.0f * ease);
                SetBone(HumanBodyBones.RightUpperArm, -6, 16, -45 - 4.0f * ease);
            }
            else if (_bodyAction == "think" || _bodyAction == "thinking")
            {
                SetBone(HumanBodyBones.Head, -4.5f * ease, -5.5f * ease, 2.0f * ease);
                SetBone(HumanBodyBones.Chest, -2.5f * ease, 0, -3.0f * ease);
            }
            else if (_bodyAction == "shy")
            {
                SetBone(HumanBodyBones.Head, 4.0f * ease, -6.0f * ease, 3.0f * ease);
                SetBone(HumanBodyBones.Chest, -3.5f * ease, 0, 2.5f * ease);
                SetBone(HumanBodyBones.LeftUpperArm, -14.0f, -14.0f, 40.0f - 18.0f * ease);
                SetBone(HumanBodyBones.RightUpperArm, -14.0f, 14.0f, -40.0f + 18.0f * ease);
                SetBone(HumanBodyBones.LeftLowerArm, 18.0f * ease, 0, 8.0f);
                SetBone(HumanBodyBones.RightLowerArm, 18.0f * ease, 0, -8.0f);
            }
            else if (_bodyAction == "lean_in" || _bodyAction == "curious")
            {
                SetBone(HumanBodyBones.Chest, -5.5f * ease, 0, 0);
                SetBone(HumanBodyBones.Head, -3.0f * ease, Mathf.Sin(p * Mathf.PI * 2.0f) * 3.0f * ease, 0);
                SetBone(HumanBodyBones.LeftUpperArm, -8.0f, -16.0f, 48.0f);
                SetBone(HumanBodyBones.RightUpperArm, -8.0f, 16.0f, -48.0f);
            }
            else if (_bodyAction == "nod")
            {
                SetBone(HumanBodyBones.Head, Mathf.Sin(p * Mathf.PI * 4.0f) * 8.0f * ease, 0, 0);
            }
        }

        private float Motion(string key)
        {
            if (Time.time > _externalMotionUntil)
            {
                return 0;
            }
            return _motionValues.TryGetValue(key, out var value) ? value : 0;
        }

        private void SetBone(HumanBodyBones bone, float xDeg, float yDeg, float zDeg)
        {
            if (_animator == null || !_baseRotations.TryGetValue(bone, out var baseRotation))
            {
                return;
            }
            var tx = _animator.GetBoneTransform(bone);
            if (tx == null)
            {
                return;
            }
            tx.localRotation = baseRotation * Quaternion.Euler(xDeg, yDeg, zDeg);
        }

        private void ApplyExpression()
        {
            var mouth = _talking ? Mathf.Max(_mouthValue, 0.08f) : _mouthValue;
            SetBlendShapeContains("aa", mouth * 100.0f);
            SetBlendShapeContains("mouth", mouth * 70.0f);
            var smile = IsHappy(_emotionTag) ? Mathf.Lerp(8, 35, _emotionIntensity) : 0;
            SetBlendShapeContains("happy", smile);
            SetBlendShapeContains("surprised", _emotionTag == "SURPRISED" ? _emotionIntensity * 80 : 0);
            SetBlendShapeContains("angry", _emotionTag == "ANGRY" ? _emotionIntensity * 80 : 0);
            SetBlendShapeContains("sad", _emotionTag == "SAD" ? _emotionIntensity * 70 : 0);
        }

        private void SetBlendShapeContains(string token, float weight)
        {
            foreach (var renderer in _renderers)
            {
                var mesh = renderer.sharedMesh;
                if (mesh == null)
                {
                    continue;
                }
                for (var i = 0; i < mesh.blendShapeCount; i++)
                {
                    var name = mesh.GetBlendShapeName(i);
                    if (name.IndexOf(token, StringComparison.OrdinalIgnoreCase) >= 0)
                    {
                        renderer.SetBlendShapeWeight(i, Mathf.Clamp(weight, 0, 100));
                    }
                }
            }
        }

        private static bool IsHappy(string tag)
        {
            return tag == "HAPPY" || tag == "JOY" || tag == "PLAYFUL" || tag == "LOVING" || tag == "EXCITED";
        }
    }
}
