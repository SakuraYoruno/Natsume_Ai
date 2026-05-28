using System;
using System.IO;
using System.Reflection;
using Natsume.AvatarRuntime;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Natsume.AvatarRuntime.Editor
{
    public static class NatsumeAvatarRuntimeSetup
    {
        private const string ScenePath = "Assets/Scenes/NatsumeAvatarRuntime.unity";
        private const int RuntimeGameViewWidth = 1920;
        private const int RuntimeGameViewHeight = 1080;
        private const string RuntimeGameViewLabel = "Natsume 1920x1080";
        private const string PlayerOutputRelativePath = @"..\..\..\artifacts\unity_avatar_runtime\NatsumeAvatarRuntime.exe";
        private static bool _playModeScheduled;

        [MenuItem("Natsume/Avatar Runtime/Setup Scene")]
        public static void SetupScene()
        {
            Directory.CreateDirectory("Assets/Scenes");
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

            var runtime = new GameObject("NatsumeAvatarRuntime");
            var loader = runtime.AddComponent<NatsumeVrmAvatarRuntime>();
            runtime.AddComponent<NatsumeAvatarBridgeServer>();

            var cameraObject = new GameObject("Main Camera");
            var camera = cameraObject.AddComponent<Camera>();
            camera.tag = "MainCamera";
            camera.transform.position = new Vector3(0, 1.35f, -4.2f);
            camera.transform.rotation = Quaternion.Euler(4, 0, 0);
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.93f, 0.95f, 0.98f);

            var lightObject = new GameObject("Key Light");
            var light = lightObject.AddComponent<Light>();
            light.type = LightType.Directional;
            light.intensity = 1.4f;
            light.transform.rotation = Quaternion.Euler(45, -25, 0);

            _ = loader;
            EditorSceneManager.SaveScene(scene, ScenePath);
            EditorSceneManager.OpenScene(ScenePath);
            Debug.Log($"Natsume avatar runtime scene created: {ScenePath}");
        }

        public static void LaunchForRuntime()
        {
            ConfigureGameViewForRuntime();
            if (EditorApplication.isPlayingOrWillChangePlaymode)
            {
                return;
            }
            if (!File.Exists(ScenePath))
            {
                SetupScene();
            }
            else
            {
                EditorSceneManager.OpenScene(ScenePath);
            }

            SchedulePlayMode();
        }

        [MenuItem("Natsume/Avatar Runtime/Build Windows Player")]
        public static void BuildWindowsPlayer()
        {
            if (!File.Exists(ScenePath))
            {
                SetupScene();
            }
            EnsureRuntimeShadersIncluded();

            var outputPath = Path.GetFullPath(Path.Combine(Application.dataPath, PlayerOutputRelativePath));
            Directory.CreateDirectory(Path.GetDirectoryName(outputPath) ?? ".");
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = new[] { ScenePath },
                locationPathName = outputPath,
                target = BuildTarget.StandaloneWindows64,
                options = BuildOptions.None,
            });

            if (report.summary.result != BuildResult.Succeeded)
            {
                throw new InvalidOperationException(
                    $"Natsume avatar runtime player build failed: {report.summary.result} ({report.summary.totalErrors} errors)");
            }

            Debug.Log($"Natsume avatar runtime player built: {outputPath}");
        }

        private static void EnsureRuntimeShadersIncluded()
        {
            var assets = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/GraphicsSettings.asset");
            if (assets == null || assets.Length == 0)
            {
                Debug.LogWarning("Natsume avatar runtime could not load GraphicsSettings.asset.");
                return;
            }

            var graphicsSettings = new SerializedObject(assets[0]);
            var alwaysIncludedShaders = graphicsSettings.FindProperty("m_AlwaysIncludedShaders");
            if (alwaysIncludedShaders == null || !alwaysIncludedShaders.isArray)
            {
                Debug.LogWarning("Natsume avatar runtime could not find Always Included Shaders.");
                return;
            }

            var changed = false;
            changed |= AddAlwaysIncludedShader(alwaysIncludedShaders, "VRM10/MToon10");
            changed |= AddAlwaysIncludedShader(alwaysIncludedShaders, "UniGLTF/UniUnlit");
            changed |= AddAlwaysIncludedShader(alwaysIncludedShaders, "Standard");

            if (changed)
            {
                graphicsSettings.ApplyModifiedProperties();
                AssetDatabase.SaveAssets();
                Debug.Log("Natsume avatar runtime updated Always Included Shaders for player builds.");
            }
        }

        private static bool AddAlwaysIncludedShader(SerializedProperty shaders, string shaderName)
        {
            var shader = Shader.Find(shaderName);
            if (shader == null)
            {
                Debug.LogWarning($"Natsume avatar runtime could not find shader: {shaderName}");
                return false;
            }

            for (var i = 0; i < shaders.arraySize; i++)
            {
                var item = shaders.GetArrayElementAtIndex(i);
                if (item.objectReferenceValue == shader)
                {
                    return false;
                }
            }

            var index = shaders.arraySize;
            shaders.InsertArrayElementAtIndex(index);
            shaders.GetArrayElementAtIndex(index).objectReferenceValue = shader;
            Debug.Log($"Natsume avatar runtime included shader in player build: {shaderName}");
            return true;
        }

        [InitializeOnLoadMethod]
        private static void AutoSetupSceneOnce()
        {
            if (Environment.GetEnvironmentVariable("NATSUME_AVATAR_AUTOPLAY") == "1")
            {
                EditorApplication.delayCall += LaunchForRuntime;
                return;
            }

            var key = "Natsume.AvatarRuntime.SceneSetup." + Application.dataPath;
            if (EditorPrefs.GetBool(key, false) && File.Exists(ScenePath))
            {
                return;
            }
            EditorApplication.delayCall += () =>
            {
                if (!File.Exists(ScenePath))
                {
                    SetupScene();
                }
                EditorPrefs.SetBool(key, true);
            };
        }

        private static void SchedulePlayMode()
        {
            if (_playModeScheduled)
            {
                return;
            }
            _playModeScheduled = true;
            EditorApplication.update += EnterPlayModeWhenReady;
        }

        private static void EnterPlayModeWhenReady()
        {
            if (EditorApplication.isCompiling || EditorApplication.isUpdating)
            {
                return;
            }
            EditorApplication.update -= EnterPlayModeWhenReady;
            _playModeScheduled = false;
            if (!EditorApplication.isPlayingOrWillChangePlaymode)
            {
                ConfigureGameViewForRuntime();
                EditorApplication.isPlaying = true;
            }
        }

        private static void ConfigureGameViewForRuntime()
        {
            try
            {
                SetGameViewFixedResolution(RuntimeGameViewWidth, RuntimeGameViewHeight, RuntimeGameViewLabel);
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"Natsume avatar runtime could not set Game View resolution: {ex.Message}");
            }

            ResetGameViewZoom();
            EditorApplication.delayCall += ResetGameViewZoom;
        }

        private static void SetGameViewFixedResolution(int width, int height, string label)
        {
            var editorAssembly = typeof(EditorWindow).Assembly;
            var gameViewSizesType = editorAssembly.GetType("UnityEditor.GameViewSizes");
            var groupEnumType = editorAssembly.GetType("UnityEditor.GameViewSizeGroupType");
            var gameViewSizeType = editorAssembly.GetType("UnityEditor.GameViewSize");
            var sizeKindType = editorAssembly.GetType("UnityEditor.GameViewSizeType");
            var gameViewType = editorAssembly.GetType("UnityEditor.GameView");
            if (gameViewSizesType == null || groupEnumType == null || gameViewSizeType == null || sizeKindType == null || gameViewType == null)
            {
                throw new InvalidOperationException("Unity GameView internal types are unavailable.");
            }

            var gameViewSizes = GetGameViewSizesInstance(gameViewSizesType);
            var standaloneGroup = Enum.Parse(groupEnumType, "Standalone");
            var getGroup = gameViewSizesType.GetMethod("GetGroup", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            var group = getGroup?.Invoke(gameViewSizes, new[] { standaloneGroup });
            if (group == null)
            {
                throw new InvalidOperationException("Standalone GameView size group is unavailable.");
            }

            var selectedIndex = FindGameViewSizeIndex(group, label, width, height);
            if (selectedIndex < 0)
            {
                selectedIndex = GetGameViewSizeCount(group);
                var fixedResolution = Enum.Parse(sizeKindType, "FixedResolution");
                var customSize = CreateGameViewSize(gameViewSizeType, sizeKindType, fixedResolution, width, height, label);
                var addCustomSize = group.GetType().GetMethod("AddCustomSize", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (addCustomSize == null)
                {
                    throw new InvalidOperationException("GameView AddCustomSize method is unavailable.");
                }
                addCustomSize.Invoke(group, new[] { customSize });
            }

            var gameView = EditorWindow.GetWindow(gameViewType);
            SetGameViewSelectedSize(gameViewType, gameView, selectedIndex);
            gameView.Show();
            gameView.Repaint();
            Debug.Log($"Natsume avatar runtime selected Game View size: {label} ({width}x{height}) index={selectedIndex}");
        }

        private static object GetGameViewSizesInstance(Type gameViewSizesType)
        {
            var singletonType = typeof(ScriptableSingleton<>).MakeGenericType(gameViewSizesType);
            var instanceProperty =
                singletonType.GetProperty("instance", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic) ??
                gameViewSizesType.GetProperty("instance", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
            var instance = instanceProperty?.GetValue(null);
            if (instance == null)
            {
                throw new InvalidOperationException("GameViewSizes singleton is unavailable.");
            }
            return instance;
        }

        private static int FindGameViewSizeIndex(object group, string label, int width, int height)
        {
            var count = GetGameViewSizeCount(group);
            var getSize = group.GetType().GetMethod("GetGameViewSize", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (getSize == null)
            {
                throw new InvalidOperationException("GameView GetGameViewSize method is unavailable.");
            }

            for (var i = 0; i < count; i++)
            {
                var size = getSize.Invoke(group, new object[] { i });
                if (size == null)
                {
                    continue;
                }

                var displayText = ReadStringMember(size, "displayText");
                var baseText = ReadStringMember(size, "baseText");
                var sizeWidth = ReadIntMember(size, "width");
                var sizeHeight = ReadIntMember(size, "height");
                if (displayText.Contains(label) || baseText.Contains(label) || (sizeWidth == width && sizeHeight == height))
                {
                    return i;
                }
            }
            return -1;
        }

        private static int GetGameViewSizeCount(object group)
        {
            var type = group.GetType();
            var totalMethod = type.GetMethod("GetTotalCount", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (totalMethod != null)
            {
                return (int)totalMethod.Invoke(group, null);
            }

            var builtinMethod = type.GetMethod("GetBuiltinCount", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            var customMethod = type.GetMethod("GetCustomCount", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (builtinMethod == null || customMethod == null)
            {
                throw new InvalidOperationException("GameView size count methods are unavailable.");
            }
            return (int)builtinMethod.Invoke(group, null) + (int)customMethod.Invoke(group, null);
        }

        private static object CreateGameViewSize(Type gameViewSizeType, Type sizeKindType, object fixedResolution, int width, int height, string label)
        {
            foreach (var constructor in gameViewSizeType.GetConstructors(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic))
            {
                var parameters = constructor.GetParameters();
                if (parameters.Length == 4 &&
                    parameters[0].ParameterType == sizeKindType &&
                    parameters[1].ParameterType == typeof(int) &&
                    parameters[2].ParameterType == typeof(int) &&
                    parameters[3].ParameterType == typeof(string))
                {
                    return constructor.Invoke(new[] { fixedResolution, width, height, label });
                }
            }
            throw new InvalidOperationException("GameViewSize constructor is unavailable.");
        }

        private static void SetGameViewSelectedSize(Type gameViewType, EditorWindow gameView, int selectedIndex)
        {
            var selectedProperty = gameViewType.GetProperty("selectedSizeIndex", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (selectedProperty != null && selectedProperty.CanWrite)
            {
                selectedProperty.SetValue(gameView, selectedIndex);
                return;
            }

            var selectedField = gameViewType.GetField("m_SelectedSizeIndex", BindingFlags.Instance | BindingFlags.NonPublic);
            if (selectedField == null)
            {
                throw new InvalidOperationException("GameView selected size member is unavailable.");
            }
            selectedField.SetValue(gameView, selectedIndex);
        }

        private static string ReadStringMember(object target, string name)
        {
            var type = target.GetType();
            var property = type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (property != null)
            {
                return property.GetValue(target)?.ToString() ?? "";
            }
            var field = type.GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            return field?.GetValue(target)?.ToString() ?? "";
        }

        private static int ReadIntMember(object target, string name)
        {
            var type = target.GetType();
            var property = type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (property != null && property.GetValue(target) is int propertyValue)
            {
                return propertyValue;
            }
            var field = type.GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            return field?.GetValue(target) is int fieldValue ? fieldValue : -1;
        }

        private static void ResetGameViewZoom()
        {
            try
            {
                var gameViewType = typeof(EditorWindow).Assembly.GetType("UnityEditor.GameView");
                if (gameViewType == null)
                {
                    return;
                }

                var gameView = EditorWindow.GetWindow(gameViewType);
                var zoomAreaField = gameViewType.GetField("m_ZoomArea", BindingFlags.Instance | BindingFlags.NonPublic);
                var zoomArea = zoomAreaField?.GetValue(gameView);
                if (zoomArea == null)
                {
                    return;
                }

                var focus = new Vector2(gameView.position.width * 0.5f, gameView.position.height * 0.5f);
                MethodInfo setScaleFocused = null;
                foreach (var method in zoomArea.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic))
                {
                    if (method.Name != "SetScaleFocused")
                    {
                        continue;
                    }
                    var parameters = method.GetParameters();
                    if (parameters.Length == 2 && parameters[0].ParameterType == typeof(Vector2))
                    {
                        setScaleFocused = method;
                        break;
                    }
                }
                if (setScaleFocused != null)
                {
                    setScaleFocused.Invoke(zoomArea, new object[] { Vector2.one, focus });
                }
                else
                {
                    var scaleField = zoomArea.GetType().GetField("m_Scale", BindingFlags.Instance | BindingFlags.NonPublic);
                    scaleField?.SetValue(zoomArea, Vector2.one);
                }
                gameView.Repaint();
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"Natsume avatar runtime could not reset Game View zoom: {ex.Message}");
            }
        }

        [MenuItem("Natsume/Avatar Runtime/Open VRM Folder")]
        public static void OpenVrmFolder()
        {
            var path = Environment.GetEnvironmentVariable("NATSUME_AIMIS_VRM_PATH");
            if (string.IsNullOrWhiteSpace(path))
            {
                path = @"C:\Users\34145\Downloads\鸣潮_爱弥斯1.05(1.0)\鸣潮_爱弥斯1.05(1.0).vrm";
            }
            EditorUtility.RevealInFinder(path);
        }
    }
}
