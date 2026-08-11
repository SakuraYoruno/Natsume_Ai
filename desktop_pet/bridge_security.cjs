"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const EXPOSURE_MODES = Object.freeze({
  DISABLED: "DISABLED",
  LOOPBACK: "LOOPBACK",
  REMOTE_APPROVED: "REMOTE_APPROVED",
});

const BRIDGE_VERSION = "natsume.bridge-auth.v1";
const BRIDGE_TRANSPORT = "http";
const ACTION_SCOPE = "bridge:desktop-pet:action";
const READ_SCOPE = "bridge:desktop-pet:read";
const DEFAULT_LOOPBACK_PRINCIPAL = "host.desktop-pet.bridge";
const DEFAULT_KEY_ID = "desktop-pet.bridge.hmac.v1";
const DEFAULT_MAX_BODY_BYTES = 64 * 1024;
const MAX_BODY_BYTES_CEILING = 1024 * 1024;
const DEFAULT_RATE_PER_SECOND = 10;
const DEFAULT_RATE_BURST = 20;
const DEFAULT_AUTH_TTL_MS = 30_000;

const HEADER_NAMES = Object.freeze({
  version: "x-natsume-bridge-version",
  keyId: "x-natsume-bridge-key-id",
  timestamp: "x-natsume-bridge-timestamp",
  nonce: "x-natsume-bridge-nonce",
  principal: "x-natsume-bridge-principal",
  scope: "x-natsume-bridge-scope",
  signature: "x-natsume-bridge-signature",
});

const SAFE_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$/;
const SAFE_NONCE_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/;
const SAFE_APPROVAL_RE = /^[A-Za-z0-9][A-Za-z0-9._:@/-]{7,255}$/;
const HMAC_SIGNATURE_RE = /^sha256=[0-9a-f]{64}$/;

class BridgeSecurityError extends Error {
  constructor(reasonCode, message, statusCode = 401) {
    super(String(message || reasonCode));
    this.name = "BridgeSecurityError";
    this.reasonCode = String(reasonCode || "BRIDGE_SECURITY_REJECTED");
    this.statusCode = Number(statusCode) || 401;
  }
}

function utf8Length(value) {
  return Buffer.byteLength(String(value || ""), "utf8");
}

function parseCsv(value) {
  return Object.freeze(
    Array.from(
      new Set(
        String(value || "")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      ),
    ),
  );
}

function parsePositiveNumber(value, fallback) {
  const raw = value === undefined || value === null || String(value).trim() === ""
    ? fallback
    : Number(value);
  return Number.isFinite(raw) && raw > 0 ? raw : null;
}

function parsePositiveInteger(value, fallback) {
  const parsed = parsePositiveNumber(value, fallback);
  return parsed !== null && Number.isSafeInteger(parsed) ? parsed : null;
}

function hasExplicitValue(source, name) {
  return Object.prototype.hasOwnProperty.call(source, name)
    && String(source[name] ?? "").trim() !== "";
}

function isLoopbackHost(value) {
  const host = String(value || "").trim().toLowerCase();
  return host === "127.0.0.1" || host === "::1";
}

function isLoopbackPeer(value) {
  const peer = String(value || "").trim().toLowerCase().split("%", 1)[0];
  return peer === "127.0.0.1"
    || peer === "::1"
    || peer === "::ffff:127.0.0.1";
}

function storagePathsAreDistinct({ auditPath, replayPath }) {
  const candidates = [
    replayPath,
    auditPath,
    ...Array.from({ length: 8 }, (_item, index) => `${auditPath}.${index + 1}`),
  ].map((item) => path.resolve(String(item || "")));
  const normalize = (item) => process.platform === "win32" ? item.toLowerCase() : item;
  if (new Set(candidates.map(normalize)).size !== candidates.length) return false;

  const canonicalizeMissingLeaf = (candidate) => {
    let ancestor = candidate;
    const missingSegments = [];
    while (!fs.existsSync(ancestor)) {
      const parent = path.dirname(ancestor);
      if (parent === ancestor) return normalize(candidate);
      missingSegments.unshift(path.basename(ancestor));
      ancestor = parent;
    }
    const realAncestor = fs.realpathSync.native(ancestor);
    return normalize(path.join(realAncestor, ...missingSegments));
  };

  try {
    const canonicalCandidates = candidates.map(canonicalizeMissingLeaf);
    if (new Set(canonicalCandidates).size !== canonicalCandidates.length) return false;
  } catch {
    return false;
  }

  const realPaths = new Set();
  const fileIdentities = new Set();
  for (const candidate of candidates) {
    try {
      if (!fs.existsSync(candidate)) continue;
      const metadata = fs.lstatSync(candidate);
      if (metadata.isSymbolicLink()) return false;
      const realPath = normalize(fs.realpathSync.native(candidate));
      if (realPaths.has(realPath)) return false;
      realPaths.add(realPath);
      const identity = `${metadata.dev}:${metadata.ino}`;
      if (metadata.ino && fileIdentities.has(identity)) return false;
      if (metadata.ino) fileIdentities.add(identity);
    } catch {
      return false;
    }
  }
  return true;
}

function resolveBridgeConfig(env = process.env) {
  const configuredMode = String(env.NATSUME_BRIDGE_EXPOSURE_MODE || "DISABLED")
    .trim()
    .toUpperCase();
  const errors = [];
  const knownModes = new Set(Object.values(EXPOSURE_MODES));
  const mode = knownModes.has(configuredMode) ? configuredMode : EXPOSURE_MODES.DISABLED;
  if (!knownModes.has(configuredMode)) {
    errors.push("NATSUME_BRIDGE_EXPOSURE_MODE_INVALID");
  }

  const disabledConfig = {
    mode,
    configuredMode,
    listenerEnabled: false,
    valid: errors.length === 0,
    errors: Object.freeze([...errors]),
    host: "127.0.0.1",
    port: 18788,
    approvalRef: "",
    keyId: "",
    allowedPrincipals: Object.freeze([]),
    allowedScopes: Object.freeze([]),
    ratePerSecond: DEFAULT_RATE_PER_SECOND,
    rateBurst: DEFAULT_RATE_BURST,
    authTtlMs: DEFAULT_AUTH_TTL_MS,
    maxBodyBytes: DEFAULT_MAX_BODY_BYTES,
    auditPath: "",
    replayPath: "",
    auditRequired: false,
  };
  Object.defineProperty(disabledConfig, "secretBytes", {
    value: Buffer.alloc(0),
    enumerable: false,
    writable: false,
  });
  if (mode === EXPOSURE_MODES.DISABLED) {
    return Object.freeze(disabledConfig);
  }

  const bridgeEnabled = /^(1|true|yes|on)$/i.test(
    String(env.NATSUME_DESKTOP_PET_BRIDGE_ENABLED || "false").trim(),
  );
  if (!bridgeEnabled) {
    errors.push("NATSUME_DESKTOP_PET_BRIDGE_ENABLED_REQUIRED");
  }

  const host = String(env.NATSUME_DESKTOP_PET_HOST ?? "127.0.0.1").trim();
  const port = parsePositiveInteger(env.NATSUME_DESKTOP_PET_PORT, 18788);
  const keyId = String(
    env.NATSUME_DESKTOP_PET_HMAC_KEY_ID
      ?? (mode === EXPOSURE_MODES.LOOPBACK ? DEFAULT_KEY_ID : ""),
  ).trim();
  const secretText = String(env.NATSUME_DESKTOP_PET_HMAC_SECRET || "");
  const secretBytes = Buffer.from(secretText, "utf8");
  const approvalRef = String(env.NATSUME_DESKTOP_PET_REMOTE_APPROVAL_REF || "").trim();
  const principalsConfigured = String(env.NATSUME_DESKTOP_PET_ALLOWED_PRINCIPAL || "").trim();
  const scopesConfigured = String(env.NATSUME_DESKTOP_PET_ALLOWED_SCOPES || "").trim();
  const allowedPrincipals = parseCsv(
    principalsConfigured || (mode === EXPOSURE_MODES.LOOPBACK ? DEFAULT_LOOPBACK_PRINCIPAL : ""),
  );
  const allowedScopes = parseCsv(
    scopesConfigured || (mode === EXPOSURE_MODES.LOOPBACK ? `${ACTION_SCOPE},${READ_SCOPE}` : ""),
  );
  const ratePerSecond = parsePositiveNumber(
    env.NATSUME_BRIDGE_RATE_PER_SECOND,
    DEFAULT_RATE_PER_SECOND,
  );
  const rateBurst = parsePositiveInteger(
    env.NATSUME_BRIDGE_RATE_BURST,
    DEFAULT_RATE_BURST,
  );
  const nonceTtlSeconds = parsePositiveInteger(
    env.NATSUME_BRIDGE_NONCE_TTL_SECONDS,
    DEFAULT_AUTH_TTL_MS / 1000,
  );
  const authTtlMs = nonceTtlSeconds === null ? null : nonceTtlSeconds * 1000;
  const maxBodyBytes = parsePositiveInteger(
    env.NATSUME_BRIDGE_MAX_BODY_BYTES,
    DEFAULT_MAX_BODY_BYTES,
  );
  const explicitAuditPath = String(env.NATSUME_DESKTOP_PET_AUDIT_JSONL_PATH || "").trim();
  const auditPath = explicitAuditPath || path.resolve(
    process.cwd(),
    "runtime",
    "bridge",
    "desktop_pet_security.jsonl",
  );
  const replayPath = String(env.NATSUME_DESKTOP_PET_REPLAY_LEDGER_PATH || "").trim()
    || `${auditPath}.nonces.jsonl`;

  if (!storagePathsAreDistinct({ auditPath, replayPath })) {
    errors.push("NATSUME_DESKTOP_PET_STORAGE_PATH_COLLISION");
  }

  if (!host) errors.push("NATSUME_DESKTOP_PET_HOST_REQUIRED");
  if (port === null || port > 65_535) errors.push("NATSUME_DESKTOP_PET_PORT_INVALID");
  if (!SAFE_ID_RE.test(keyId)) errors.push("NATSUME_DESKTOP_PET_HMAC_KEY_ID_INVALID");
  if (secretBytes.length < 32) errors.push("NATSUME_DESKTOP_PET_HMAC_SECRET_TOO_SHORT");
  if (
    allowedPrincipals.length !== 1
    || allowedPrincipals.some((item) => !SAFE_ID_RE.test(item))
  ) {
    errors.push("NATSUME_BRIDGE_PRINCIPAL_ALLOWLIST_INVALID");
  }
  if (!allowedScopes.length || allowedScopes.some((item) => !SAFE_ID_RE.test(item))) {
    errors.push("NATSUME_BRIDGE_SCOPE_ALLOWLIST_INVALID");
  }
  if (!allowedScopes.includes(ACTION_SCOPE)) {
    errors.push("NATSUME_BRIDGE_DESKTOP_PET_ACTION_SCOPE_REQUIRED");
  }
  if (ratePerSecond === null) errors.push("NATSUME_BRIDGE_RATE_PER_SECOND_INVALID");
  if (rateBurst === null) errors.push("NATSUME_BRIDGE_RATE_BURST_INVALID");
  if (authTtlMs === null || authTtlMs > 300_000) {
    errors.push("NATSUME_BRIDGE_NONCE_TTL_SECONDS_INVALID");
  }
  if (maxBodyBytes === null || maxBodyBytes > MAX_BODY_BYTES_CEILING) {
    errors.push("NATSUME_BRIDGE_MAX_BODY_BYTES_INVALID");
  }

  if (mode === EXPOSURE_MODES.LOOPBACK && !isLoopbackHost(host)) {
    errors.push("NATSUME_DESKTOP_PET_LOOPBACK_HOST_REQUIRED");
  }

  if (mode === EXPOSURE_MODES.REMOTE_APPROVED) {
    if (!hasExplicitValue(env, "NATSUME_DESKTOP_PET_HOST") || !host) {
      errors.push("NATSUME_DESKTOP_PET_REMOTE_HOST_MUST_BE_EXPLICIT");
    }
    if (!SAFE_APPROVAL_RE.test(approvalRef)) {
      errors.push("NATSUME_DESKTOP_PET_REMOTE_APPROVAL_REF_INVALID");
    }
    if (!hasExplicitValue(env, "NATSUME_DESKTOP_PET_HMAC_KEY_ID")) {
      errors.push("NATSUME_BRIDGE_REMOTE_KEY_ID_MUST_BE_EXPLICIT");
    }
    if (!principalsConfigured) {
      errors.push("NATSUME_DESKTOP_PET_REMOTE_PRINCIPAL_MUST_BE_EXPLICIT");
    }
    if (!scopesConfigured || !allowedScopes.includes(READ_SCOPE)) {
      errors.push("NATSUME_BRIDGE_REMOTE_SCOPES_MUST_INCLUDE_READ");
    }
    if (!hasExplicitValue(env, "NATSUME_BRIDGE_RATE_PER_SECOND")) {
      errors.push("NATSUME_BRIDGE_REMOTE_RATE_MUST_BE_EXPLICIT");
    }
    if (!hasExplicitValue(env, "NATSUME_BRIDGE_RATE_BURST")) {
      errors.push("NATSUME_BRIDGE_REMOTE_BURST_MUST_BE_EXPLICIT");
    }
    if (!hasExplicitValue(env, "NATSUME_BRIDGE_NONCE_TTL_SECONDS")) {
      errors.push("NATSUME_BRIDGE_REMOTE_TTL_MUST_BE_EXPLICIT");
    }
    if (!explicitAuditPath) {
      errors.push("NATSUME_DESKTOP_PET_AUDIT_JSONL_PATH_REQUIRED");
    }
  }

  const config = {
    mode,
    configuredMode,
    listenerEnabled: errors.length === 0,
    valid: errors.length === 0,
    errors: Object.freeze([...new Set(errors)]),
    host,
    port: port || 18788,
    approvalRef,
    keyId,
    allowedPrincipals,
    allowedScopes,
    ratePerSecond: ratePerSecond || DEFAULT_RATE_PER_SECOND,
    rateBurst: rateBurst || DEFAULT_RATE_BURST,
    authTtlMs: authTtlMs || DEFAULT_AUTH_TTL_MS,
    maxBodyBytes: maxBodyBytes || DEFAULT_MAX_BODY_BYTES,
    auditPath,
    replayPath,
    auditRequired: true,
  };
  Object.defineProperty(config, "secretBytes", {
    value: Buffer.from(secretBytes),
    enumerable: false,
    writable: false,
  });
  return Object.freeze(config);
}

function publicBridgeConfig(config) {
  return {
    mode: config.mode,
    listenerEnabled: Boolean(config.listenerEnabled),
    valid: Boolean(config.valid),
    errors: [...(config.errors || [])],
    host: config.host,
    port: config.port,
    allowedPrincipalCount: (config.allowedPrincipals || []).length,
    allowedScopeCount: (config.allowedScopes || []).length,
    keyId: config.keyId,
    ratePerSecond: config.ratePerSecond,
    rateBurst: config.rateBurst,
    authTtlMs: config.authTtlMs,
    maxBodyBytes: config.maxBodyBytes,
    auditRequired: Boolean(config.auditRequired),
    auditConfigured: Boolean(config.auditPath),
    replayConfigured: Boolean(config.replayPath),
    secretConfigured: Buffer.isBuffer(config.secretBytes) && config.secretBytes.length >= 32,
  };
}

function bodySha256(rawBody) {
  return crypto.createHash("sha256").update(Buffer.from(rawBody || Buffer.alloc(0))).digest("hex");
}

function canonicalBridgeRequest({
  version = BRIDGE_VERSION,
  keyId,
  transport = BRIDGE_TRANSPORT,
  method,
  path: protocolPath,
  requestPath,
  timestampMs,
  timestamp,
  nonce,
  principal,
  scope,
  rawBody = Buffer.alloc(0),
  bodyDigest,
}) {
  const normalizedMethod = String(method || "").trim().toUpperCase();
  const normalizedPath = String(protocolPath ?? requestPath ?? "").trim();
  const normalizedTimestamp = String(timestampMs ?? timestamp ?? "").trim();
  const normalizedDigest = String(bodyDigest || bodySha256(rawBody)).trim().toLowerCase();
  return [
    String(version || "").trim(),
    String(keyId || "").trim(),
    String(transport || "").trim().toLowerCase(),
    normalizedMethod,
    normalizedPath,
    normalizedTimestamp,
    String(nonce || "").trim(),
    String(principal || "").trim(),
    String(scope || "").trim(),
    normalizedDigest,
  ].join("\n");
}

function buildBridgeSignature({ secret, ...request }) {
  const key = Buffer.isBuffer(secret) ? Buffer.from(secret) : Buffer.from(String(secret || ""), "utf8");
  if (key.length < 32) {
    throw new BridgeSecurityError(
      "BRIDGE_SECRET_INVALID",
      "bridge HMAC secret must be at least 32 bytes",
      500,
    );
  }
  const digest = crypto
    .createHmac("sha256", key)
    .update(canonicalBridgeRequest(request), "utf8")
    .digest("hex");
  return `sha256=${digest}`;
}

function buildSignedHeaders({
  secret,
  keyId = DEFAULT_KEY_ID,
  method,
  requestPath,
  timestampMs,
  timestamp,
  nonce,
  principal,
  scope,
  rawBody = Buffer.alloc(0),
}) {
  const signedTimestamp = timestampMs ?? timestamp;
  const signature = buildBridgeSignature({
    secret,
    keyId,
    method,
    requestPath,
    timestampMs: signedTimestamp,
    nonce,
    principal,
    scope,
    rawBody,
  });
  return {
    "X-Natsume-Bridge-Version": BRIDGE_VERSION,
    "X-Natsume-Bridge-Key-Id": String(keyId),
    "X-Natsume-Bridge-Timestamp": String(signedTimestamp),
    "X-Natsume-Bridge-Nonce": String(nonce),
    "X-Natsume-Bridge-Principal": String(principal),
    "X-Natsume-Bridge-Scope": String(scope),
    "X-Natsume-Bridge-Signature": signature,
  };
}

function securityHeader(headers, rawHeaders, name) {
  const target = String(name).toLowerCase();
  const matches = [];
  if (Array.isArray(rawHeaders) && rawHeaders.length) {
    for (let index = 0; index + 1 < rawHeaders.length; index += 2) {
      if (String(rawHeaders[index] || "").trim().toLowerCase() === target) {
        matches.push(String(rawHeaders[index + 1] || "").trim());
      }
    }
  } else {
    const value = headers ? headers[target] : undefined;
    if (Array.isArray(value)) matches.push(...value.map((item) => String(item || "").trim()));
    else if (value !== undefined) matches.push(String(value || "").trim());
  }
  if (matches.length !== 1 || !matches[0]) {
    throw new BridgeSecurityError(
      matches.length > 1 ? "BRIDGE_AUTH_HEADER_AMBIGUOUS" : "BRIDGE_AUTH_HEADER_MISSING",
      "required bridge security header is missing or ambiguous",
      401,
    );
  }
  return matches[0];
}

class NonceReplayCache {
  constructor({ ttlMs, maxEntries, clock = Date.now }) {
    this.ttlMs = Number(ttlMs);
    this.maxEntries = Number(maxEntries);
    this.clock = clock;
    this.rows = new Map();
  }

  consume(key, nowMs = this.clock(), expiresAtMs = Number(nowMs) + this.ttlMs) {
    this.purge(nowMs);
    const normalized = String(key);
    if (this.rows.has(normalized)) {
      throw new BridgeSecurityError("BRIDGE_REPLAY_DETECTED", "bridge nonce was already consumed", 409);
    }
    if (this.rows.size >= this.maxEntries) {
      throw new BridgeSecurityError(
        "BRIDGE_REPLAY_CACHE_SATURATED",
        "bridge replay cache is saturated",
        429,
      );
    }
    const expiry = Number(expiresAtMs);
    if (!Number.isSafeInteger(expiry) || expiry < Number(nowMs)) {
      throw new BridgeSecurityError(
        "BRIDGE_REPLAY_EXPIRY_INVALID",
        "bridge replay expiry is invalid",
        500,
      );
    }
    this.rows.set(normalized, expiry);
  }

  has(key, nowMs = this.clock()) {
    this.purge(nowMs);
    return this.rows.has(String(key));
  }

  purge(nowMs = this.clock()) {
    for (const [key, expiresAtMs] of this.rows.entries()) {
      if (expiresAtMs < Number(nowMs)) this.rows.delete(key);
    }
  }
}

class DurableNonceReplayStore {
  constructor({ filePath, ttlMs, maxEntries, clock = Date.now }) {
    this.filePath = path.resolve(String(filePath || ""));
    this.ttlMs = Number(ttlMs);
    this.maxEntries = Number(maxEntries);
    this.clock = clock;
    this.rows = new Map();
    if (!String(filePath || "").trim()) {
      throw new BridgeSecurityError(
        "BRIDGE_REPLAY_LEDGER_PATH_INVALID",
        "durable replay ledger path is required",
        500,
      );
    }
    this.ensureReady();
    this.load();
  }

  ensureReady() {
    const parent = path.dirname(this.filePath);
    fs.mkdirSync(parent, { recursive: true, mode: 0o700 });
    if (fs.existsSync(this.filePath) && fs.lstatSync(this.filePath).isSymbolicLink()) {
      throw new Error("replay ledger path must not be a symbolic link");
    }
    const fd = fs.openSync(this.filePath, "a", 0o600);
    try {
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
  }

  load() {
    const nowMs = Number(this.clock());
    const parseLine = (line) => {
      if (!line.trim()) return;
      let row;
      try {
        row = JSON.parse(line);
      } catch {
        throw new Error("replay ledger contains malformed JSON");
      }
      if (
        !row
        || !/^[0-9a-f]{64}$/.test(String(row.nonce_sha256 || ""))
        || !Number.isSafeInteger(row.expires_at_ms)
      ) {
        throw new Error("replay ledger contains an invalid row");
      }
      if (row.expires_at_ms >= nowMs) {
        this.rows.set(
          row.nonce_sha256,
          Math.max(this.rows.get(row.nonce_sha256) || 0, row.expires_at_ms),
        );
      }
    };
    const fd = fs.openSync(this.filePath, "r");
    const chunk = Buffer.alloc(64 * 1024);
    let carry = Buffer.alloc(0);
    try {
      while (true) {
        const count = fs.readSync(fd, chunk, 0, chunk.length, null);
        if (count <= 0) break;
        const combined = Buffer.concat([carry, chunk.subarray(0, count)]);
        let start = 0;
        for (let index = 0; index < combined.length; index += 1) {
          if (combined[index] !== 0x0a) continue;
          parseLine(combined.subarray(start, index).toString("utf8").trim());
          start = index + 1;
        }
        carry = Buffer.from(combined.subarray(start));
        if (carry.length > 16 * 1024) {
          throw new Error("replay ledger row exceeds validation limit");
        }
      }
      if (carry.length !== 0) {
        throw new Error("replay ledger has a torn terminal row");
      }
    } finally {
      fs.closeSync(fd);
    }
    if (this.rows.size > this.maxEntries) {
      throw new Error("replay ledger exceeds configured capacity");
    }
    this.compact();
  }

  consume(key, nowMs = this.clock(), expiresAtMs = Number(nowMs) + this.ttlMs) {
    this.purge(nowMs);
    const nonceSha256 = bodySha256(Buffer.from(String(key), "utf8"));
    if (this.rows.has(nonceSha256)) {
      throw new BridgeSecurityError("BRIDGE_REPLAY_DETECTED", "bridge nonce was already consumed", 409);
    }
    if (this.rows.size >= this.maxEntries) {
      throw new BridgeSecurityError(
        "BRIDGE_REPLAY_CACHE_SATURATED",
        "bridge replay ledger is saturated",
        429,
      );
    }
    if (fs.existsSync(this.filePath) && fs.lstatSync(this.filePath).isSymbolicLink()) {
      throw new BridgeSecurityError(
        "BRIDGE_REPLAY_LEDGER_UNAVAILABLE",
        "bridge replay ledger is unavailable",
        503,
      );
    }
    const durableExpiry = Number(expiresAtMs);
    if (!Number.isSafeInteger(durableExpiry) || durableExpiry < Number(nowMs)) {
      throw new BridgeSecurityError(
        "BRIDGE_REPLAY_EXPIRY_INVALID",
        "bridge replay expiry is invalid",
        500,
      );
    }
    let fd;
    try {
      fd = fs.openSync(this.filePath, "a", 0o600);
      fs.writeFileSync(
        fd,
        `${JSON.stringify({ nonce_sha256: nonceSha256, expires_at_ms: durableExpiry })}\n`,
        { encoding: "utf8" },
      );
      fs.fsyncSync(fd);
    } catch (error) {
      if (error instanceof BridgeSecurityError) throw error;
      throw new BridgeSecurityError(
        "BRIDGE_REPLAY_LEDGER_UNAVAILABLE",
        "bridge replay claim could not be persisted",
        503,
      );
    } finally {
      if (fd !== undefined) fs.closeSync(fd);
    }
    this.rows.set(nonceSha256, durableExpiry);
    try {
      if (fs.statSync(this.filePath).size > 8 * 1024 * 1024) {
        this.compact();
      }
    } catch (error) {
      if (error instanceof BridgeSecurityError) throw error;
      throw new BridgeSecurityError(
        "BRIDGE_REPLAY_LEDGER_UNAVAILABLE",
        "bridge replay ledger state could not be inspected",
        503,
      );
    }
  }

  has(key, nowMs = this.clock()) {
    this.purge(nowMs);
    const nonceSha256 = bodySha256(Buffer.from(String(key), "utf8"));
    return this.rows.has(nonceSha256);
  }

  purge(nowMs = this.clock()) {
    for (const [key, expiresAtMs] of this.rows.entries()) {
      if (expiresAtMs < Number(nowMs)) this.rows.delete(key);
    }
  }

  compact() {
    const temporaryPath = `${this.filePath}.${process.pid}.${crypto.randomUUID()}.tmp`;
    let fd;
    try {
      fd = fs.openSync(temporaryPath, "wx", 0o600);
      for (const [nonceSha256, expiresAtMs] of this.rows.entries()) {
        fs.writeFileSync(
          fd,
          `${JSON.stringify({ nonce_sha256: nonceSha256, expires_at_ms: expiresAtMs })}\n`,
          { encoding: "utf8" },
        );
      }
      fs.fsyncSync(fd);
      fs.closeSync(fd);
      fd = undefined;
      fs.renameSync(temporaryPath, this.filePath);
    } catch {
      if (fd !== undefined) {
        try { fs.closeSync(fd); } catch { }
      }
      try { fs.unlinkSync(temporaryPath); } catch { }
      throw new BridgeSecurityError(
        "BRIDGE_REPLAY_LEDGER_UNAVAILABLE",
        "bridge replay ledger compaction failed",
        503,
      );
    }
  }
}

class TokenBucketRateLimiter {
  constructor({
    ratePerSecond,
    burst,
    clock = Date.now,
    maxEntries = 10_000,
    idleTtlMs = 5 * 60 * 1000,
  }) {
    this.ratePerSecond = Number(ratePerSecond);
    this.burst = Number(burst);
    this.clock = clock;
    this.maxEntries = Number(maxEntries);
    this.idleTtlMs = Number(idleTtlMs);
    this.buckets = new Map();
  }

  consume(key, nowMs = this.clock()) {
    let normalized = String(key);
    if (!this.buckets.has(normalized) && this.buckets.size >= this.maxEntries) {
      const idleCutoff = Number(nowMs) - this.idleTtlMs;
      for (const [existingKey, bucket] of this.buckets.entries()) {
        if (bucket.updatedAtMs < idleCutoff) this.buckets.delete(existingKey);
      }
    }
    if (!this.buckets.has(normalized) && this.buckets.size >= this.maxEntries) {
      let oldestKey;
      let oldestAt = Number.POSITIVE_INFINITY;
      for (const [existingKey, bucket] of this.buckets.entries()) {
        if (bucket.updatedAtMs < oldestAt) {
          oldestAt = bucket.updatedAtMs;
          oldestKey = existingKey;
        }
      }
      if (oldestKey !== undefined) this.buckets.delete(oldestKey);
    }
    const previous = this.buckets.get(normalized) || {
      tokens: this.burst,
      updatedAtMs: Number(nowMs),
    };
    const elapsedSeconds = Math.max(0, Number(nowMs) - previous.updatedAtMs) / 1000;
    const replenished = Math.min(
      this.burst,
      previous.tokens + elapsedSeconds * this.ratePerSecond,
    );
    if (replenished < 1) {
      this.buckets.set(normalized, { tokens: replenished, updatedAtMs: Number(nowMs) });
      throw new BridgeSecurityError("BRIDGE_RATE_LIMITED", "bridge request rate exceeded", 429);
    }
    this.buckets.set(normalized, {
      tokens: replenished - 1,
      updatedAtMs: Number(nowMs),
    });
  }
}

class DurableJsonlAudit {
  constructor(filePath) {
    this.filePath = path.resolve(String(filePath || ""));
    if (!String(filePath || "").trim()) {
      throw new BridgeSecurityError("BRIDGE_AUDIT_PATH_INVALID", "audit path is required", 500);
    }
    this.poisoned = false;
    this.ensureReady();
  }

  ensureReady() {
    const parent = path.dirname(this.filePath);
    fs.mkdirSync(parent, { recursive: true, mode: 0o700 });
    if (fs.existsSync(this.filePath) && fs.lstatSync(this.filePath).isSymbolicLink()) {
      throw new Error("audit path must not be a symbolic link");
    }
    const fd = fs.openSync(this.filePath, "a", 0o600);
    try {
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
    try {
      fs.chmodSync(this.filePath, 0o600);
    } catch {
      // Windows ACLs are inherited; the file is still opened and fsynced above.
    }
    this.validateExistingRows();
  }

  validateExistingRows() {
    const fd = fs.openSync(this.filePath, "r");
    const chunk = Buffer.alloc(64 * 1024);
    let carry = Buffer.alloc(0);
    try {
      while (true) {
        const count = fs.readSync(fd, chunk, 0, chunk.length, null);
        if (count <= 0) break;
        const combined = Buffer.concat([carry, chunk.subarray(0, count)]);
        let start = 0;
        for (let index = 0; index < combined.length; index += 1) {
          if (combined[index] !== 0x0a) continue;
          const rawLine = combined.subarray(start, index).toString("utf8").trim();
          if (rawLine) {
            const parsed = JSON.parse(rawLine);
            if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
              throw new Error("audit row must be a JSON object");
            }
          }
          start = index + 1;
        }
        carry = Buffer.from(combined.subarray(start));
        if (carry.length > 64 * 1024) {
          throw new Error("audit row exceeds validation limit");
        }
      }
      if (carry.length !== 0) {
        throw new Error("audit log has a torn terminal row");
      }
    } finally {
      fs.closeSync(fd);
    }
  }

  append(record) {
    if (this.poisoned) {
      throw new Error("audit writer is poisoned after a durability failure");
    }
    const safeRecord = sanitizeAuditRecord(record);
    let fd;
    try {
      if (fs.existsSync(this.filePath) && fs.lstatSync(this.filePath).isSymbolicLink()) {
        throw new Error("audit path must not be a symbolic link");
      }
      const encoded = `${JSON.stringify(safeRecord)}\n`;
      this.rotateIfNeeded(Buffer.byteLength(encoded, "utf8"));
      fd = fs.openSync(this.filePath, "a", 0o600);
      fs.writeFileSync(fd, encoded, { encoding: "utf8" });
      fs.fsyncSync(fd);
      fs.closeSync(fd);
      fd = undefined;
      try {
        fs.chmodSync(this.filePath, 0o600);
      } catch {
        // Windows ACLs are inherited; fsync above remains the durability boundary.
      }
    } catch (error) {
      this.poisoned = true;
      if (fd !== undefined) {
        try { fs.closeSync(fd); } catch { }
      }
      throw error;
    }
  }

  rotateIfNeeded(incomingBytes) {
    const maximumBytes = 64 * 1024 * 1024;
    const currentBytes = fs.existsSync(this.filePath) ? fs.statSync(this.filePath).size : 0;
    if (currentBytes + Number(incomingBytes) <= maximumBytes) return;
    const archives = 8;
    const oldest = `${this.filePath}.${archives}`;
    if (fs.existsSync(oldest)) fs.unlinkSync(oldest);
    for (let index = archives - 1; index >= 1; index -= 1) {
      const source = `${this.filePath}.${index}`;
      if (fs.existsSync(source)) fs.renameSync(source, `${this.filePath}.${index + 1}`);
    }
    if (fs.existsSync(this.filePath)) fs.renameSync(this.filePath, `${this.filePath}.1`);
  }
}

function sanitizeAuditText(value, maxLength = 160) {
  return String(value || "").replace(/[\r\n\u0000-\u001f\u007f]/g, " ").slice(0, maxLength);
}

function sanitizeAuditRecord(record) {
  const allowed = {
    event_id: sanitizeAuditText(record.event_id, 80),
    recorded_at_ms: Number(record.recorded_at_ms) || 0,
    outcome: record.outcome === "accepted" ? "accepted" : "rejected",
    reason_code: sanitizeAuditText(record.reason_code, 96),
    mode: sanitizeAuditText(record.mode, 32),
    key_id: sanitizeAuditText(record.key_id, 128),
    transport: BRIDGE_TRANSPORT,
    method: sanitizeAuditText(record.method, 16),
    path: sanitizeAuditText(record.path, 128),
    principal: sanitizeAuditText(record.principal, 128),
    scope: sanitizeAuditText(record.scope, 128),
    peer: sanitizeAuditText(record.peer, 96),
    body_sha256: sanitizeAuditText(record.body_sha256, 64),
    nonce_sha256: sanitizeAuditText(record.nonce_sha256, 64),
    approval_ref_sha256: sanitizeAuditText(record.approval_ref_sha256, 64),
    suppressed_count: Math.max(0, Number(record.suppressed_count) || 0),
  };
  return allowed;
}

class BridgeSecurityGate {
  constructor(config, { clock = Date.now, audit = undefined, replay = undefined } = {}) {
    if (!config || !config.listenerEnabled || !config.valid) {
      throw new BridgeSecurityError("BRIDGE_CONFIG_INVALID", "enabled bridge config is required", 500);
    }
    this.config = config;
    this.clock = clock;
    try {
      this.audit = audit === undefined && config.auditPath
        ? new DurableJsonlAudit(config.auditPath)
        : audit;
    } catch {
      throw new BridgeSecurityError(
        "BRIDGE_AUDIT_UNAVAILABLE",
        "bridge security audit could not be initialized",
        503,
      );
    }
    if (!this.audit || typeof this.audit.append !== "function") {
      throw new BridgeSecurityError(
        "BRIDGE_AUDIT_UNAVAILABLE",
        "enabled bridge requires durable audit",
        500,
      );
    }
    const replayCapacity = Math.min(
      100_000,
      Math.max(
        1_000,
        Math.ceil(
          config.allowedPrincipals.length
          * config.allowedScopes.length
          * (config.ratePerSecond * (config.authTtlMs / 1000) + config.rateBurst),
        ),
      ),
    );
    try {
      this.replay = replay === undefined
        ? new DurableNonceReplayStore({
          filePath: config.replayPath,
          ttlMs: config.authTtlMs,
          maxEntries: replayCapacity,
          clock,
        })
        : replay;
    } catch {
      throw new BridgeSecurityError(
        "BRIDGE_REPLAY_LEDGER_UNAVAILABLE",
        "bridge durable replay ledger could not be initialized",
        503,
      );
    }
    if (
      !this.replay
      || typeof this.replay.consume !== "function"
      || typeof this.replay.has !== "function"
    ) {
      throw new BridgeSecurityError(
        "BRIDGE_REPLAY_LEDGER_UNAVAILABLE",
        "enabled bridge requires a replay ledger",
        500,
      );
    }
    this.rate = new TokenBucketRateLimiter({
      ratePerSecond: config.ratePerSecond,
      burst: config.rateBurst,
      clock,
    });
    this.admission = new TokenBucketRateLimiter({
      ratePerSecond: Math.max(20, config.ratePerSecond * 4),
      burst: Math.max(40, config.rateBurst * 4),
      clock,
      maxEntries: 4096,
    });
    this.globalAdmission = new TokenBucketRateLimiter({
      ratePerSecond: Math.max(50, config.ratePerSecond * 8),
      burst: Math.max(100, config.rateBurst * 8),
      clock,
      maxEntries: 1,
    });
    this.rejectionAuditState = new Map();
  }

  admitPeer(peer) {
    try {
      this.globalAdmission.consume("global", Number(this.clock()));
    } catch (error) {
      if (error instanceof BridgeSecurityError && error.reasonCode === "BRIDGE_RATE_LIMITED") {
        throw new BridgeSecurityError(
          "BRIDGE_PREAUTH_GLOBAL_RATE_LIMITED",
          "bridge global admission rate exceeded",
          429,
        );
      }
      throw error;
    }
    try {
      this.admission.consume(String(peer || "unknown"), Number(this.clock()));
    } catch (error) {
      if (error instanceof BridgeSecurityError && error.reasonCode === "BRIDGE_RATE_LIMITED") {
        throw new BridgeSecurityError(
          "BRIDGE_PREAUTH_RATE_LIMITED",
          "bridge peer admission rate exceeded",
          429,
        );
      }
      throw error;
    }
  }

  authorize({ method, requestPath, headers = {}, rawHeaders = [], rawBody, peer, requiredScope }) {
    const body = Buffer.from(rawBody || Buffer.alloc(0));
    const nowMs = Number(this.clock());
    const auditContext = {
      event_id: crypto.randomUUID(),
      recorded_at_ms: nowMs,
      mode: this.config.mode,
      method: String(method || "").toUpperCase(),
      path: String(requestPath || ""),
      peer: String(peer || ""),
      body_sha256: bodySha256(body),
      approval_ref_sha256: bodySha256(Buffer.from(this.config.approvalRef || "", "utf8")),
      key_id: "",
      principal: "",
      scope: "",
      nonce_sha256: "",
    };
    try {
      if (body.length > this.config.maxBodyBytes) {
        throw new BridgeSecurityError("BRIDGE_BODY_TOO_LARGE", "bridge body exceeds limit", 413);
      }
      if (this.config.mode === EXPOSURE_MODES.LOOPBACK && !isLoopbackPeer(peer)) {
        throw new BridgeSecurityError("BRIDGE_LOOPBACK_PEER_REQUIRED", "non-loopback peer rejected", 403);
      }
      const version = securityHeader(headers, rawHeaders, HEADER_NAMES.version);
      const keyId = securityHeader(headers, rawHeaders, HEADER_NAMES.keyId);
      const timestamp = securityHeader(headers, rawHeaders, HEADER_NAMES.timestamp);
      const nonce = securityHeader(headers, rawHeaders, HEADER_NAMES.nonce);
      const principal = securityHeader(headers, rawHeaders, HEADER_NAMES.principal);
      const scope = securityHeader(headers, rawHeaders, HEADER_NAMES.scope);
      const signature = securityHeader(headers, rawHeaders, HEADER_NAMES.signature).toLowerCase();
      auditContext.principal = principal;
      auditContext.scope = scope;
      auditContext.key_id = keyId;
      auditContext.nonce_sha256 = bodySha256(Buffer.from(nonce, "utf8"));

      if (version !== BRIDGE_VERSION) {
        throw new BridgeSecurityError("BRIDGE_VERSION_INVALID", "bridge version mismatch", 401);
      }
      if (keyId !== this.config.keyId) {
        throw new BridgeSecurityError("BRIDGE_KEY_ID_INVALID", "bridge key id mismatch", 401);
      }
      if (!/^\d{10,16}$/.test(timestamp)) {
        throw new BridgeSecurityError("BRIDGE_TIMESTAMP_INVALID", "bridge timestamp is invalid", 401);
      }
      const timestampMs = Number(timestamp);
      if (!Number.isSafeInteger(timestampMs) || Math.abs(nowMs - timestampMs) > this.config.authTtlMs) {
        throw new BridgeSecurityError("BRIDGE_TIMESTAMP_EXPIRED", "bridge timestamp outside TTL", 401);
      }
      if (!SAFE_NONCE_RE.test(nonce)) {
        throw new BridgeSecurityError("BRIDGE_NONCE_INVALID", "bridge nonce is invalid", 401);
      }
      if (!SAFE_ID_RE.test(principal) || !this.config.allowedPrincipals.includes(principal)) {
        throw new BridgeSecurityError("BRIDGE_PRINCIPAL_FORBIDDEN", "bridge principal is not allowed", 403);
      }
      if (
        !SAFE_ID_RE.test(scope)
        || scope !== requiredScope
        || !this.config.allowedScopes.includes(scope)
      ) {
        throw new BridgeSecurityError("BRIDGE_SCOPE_FORBIDDEN", "bridge scope is not allowed", 403);
      }
      if (!HMAC_SIGNATURE_RE.test(signature)) {
        throw new BridgeSecurityError("BRIDGE_SIGNATURE_INVALID", "bridge signature is invalid", 401);
      }
      const expected = buildBridgeSignature({
        secret: this.config.secretBytes,
        keyId,
        method,
        requestPath,
        timestamp,
        nonce,
        principal,
        scope,
        rawBody: body,
      });
      const expectedBytes = Buffer.from(expected, "utf8");
      const presentedBytes = Buffer.from(signature, "utf8");
      if (
        expectedBytes.length !== presentedBytes.length
        || !crypto.timingSafeEqual(expectedBytes, presentedBytes)
      ) {
        throw new BridgeSecurityError("BRIDGE_SIGNATURE_INVALID", "bridge signature mismatch", 401);
      }

      const replayKey = `${principal}\n${nonce}`;
      if (this.replay.has(replayKey, nowMs)) {
        throw new BridgeSecurityError("BRIDGE_REPLAY_DETECTED", "bridge nonce was already consumed", 409);
      }
      this.rate.consume(`${principal}\n${scope}`, nowMs);
      this.replay.consume(
        replayKey,
        nowMs,
        timestampMs + this.config.authTtlMs,
      );
      this.appendAudit({ ...auditContext, outcome: "accepted", reason_code: "BRIDGE_AUTH_ACCEPTED" });
      return Object.freeze({
        principal,
        scope,
        nonceSha256: auditContext.nonce_sha256,
        bodySha256: auditContext.body_sha256,
      });
    } catch (error) {
      const securityError = error instanceof BridgeSecurityError
        ? error
        : new BridgeSecurityError("BRIDGE_AUTH_FAILED", "bridge authorization failed", 401);
      this.appendRejectedAudit({
        ...auditContext,
        outcome: "rejected",
        reason_code: securityError.reasonCode,
      });
      securityError.auditRecorded = true;
      throw securityError;
    }
  }

  appendAudit(record) {
    if (!this.audit || typeof this.audit.append !== "function") return;
    try {
      this.audit.append(record);
    } catch {
      throw new BridgeSecurityError(
        "BRIDGE_AUDIT_UNAVAILABLE",
        "bridge security audit could not be persisted",
        503,
      );
    }
  }

  appendRejectedAudit(record) {
    const nowMs = Number(record.recorded_at_ms) || Number(this.clock());
    let key = record.reason_code === "BRIDGE_PREAUTH_GLOBAL_RATE_LIMITED"
      ? "__global_admission__"
      : `${record.peer || ""}\n${record.reason_code || ""}`;
    if (!this.rejectionAuditState.has(key) && this.rejectionAuditState.size >= 4096) {
      let oldestKey;
      let oldestAt = Number.POSITIVE_INFINITY;
      for (const [existingKey, state] of this.rejectionAuditState.entries()) {
        if (state.recordedAtMs < oldestAt) {
          oldestAt = state.recordedAtMs;
          oldestKey = existingKey;
        }
      }
      if (oldestKey !== undefined) this.rejectionAuditState.delete(oldestKey);
    }
    const previous = this.rejectionAuditState.get(key);
    if (previous && nowMs - previous.recordedAtMs < 1000) {
      previous.suppressed += 1;
      return;
    }
    const suppressed = previous ? previous.suppressed : 0;
    this.rejectionAuditState.set(key, { recordedAtMs: nowMs, suppressed: 0 });
    this.appendAudit({ ...record, suppressed_count: suppressed });
  }

  recordPreauthRejection({ peer, method, requestPath, reasonCode }) {
    const nowMs = Number(this.clock());
    this.appendRejectedAudit({
      event_id: crypto.randomUUID(),
      recorded_at_ms: nowMs,
      outcome: "rejected",
      reason_code: String(reasonCode || "BRIDGE_REQUEST_REJECTED"),
      mode: this.config.mode,
      key_id: "",
      method: String(method || "").toUpperCase(),
      path: String(requestPath || ""),
      principal: "",
      scope: "",
      peer: String(peer || ""),
      body_sha256: bodySha256(Buffer.alloc(0)),
      nonce_sha256: "",
      approval_ref_sha256: bodySha256(Buffer.from(this.config.approvalRef || "", "utf8")),
    });
  }
}

function createBridgeSecurity(config, options = {}) {
  return new BridgeSecurityGate(config, options);
}

module.exports = {
  ACTION_SCOPE,
  BRIDGE_TRANSPORT,
  BRIDGE_VERSION,
  BridgeSecurityError,
  BridgeSecurityGate,
  DEFAULT_LOOPBACK_PRINCIPAL,
  DEFAULT_KEY_ID,
  DurableNonceReplayStore,
  DurableJsonlAudit,
  EXPOSURE_MODES,
  HEADER_NAMES,
  NonceReplayCache,
  READ_SCOPE,
  TokenBucketRateLimiter,
  bodySha256,
  buildBridgeSignature,
  buildSignedHeaders,
  canonicalBridgeRequest,
  createBridgeSecurity,
  isLoopbackHost,
  isLoopbackPeer,
  publicBridgeConfig,
  resolveBridgeConfig,
  sanitizeAuditRecord,
  storagePathsAreDistinct,
};
