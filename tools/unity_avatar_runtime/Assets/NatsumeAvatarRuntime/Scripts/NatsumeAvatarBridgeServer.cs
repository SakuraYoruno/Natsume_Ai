using System;
using System.Collections.Concurrent;
using System.Net;
using System.Text;
using System.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace Natsume.AvatarRuntime
{
    public sealed class NatsumeAvatarBridgeServer : MonoBehaviour
    {
        [SerializeField] private string host = "127.0.0.1";
        [SerializeField] private int port = 18770;
        [SerializeField] private NatsumeVrmAvatarRuntime avatarRuntime;

        private readonly ConcurrentQueue<AvatarBridgeCommand> _commandQueue = new ConcurrentQueue<AvatarBridgeCommand>();
        private HttpListener _listener;
        private Thread _listenerThread;
        private volatile bool _running;
        private long _commandCount;
        private string _lastCommandType = "";

        public int Port => port;
        public long CommandCount => Interlocked.Read(ref _commandCount);

        private void Awake()
        {
            if (avatarRuntime == null)
            {
                avatarRuntime = GetComponent<NatsumeVrmAvatarRuntime>();
            }
        }

        private void OnEnable()
        {
            StartServer();
        }

        private void OnDisable()
        {
            StopServer();
        }

        private void Update()
        {
            while (_commandQueue.TryDequeue(out var command))
            {
                _lastCommandType = command.type ?? "";
                if (avatarRuntime != null)
                {
                    avatarRuntime.ApplyCommand(command);
                }
            }
        }

        [ContextMenu("Start Bridge Server")]
        public void StartServer()
        {
            if (_running)
            {
                return;
            }

            _listener = new HttpListener();
            _listener.Prefixes.Add($"http://{host}:{port}/");
            _listener.Start();
            _running = true;
            _listenerThread = new Thread(ListenLoop)
            {
                IsBackground = true,
                Name = "NatsumeAvatarBridgeServer",
            };
            _listenerThread.Start();
            Debug.Log($"Natsume Avatar Bridge listening at http://{host}:{port}/");
        }

        [ContextMenu("Stop Bridge Server")]
        public void StopServer()
        {
            _running = false;
            try
            {
                _listener?.Stop();
                _listener?.Close();
            }
            catch (Exception)
            {
                // Best-effort shutdown.
            }
            _listener = null;
            _listenerThread = null;
        }

        private void ListenLoop()
        {
            while (_running && _listener != null)
            {
                try
                {
                    var context = _listener.GetContext();
                    ThreadPool.QueueUserWorkItem(_ => HandleContext(context));
                }
                catch (HttpListenerException)
                {
                    if (_running)
                    {
                        Debug.LogWarning("Natsume Avatar Bridge listener interrupted.");
                    }
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"Natsume Avatar Bridge listen error: {ex.Message}");
                }
            }
        }

        private void HandleContext(HttpListenerContext context)
        {
            var request = context.Request;
            var path = request.Url != null ? request.Url.AbsolutePath.Trim('/').ToLowerInvariant() : "";
            if (request.HttpMethod == "GET" && path == "health")
            {
                WriteJson(context.Response, new
                {
                    status = "ok",
                    bridge = "unity",
                    port,
                    command_count = CommandCount,
                    last_command_type = _lastCommandType,
                    avatar_loaded = avatarRuntime != null && avatarRuntime.IsAvatarLoaded,
                    avatar_path = avatarRuntime != null ? avatarRuntime.VrmPath : null,
                    body_action = avatarRuntime != null ? avatarRuntime.CurrentBodyAction : null,
                    ambient_action = avatarRuntime != null ? avatarRuntime.CurrentAmbientAction : null,
                });
                return;
            }

            if (request.HttpMethod == "POST" && path == "command")
            {
                try
                {
                    using var reader = new System.IO.StreamReader(request.InputStream, request.ContentEncoding);
                    var raw = reader.ReadToEnd();
                    var command = ParseCommand(raw);
                    command.received_at = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                    _commandQueue.Enqueue(command);
                    Interlocked.Increment(ref _commandCount);
                    WriteJson(context.Response, new
                    {
                        success = true,
                        detail = "unity bridge command accepted",
                        command_count = CommandCount,
                    });
                }
                catch (Exception ex)
                {
                    WriteJson(context.Response, new
                    {
                        success = false,
                        detail = ex.Message,
                    }, 400);
                }
                return;
            }

            WriteJson(context.Response, new { success = false, detail = "not found" }, 404);
        }

        private static AvatarBridgeCommand ParseCommand(string raw)
        {
            var root = JObject.Parse(raw);
            var command = root.ToObject<AvatarBridgeCommand>() ?? new AvatarBridgeCommand();
            var motion = root["motion_values"] as JObject;
            if (motion != null)
            {
                command.motion_values.Clear();
                foreach (var item in motion)
                {
                    if (item.Value != null && item.Value.Type != JTokenType.Null)
                    {
                        command.motion_values[item.Key] = item.Value.Value<float>();
                    }
                }
            }
            return command;
        }

        private static void WriteJson(HttpListenerResponse response, object payload, int statusCode = 200)
        {
            var raw = JsonConvert.SerializeObject(payload);
            var bytes = Encoding.UTF8.GetBytes(raw);
            response.StatusCode = statusCode;
            response.ContentType = "application/json; charset=utf-8";
            response.ContentLength64 = bytes.Length;
            response.OutputStream.Write(bytes, 0, bytes.Length);
            response.OutputStream.Close();
        }
    }
}
