using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using UnityEngine;

namespace Natsume.MateEngineBridge;

internal sealed class BridgeSecurityGate
{
    internal const string AuthVersion = "natsume.bridge-auth.v1";
    internal const string PluginScope = "bridge:mateengine:plugin";
    internal const string PluginPath = "/plugin-command";
    private const string Transport = "udp";
    private const string Method = "DATAGRAM";

    private readonly string _mode;
    private readonly string _keyId;
    private readonly byte[] _secret;
    private readonly string _principal;
    private readonly long _ttlMilliseconds;
    private readonly int _maxBodyBytes;
    private readonly DurableReplayLedger _replayLedger;
    private readonly DurableAuditLog _auditLog;
    private readonly TokenBucket _rateLimiter;
    private readonly TokenBucket _preAuthLimiter;

    private BridgeSecurityGate(
        string mode,
        int port,
        int queueCapacity,
        string keyId,
        byte[] secret,
        string principal,
        long ttlMilliseconds,
        int maxBodyBytes,
        DurableReplayLedger replayLedger,
        DurableAuditLog auditLog,
        TokenBucket rateLimiter)
    {
        _mode = mode;
        Port = port;
        QueueCapacity = queueCapacity;
        _keyId = keyId;
        _secret = secret;
        _principal = principal;
        _ttlMilliseconds = ttlMilliseconds;
        _maxBodyBytes = maxBodyBytes;
        _replayLedger = replayLedger;
        _auditLog = auditLog;
        _rateLimiter = rateLimiter;
        _preAuthLimiter = new TokenBucket(
            Math.Max(50.0, rateLimiter.RatePerSecond * 4.0),
            Math.Max(100, rateLimiter.Burst * 4));
    }

    internal int Port { get; }

    internal int QueueCapacity { get; }

    internal int MaxDatagramBytes => Math.Min(65_000, _maxBodyBytes + 8_192);

    internal static bool TryCreate(out BridgeSecurityGate? gate, out string reason)
    {
        gate = null;
        var mode = (Environment.GetEnvironmentVariable("NATSUME_BRIDGE_EXPOSURE_MODE") ?? "DISABLED")
            .Trim()
            .ToUpperInvariant();
        if (mode == "DISABLED")
        {
            reason = "NATSUME_BRIDGE_EXPOSURE_MODE=DISABLED";
            return false;
        }

        if (mode != "LOOPBACK" && mode != "REMOTE_APPROVED")
        {
            reason = "invalid NATSUME_BRIDGE_EXPOSURE_MODE; bridge remains disabled";
            return false;
        }

        var mateEngineEnabled = (Environment.GetEnvironmentVariable("HOST_MATEENGINE_BRIDGE_ENABLED") ?? "false")
            .Trim()
            .ToLowerInvariant();
        if (mateEngineEnabled != "1"
            && mateEngineEnabled != "true"
            && mateEngineEnabled != "yes"
            && mateEngineEnabled != "on")
        {
            reason = "HOST_MATEENGINE_BRIDGE_ENABLED is not explicitly true";
            return false;
        }

        var keyId = (Environment.GetEnvironmentVariable("NATSUME_MATEENGINE_PLUGIN_HMAC_KEY_ID") ?? "").Trim();
        var secret = Encoding.UTF8.GetBytes(
            Environment.GetEnvironmentVariable("NATSUME_MATEENGINE_PLUGIN_HMAC_SECRET") ?? "");
        var principal = (Environment.GetEnvironmentVariable("NATSUME_MATEENGINE_PLUGIN_PRINCIPAL") ?? "host.mateengine.bridge").Trim();
        var auditPath = (Environment.GetEnvironmentVariable("NATSUME_MATEENGINE_PLUGIN_AUDIT_JSONL_PATH") ?? "").Trim();
        var approvalRef = (Environment.GetEnvironmentVariable("NATSUME_MATEENGINE_BRIDGE_REMOTE_APPROVAL_REF") ?? "").Trim();

        if (!IsSafeToken(keyId, 128) || secret.Length < 32 || !IsSafeToken(principal, 128))
        {
            reason = "bridge HMAC key id, secret, or service principal is invalid";
            return false;
        }

        if (string.IsNullOrWhiteSpace(auditPath))
        {
            reason = "NATSUME_MATEENGINE_PLUGIN_AUDIT_JSONL_PATH is required";
            return false;
        }

        if (mode == "REMOTE_APPROVED" && !IsSafeReference(approvalRef, 512))
        {
            reason = "REMOTE_APPROVED requires NATSUME_MATEENGINE_BRIDGE_REMOTE_APPROVAL_REF";
            return false;
        }

        if (!TryReadInt("MATEENGINE_PLUGIN_PORT", 32_146, 1, 65_535, out var port)
            || !TryReadInt("NATSUME_BRIDGE_RATE_BURST", 20, 1, 10_000, out var rateBurst)
            || !TryReadInt("NATSUME_BRIDGE_NONCE_TTL_SECONDS", 30, 1, 3_600, out var ttlSeconds)
            || !TryReadInt("NATSUME_BRIDGE_MAX_BODY_BYTES", 65_536, 1, 1_048_576, out var maxBodyBytes)
            || !TryReadInt("NATSUME_BRIDGE_PLUGIN_QUEUE_CAPACITY", 256, 1, 4_096, out var queueCapacity)
            || !TryReadDouble("NATSUME_BRIDGE_RATE_PER_SECOND", 10.0, 0.001, 10_000.0, out var ratePerSecond))
        {
            reason = "bridge numeric security configuration is invalid";
            return false;
        }

        var replayPath = (Environment.GetEnvironmentVariable("NATSUME_MATEENGINE_PLUGIN_REPLAY_PATH") ?? "").Trim();
        if (string.IsNullOrWhiteSpace(replayPath))
        {
            replayPath = auditPath + ".mateengine-plugin-replay.jsonl";
        }

        try
        {
            var fullAuditPath = Path.GetFullPath(auditPath);
            var fullReplayPath = Path.GetFullPath(replayPath);
            if (!StoragePathsAreDistinct(fullAuditPath, fullReplayPath))
            {
                reason = "bridge audit and replay path families must be distinct";
                return false;
            }
            var auditLog = new DurableAuditLog(fullAuditPath);
            var replayLedger = new DurableReplayLedger(fullReplayPath);
            var rateLimiter = new TokenBucket(ratePerSecond, rateBurst);
            var candidate = new BridgeSecurityGate(
                mode,
                port,
                queueCapacity,
                keyId,
                secret,
                principal,
                checked((long)ttlSeconds * 1_000L),
                Math.Min(maxBodyBytes, 56_000),
                replayLedger,
                auditLog,
                rateLimiter);
            if (!auditLog.Append(AuditRecord.Ready(mode)))
            {
                reason = "bridge audit log is unavailable";
                return false;
            }

            gate = candidate;
            reason = "authorized bridge security gate ready";
            return true;
        }
        catch (Exception ex) when (
            ex is IOException
            || ex is InvalidDataException
            || ex is UnauthorizedAccessException
            || ex is ArgumentException)
        {
            reason = $"bridge durable security storage unavailable: {ex.GetType().Name}";
            return false;
        }
    }

    internal bool TryAuthenticate(
        byte[] datagram,
        IPEndPoint source,
        out string payloadJson,
        out BridgeAuthorization authorization,
        out string reason)
    {
        payloadJson = "";
        authorization = BridgeAuthorization.Empty;
        var bodyDigest = Sha256Hex(datagram);
        if (!_preAuthLimiter.TryConsume())
        {
            reason = "preauth_rate_limited";
            return false;
        }
        if (!IPAddress.IsLoopback(source.Address))
        {
            reason = "non_loopback_source";
            AuditRejected(reason, source, "", bodyDigest);
            return false;
        }

        if (datagram.Length <= 0 || datagram.Length > MaxDatagramBytes)
        {
            reason = "datagram_size_invalid";
            AuditRejected(reason, source, "", bodyDigest);
            return false;
        }

        SignedBridgeEnvelope? envelope;
        try
        {
            envelope = JsonUtility.FromJson<SignedBridgeEnvelope>(Encoding.UTF8.GetString(datagram));
        }
        catch (Exception)
        {
            envelope = null;
        }

        if (envelope == null)
        {
            reason = "invalid_envelope_json";
            AuditRejected(reason, source, "", bodyDigest);
            return false;
        }

        var nonceDigest = Sha256Hex(Encoding.UTF8.GetBytes(envelope.nonce ?? ""));
        if (envelope.version != AuthVersion
            || envelope.key_id != _keyId
            || !string.Equals(envelope.transport, Transport, StringComparison.Ordinal)
            || !string.Equals(envelope.method, Method, StringComparison.Ordinal)
            || !string.Equals(envelope.path, PluginPath, StringComparison.Ordinal)
            || !string.Equals(envelope.principal, _principal, StringComparison.Ordinal)
            || !string.Equals(envelope.scope, PluginScope, StringComparison.Ordinal))
        {
            reason = "envelope_contract_mismatch";
            AuditRejected(reason, source, nonceDigest, bodyDigest);
            return false;
        }

        if (!IsSafeToken(envelope.nonce, 256))
        {
            reason = "invalid_nonce";
            AuditRejected(reason, source, nonceDigest, bodyDigest);
            return false;
        }

        var nowMilliseconds = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        if (envelope.timestamp_ms <= 0 || Math.Abs(nowMilliseconds - envelope.timestamp_ms) > _ttlMilliseconds)
        {
            reason = "expired_timestamp";
            AuditRejected(reason, source, nonceDigest, bodyDigest);
            return false;
        }

        payloadJson = envelope.payload_json ?? "";
        var payloadBytes = Encoding.UTF8.GetBytes(payloadJson);
        if (payloadBytes.Length <= 0 || payloadBytes.Length > _maxBodyBytes)
        {
            reason = "payload_size_invalid";
            AuditRejected(reason, source, nonceDigest, bodyDigest);
            return false;
        }

        var payloadDigest = Sha256Hex(payloadBytes);
        if (!FixedTimeEqualsHex(payloadDigest, (envelope.body_sha256 ?? "").ToLowerInvariant()))
        {
            reason = "body_digest_mismatch";
            AuditRejected(reason, source, nonceDigest, payloadDigest);
            return false;
        }

        var canonical = string.Join(
            "\n",
            AuthVersion,
            _keyId,
            Transport,
            Method,
            PluginPath,
            envelope.timestamp_ms.ToString(CultureInfo.InvariantCulture),
            envelope.nonce,
            _principal,
            PluginScope,
            payloadDigest);
        var expectedSignature = "sha256=" + HmacSha256Hex(_secret, Encoding.UTF8.GetBytes(canonical));
        if (!FixedTimeEqualsAscii(expectedSignature, (envelope.signature ?? "").ToLowerInvariant()))
        {
            reason = "invalid_signature";
            AuditRejected(reason, source, nonceDigest, payloadDigest);
            return false;
        }

        var replayKey = Sha256Hex(Encoding.UTF8.GetBytes(_principal + "\n" + envelope.nonce));
        if (_replayLedger.IsClaimed(replayKey, nowMilliseconds))
        {
            reason = "replay_detected";
            AuditRejected(reason, source, nonceDigest, payloadDigest);
            return false;
        }

        if (!_rateLimiter.TryConsume())
        {
            reason = "rate_limited";
            AuditRejected(reason, source, nonceDigest, payloadDigest);
            return false;
        }

        if (!_replayLedger.TryClaim(
            replayKey,
            envelope.timestamp_ms + _ttlMilliseconds,
            nowMilliseconds))
        {
            reason = "replay_detected";
            AuditRejected(reason, source, nonceDigest, payloadDigest);
            return false;
        }

        authorization = new BridgeAuthorization(
            _principal,
            nonceDigest,
            payloadDigest,
            source.Address.ToString(),
            envelope.timestamp_ms);
        reason = "authorized";
        return true;
    }

    internal bool CommitAccepted(BridgeAuthorization authorization, string commandType)
    {
        return _auditLog.Append(AuditRecord.Decision(
            "plugin_command_accepted",
            "authorized",
            _mode,
            authorization,
            commandType,
            true));
    }

    internal void AuditRejected(BridgeAuthorization authorization, string reason, string commandType)
    {
        _auditLog.Append(AuditRecord.Decision(
            "plugin_command_rejected",
            reason,
            _mode,
            authorization,
            commandType,
            false));
    }

    internal void AuditCompleted(BridgeAuthorization authorization, string commandType, bool success)
    {
        _auditLog.Append(AuditRecord.Decision(
            "plugin_command_completed",
            success ? "completed" : "handler_failed",
            _mode,
            authorization,
            commandType,
            success));
    }

    private void AuditRejected(string reason, IPEndPoint source, string nonceDigest, string bodyDigest)
    {
        var authorization = new BridgeAuthorization("", nonceDigest, bodyDigest, source.Address.ToString(), 0L);
        AuditRejected(authorization, reason, "");
    }

    private static HashSet<string> ParseCsv(string? value)
    {
        return new HashSet<string>(
            (value ?? "")
                .Split(new[] { ',', ' ' }, StringSplitOptions.RemoveEmptyEntries)
                .Select(item => item.Trim())
                .Where(item => item.Length > 0),
            StringComparer.Ordinal);
    }

    private static bool IsSafeToken(string? value, int maximumLength)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length > maximumLength)
        {
            return false;
        }

        foreach (var character in value)
        {
            if (char.IsWhiteSpace(character) || char.IsControl(character))
            {
                return false;
            }
        }

        return true;
    }

    private static bool IsSafeReference(string? value, int maximumLength)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length > maximumLength)
        {
            return false;
        }

        return value.IndexOf('\r') < 0 && value.IndexOf('\n') < 0;
    }

    private static bool StoragePathsAreDistinct(string auditPath, string replayPath)
    {
        var auditFamily = Enumerable.Range(0, 9)
            .Select(index => index == 0
                ? auditPath
                : auditPath + "." + index.ToString(CultureInfo.InvariantCulture));
        if (auditFamily.Any(path => string.Equals(
            path,
            replayPath,
            StringComparison.OrdinalIgnoreCase)))
        {
            return false;
        }
        foreach (var path in auditFamily.Append(replayPath))
        {
            if (File.Exists(path)
                && (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            {
                return false;
            }
        }
        return true;
    }

    private static bool TryReadInt(string name, int defaultValue, int minimum, int maximum, out int value)
    {
        var raw = Environment.GetEnvironmentVariable(name);
        if (string.IsNullOrWhiteSpace(raw))
        {
            value = defaultValue;
            return true;
        }

        return int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out value)
            && value >= minimum
            && value <= maximum;
    }

    private static bool TryReadDouble(string name, double defaultValue, double minimum, double maximum, out double value)
    {
        var raw = Environment.GetEnvironmentVariable(name);
        if (string.IsNullOrWhiteSpace(raw))
        {
            value = defaultValue;
            return true;
        }

        return double.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out value)
            && !double.IsNaN(value)
            && !double.IsInfinity(value)
            && value >= minimum
            && value <= maximum;
    }

    private static string Sha256Hex(byte[] value)
    {
        using var sha = SHA256.Create();
        return ToLowerHex(sha.ComputeHash(value));
    }

    private static string HmacSha256Hex(byte[] key, byte[] value)
    {
        using var hmac = new HMACSHA256(key);
        return ToLowerHex(hmac.ComputeHash(value));
    }

    private static string ToLowerHex(byte[] value)
    {
        var builder = new StringBuilder(value.Length * 2);
        foreach (var item in value)
        {
            builder.Append(item.ToString("x2", CultureInfo.InvariantCulture));
        }

        return builder.ToString();
    }

    private static bool FixedTimeEqualsHex(string expected, string supplied)
    {
        return expected.Length == 64 && supplied.Length == 64 && FixedTimeEqualsAscii(expected, supplied);
    }

    private static bool FixedTimeEqualsAscii(string expected, string supplied)
    {
        var expectedBytes = Encoding.ASCII.GetBytes(expected);
        var suppliedBytes = Encoding.ASCII.GetBytes(supplied);
        var difference = expectedBytes.Length ^ suppliedBytes.Length;
        var maximum = Math.Max(expectedBytes.Length, suppliedBytes.Length);
        for (var index = 0; index < maximum; index++)
        {
            var left = index < expectedBytes.Length ? expectedBytes[index] : (byte)0;
            var right = index < suppliedBytes.Length ? suppliedBytes[index] : (byte)0;
            difference |= left ^ right;
        }

        return difference == 0;
    }

    [Serializable]
    private sealed class SignedBridgeEnvelope
    {
        public string? version;
        public string? key_id;
        public string? transport;
        public string? method;
        public string? path;
        public long timestamp_ms;
        public string? nonce;
        public string? principal;
        public string? scope;
        public string? payload_json;
        public string? body_sha256;
        public string? signature;
    }
}

internal readonly struct BridgeAuthorization
{
    internal static readonly BridgeAuthorization Empty = new("", "", "", "", 0L);

    internal BridgeAuthorization(
        string principal,
        string nonceDigest,
        string bodyDigest,
        string source,
        long timestampMilliseconds)
    {
        Principal = principal;
        NonceDigest = nonceDigest;
        BodyDigest = bodyDigest;
        Source = source;
        TimestampMilliseconds = timestampMilliseconds;
    }

    internal string Principal { get; }
    internal string NonceDigest { get; }
    internal string BodyDigest { get; }
    internal string Source { get; }
    internal long TimestampMilliseconds { get; }
}

internal sealed class TokenBucket
{
    private readonly object _sync = new();
    private readonly double _ratePerSecond;
    private readonly double _burst;
    private double _tokens;
    private double _updatedAtSeconds;

    internal TokenBucket(double ratePerSecond, int burst)
    {
        _ratePerSecond = ratePerSecond;
        _burst = burst;
        _tokens = burst;
        _updatedAtSeconds = MonotonicSeconds();
    }

    internal double RatePerSecond => _ratePerSecond;

    internal int Burst => (int)_burst;

    internal bool TryConsume()
    {
        lock (_sync)
        {
            var now = MonotonicSeconds();
            var elapsed = Math.Max(0.0, now - _updatedAtSeconds);
            _tokens = Math.Min(_burst, _tokens + elapsed * _ratePerSecond);
            _updatedAtSeconds = now;
            if (_tokens < 1.0)
            {
                return false;
            }

            _tokens -= 1.0;
            return true;
        }
    }

    private static double MonotonicSeconds()
    {
        return Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency;
    }
}

internal sealed class DurableReplayLedger
{
    private const int MaximumLiveClaims = 100_000;
    private const int CompactClaimInterval = 4_096;
    private const int CleanupClaimInterval = 4_096;
    private const long CleanupIntervalMilliseconds = 60_000L;
    private readonly object _sync = new();
    private readonly string _path;
    private readonly Dictionary<string, long> _claims = new(StringComparer.Ordinal);
    private int _claimsSinceCompact;
    private int _claimsSinceCleanup;
    private long _lastCleanupMilliseconds;
    private bool _poisoned;

    internal DurableReplayLedger(string path)
    {
        _path = path;
        EnsureParentDirectory(path);
        var nowMilliseconds = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        LoadAndCompact(nowMilliseconds);
        _lastCleanupMilliseconds = nowMilliseconds;
    }

    internal bool TryClaim(string nonceDigest, long expiresAtMilliseconds, long nowMilliseconds)
    {
        lock (_sync)
        {
            if (_poisoned)
            {
                return false;
            }
            _claimsSinceCleanup++;
            if (_claimsSinceCleanup >= CleanupClaimInterval
                || nowMilliseconds - _lastCleanupMilliseconds >= CleanupIntervalMilliseconds)
            {
                RemoveExpired(nowMilliseconds);
            }

            if (_claims.ContainsKey(nonceDigest))
            {
                return false;
            }
            if (_claims.Count >= MaximumLiveClaims)
            {
                return false;
            }

            var row = new ReplayRow { nonce_digest = nonceDigest, expires_at_ms = expiresAtMilliseconds };
            var encoded = JsonUtility.ToJson(row) + "\n";
            if (!DurableFile.Append(_path, encoded))
            {
                _poisoned = true;
                return false;
            }

            _claims[nonceDigest] = expiresAtMilliseconds;
            _claimsSinceCompact++;
            try
            {
                if (_claimsSinceCompact >= CompactClaimInterval)
                {
                    if (!Compact())
                    {
                        _poisoned = true;
                        return false;
                    }
                }
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            {
                _poisoned = true;
                return false;
            }
            return true;
        }
    }

    internal bool IsClaimed(string nonceDigest, long nowMilliseconds)
    {
        lock (_sync)
        {
            if (_poisoned)
            {
                return true;
            }
            if (!_claims.TryGetValue(nonceDigest, out var expiry))
            {
                return false;
            }
            if (expiry < nowMilliseconds)
            {
                _claims.Remove(nonceDigest);
                return false;
            }
            return true;
        }
    }

    private void LoadAndCompact(long nowMilliseconds)
    {
        if (File.Exists(_path))
        {
            foreach (var line in File.ReadLines(_path, Encoding.UTF8))
            {
                if (string.IsNullOrWhiteSpace(line))
                {
                    continue;
                }
                try
                {
                    var row = JsonUtility.FromJson<ReplayRow>(line);
                    if (row == null
                        || row.nonce_digest == null
                        || row.nonce_digest.Length != 64
                        || row.nonce_digest.Any(character =>
                            !((character >= '0' && character <= '9') || (character >= 'a' && character <= 'f')))
                        || row.expires_at_ms <= 0)
                    {
                        throw new InvalidDataException("Replay ledger contains an invalid row.");
                    }
                    if (row.expires_at_ms >= nowMilliseconds)
                    {
                        if (_claims.Count >= MaximumLiveClaims
                            && !_claims.ContainsKey(row.nonce_digest))
                        {
                            throw new InvalidDataException("Replay ledger exceeds its live claim cap.");
                        }
                        _claims[row.nonce_digest] = Math.Max(
                            _claims.TryGetValue(row.nonce_digest, out var existing) ? existing : 0L,
                            row.expires_at_ms);
                    }
                }
                catch (Exception ex) when (!(ex is InvalidDataException))
                {
                    throw new InvalidDataException("Replay ledger contains malformed JSON.", ex);
                }
            }
        }

        if (!Compact())
        {
            throw new IOException("Unable to initialize replay ledger.");
        }
    }

    private bool Compact()
    {
        var compact = new StringBuilder();
        foreach (var row in _claims.OrderBy(item => item.Key, StringComparer.Ordinal))
        {
            compact.Append(JsonUtility.ToJson(new ReplayRow
            {
                nonce_digest = row.Key,
                expires_at_ms = row.Value,
            }));
            compact.Append('\n');
        }

        if (!DurableFile.Replace(_path, compact.ToString()))
        {
            return false;
        }
        _claimsSinceCompact = 0;
        return true;
    }

    private void RemoveExpired(long nowMilliseconds)
    {
        foreach (var stale in _claims
            .Where(item => item.Value < nowMilliseconds)
            .Select(item => item.Key)
            .ToArray())
        {
            _claims.Remove(stale);
        }
        _claimsSinceCleanup = 0;
        _lastCleanupMilliseconds = nowMilliseconds;
    }

    [Serializable]
    private sealed class ReplayRow
    {
        public string? nonce_digest;
        public long expires_at_ms;
    }

    private static void EnsureParentDirectory(string path)
    {
        var parent = Path.GetDirectoryName(path);
        if (!string.IsNullOrWhiteSpace(parent))
        {
            Directory.CreateDirectory(parent);
        }
    }
}

internal sealed class DurableAuditLog
{
    private readonly object _sync = new();
    private readonly string _path;
    private bool _poisoned;

    internal DurableAuditLog(string path)
    {
        _path = path;
        var parent = Path.GetDirectoryName(path);
        if (!string.IsNullOrWhiteSpace(parent))
        {
            Directory.CreateDirectory(parent);
        }
        ValidateExisting(path);
        for (var index = 1; index <= 8; index++)
        {
            ValidateExisting(path + "." + index.ToString(CultureInfo.InvariantCulture));
        }
    }

    internal bool Append(AuditRecord row)
    {
        lock (_sync)
        {
            if (_poisoned)
            {
                return false;
            }
            var encoded = JsonUtility.ToJson(row) + "\n";
            var appended = DurableFile.RotateIfNeeded(
                    _path,
                    Encoding.UTF8.GetByteCount(encoded),
                    64L * 1024L * 1024L,
                    8)
                && DurableFile.Append(_path, encoded);
            if (!appended)
            {
                _poisoned = true;
            }
            return appended;
        }
    }

    private static void ValidateExisting(string path)
    {
        if (!File.Exists(path))
        {
            return;
        }

        using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
        {
            if (stream.Length > 0)
            {
                stream.Seek(-1, SeekOrigin.End);
                if (stream.ReadByte() != (byte)'\n')
                {
                    throw new InvalidDataException("Audit log contains a torn terminal row.");
                }
            }
        }

        foreach (var line in File.ReadLines(path, Encoding.UTF8))
        {
            var trimmed = line.Trim();
            if (trimmed.Length < 2 || trimmed[0] != '{' || trimmed[trimmed.Length - 1] != '}')
            {
                throw new InvalidDataException("Audit log contains malformed JSON.");
            }
            AuditRecord? row;
            try
            {
                row = JsonUtility.FromJson<AuditRecord>(trimmed);
            }
            catch (Exception ex)
            {
                throw new InvalidDataException("Audit log contains malformed JSON.", ex);
            }
            if (row == null || string.IsNullOrWhiteSpace(row.event_name) || row.ts_ms <= 0)
            {
                throw new InvalidDataException("Audit log contains an invalid row.");
            }
        }
    }
}

internal static class DurableFile
{
    internal static bool RotateIfNeeded(
        string path,
        int incomingBytes,
        long maximumBytes,
        int archiveCount)
    {
        try
        {
            var currentBytes = File.Exists(path) ? new FileInfo(path).Length : 0L;
            if (currentBytes + incomingBytes <= maximumBytes)
            {
                return true;
            }
            var oldest = path + "." + archiveCount.ToString(CultureInfo.InvariantCulture);
            if (File.Exists(oldest)) File.Delete(oldest);
            for (var index = archiveCount - 1; index >= 1; index--)
            {
                var source = path + "." + index.ToString(CultureInfo.InvariantCulture);
                if (!File.Exists(source)) continue;
                var destination = path + "." + (index + 1).ToString(CultureInfo.InvariantCulture);
                if (File.Exists(destination)) File.Delete(destination);
                File.Move(source, destination);
            }
            if (File.Exists(path))
            {
                var first = path + ".1";
                if (File.Exists(first)) File.Delete(first);
                File.Move(path, first);
            }
            return true;
        }
        catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
        {
            return false;
        }
    }

    internal static bool Append(string path, string value)
    {
        try
        {
            var bytes = Encoding.UTF8.GetBytes(value);
            using var stream = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite);
            stream.Write(bytes, 0, bytes.Length);
            stream.Flush(true);
            return true;
        }
        catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
        {
            return false;
        }
    }

    internal static bool Replace(string path, string value)
    {
        var temporaryPath = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        var backupPath = path + "." + Guid.NewGuid().ToString("N") + ".bak";
        try
        {
            var bytes = Encoding.UTF8.GetBytes(value);
            using (var stream = new FileStream(
                temporaryPath,
                FileMode.CreateNew,
                FileAccess.Write,
                FileShare.None))
            {
                stream.Write(bytes, 0, bytes.Length);
                stream.Flush(true);
            }
            if (File.Exists(path))
            {
                File.Replace(temporaryPath, path, backupPath);
                try { File.Delete(backupPath); } catch { }
            }
            else
            {
                File.Move(temporaryPath, path);
            }
            return true;
        }
        catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
        {
            try { File.Delete(temporaryPath); } catch { }
            return false;
        }
    }
}

[Serializable]
internal sealed class AuditRecord
{
    public string? event_name;
    public long ts_ms;
    public string? exposure_mode;
    public string? decision_reason;
    public string? transport;
    public string? path;
    public string? principal;
    public string? nonce_digest;
    public string? body_sha256;
    public string? source;
    public string? command_type;
    public bool success;

    internal static AuditRecord Ready(string mode)
    {
        return new AuditRecord
        {
            event_name = "mateengine_plugin_security_ready",
            ts_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            exposure_mode = mode,
            decision_reason = "ready",
            transport = "udp",
            path = BridgeSecurityGate.PluginPath,
            success = true,
        };
    }

    internal static AuditRecord Decision(
        string eventName,
        string reason,
        string mode,
        BridgeAuthorization authorization,
        string commandType,
        bool success)
    {
        return new AuditRecord
        {
            event_name = eventName,
            ts_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            exposure_mode = mode,
            decision_reason = reason,
            transport = "udp",
            path = BridgeSecurityGate.PluginPath,
            principal = authorization.Principal,
            nonce_digest = authorization.NonceDigest,
            body_sha256 = authorization.BodyDigest,
            source = authorization.Source,
            command_type = commandType,
            success = success,
        };
    }
}
