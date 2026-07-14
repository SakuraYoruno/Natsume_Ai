from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[2]
SECURITY_MODULE = ROOT / "desktop_pet" / "bridge_security.cjs"
MAIN_MODULE = ROOT / "desktop_pet" / "main.cjs"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")


def _node(source: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["NATSUME_DESKTOP_PET_SECURITY_MODULE"] = str(SECURITY_MODULE)
    return subprocess.run(
        [str(NODE), "-e", textwrap.dedent(source), *args],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_node_modules_are_syntax_valid_without_executing_electron() -> None:
    for module in (SECURITY_MODULE, MAIN_MODULE):
        subprocess.run(
            [str(NODE), "--check", str(module)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


def test_default_and_unknown_modes_fail_closed_without_a_listener() -> None:
    _node(
        r"""
        const assert = require("assert/strict");
        const security = require(process.env.NATSUME_DESKTOP_PET_SECURITY_MODULE);

        const defaults = security.resolveBridgeConfig({});
        assert.equal(defaults.mode, security.EXPOSURE_MODES.DISABLED);
        assert.equal(defaults.listenerEnabled, false);
        assert.equal(defaults.valid, true);

        const unknown = security.resolveBridgeConfig({
          NATSUME_BRIDGE_EXPOSURE_MODE: "PUBLIC",
        });
        assert.equal(unknown.mode, security.EXPOSURE_MODES.DISABLED);
        assert.equal(unknown.listenerEnabled, false);
        assert.equal(unknown.valid, false);
        assert(unknown.errors.includes("NATSUME_BRIDGE_EXPOSURE_MODE_INVALID"));

        const globalModeOnly = security.resolveBridgeConfig({
          NATSUME_BRIDGE_EXPOSURE_MODE: "LOOPBACK",
          NATSUME_DESKTOP_PET_HMAC_SECRET: "0123456789abcdef0123456789abcdef",
        });
        assert.equal(globalModeOnly.listenerEnabled, false);
        assert(globalModeOnly.errors.includes("NATSUME_DESKTOP_PET_BRIDGE_ENABLED_REQUIRED"));
        """
    )


def test_canonical_payload_and_hmac_match_cross_language_golden_vector() -> None:
    secret = "0123456789abcdef0123456789abcdef"
    key_id = "desktop-pet.test-key.v1"
    raw_body = '{"action":"touch","value":"雪"}'.encode()
    fields = (
        "natsume.bridge-auth.v1",
        key_id,
        "http",
        "POST",
        "/action",
        "1783828800123",
        "nonce-000000000001",
        "host.desktop-pet.test",
        "bridge:desktop-pet:action",
        hashlib.sha256(raw_body).hexdigest(),
    )
    canonical = "\n".join(fields)
    expected_signature = "sha256=" + hmac.new(
        secret.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()

    result = _node(
        r"""
        const security = require(process.env.NATSUME_DESKTOP_PET_SECURITY_MODULE);
        const rawBody = Buffer.from('{"action":"touch","value":"雪"}', "utf8");
        const request = {
          version: security.BRIDGE_VERSION,
          keyId: "desktop-pet.test-key.v1",
          transport: "HTTP",
          method: "post",
          path: "/action",
          timestampMs: "1783828800123",
          nonce: "nonce-000000000001",
          principal: "host.desktop-pet.test",
          scope: security.ACTION_SCOPE,
          rawBody,
        };
        const headers = security.buildSignedHeaders({
          secret: "0123456789abcdef0123456789abcdef",
          keyId: request.keyId,
          method: request.method,
          requestPath: request.path,
          timestampMs: request.timestampMs,
          nonce: request.nonce,
          principal: request.principal,
          scope: request.scope,
          rawBody,
        });
        process.stdout.write(JSON.stringify({
          canonical: security.canonicalBridgeRequest(request),
          signature: security.buildBridgeSignature({
            secret: "0123456789abcdef0123456789abcdef",
            ...request,
          }),
          headers,
        }));
        """
    )
    actual = json.loads(result.stdout)
    assert actual["canonical"] == canonical
    assert not actual["canonical"].endswith("\n")
    assert len(actual["canonical"].split("\n")) == 10
    assert actual["signature"] == expected_signature
    assert actual["headers"] == {
        "X-Natsume-Bridge-Version": "natsume.bridge-auth.v1",
        "X-Natsume-Bridge-Key-Id": key_id,
        "X-Natsume-Bridge-Timestamp": "1783828800123",
        "X-Natsume-Bridge-Nonce": "nonce-000000000001",
        "X-Natsume-Bridge-Principal": "host.desktop-pet.test",
        "X-Natsume-Bridge-Scope": "bridge:desktop-pet:action",
        "X-Natsume-Bridge-Signature": expected_signature,
    }


def test_loopback_requires_hmac_and_rejects_tampering_replay_and_non_loopback() -> None:
    _node(
        r"""
        const assert = require("assert/strict");
        const s = require(process.env.NATSUME_DESKTOP_PET_SECURITY_MODULE);
        const secret = "0123456789abcdef0123456789abcdef";
        const now = 1783828800123;

        const missingSecret = s.resolveBridgeConfig({
          NATSUME_BRIDGE_EXPOSURE_MODE: "LOOPBACK",
          NATSUME_DESKTOP_PET_BRIDGE_ENABLED: "true",
        });
        assert.equal(missingSecret.listenerEnabled, false);
        assert(missingSecret.errors.includes("NATSUME_DESKTOP_PET_HMAC_SECRET_TOO_SHORT"));

        const publicHost = s.resolveBridgeConfig({
          NATSUME_BRIDGE_EXPOSURE_MODE: "LOOPBACK",
          NATSUME_DESKTOP_PET_BRIDGE_ENABLED: "true",
          NATSUME_DESKTOP_PET_HMAC_SECRET: secret,
          NATSUME_DESKTOP_PET_HOST: "0.0.0.0",
        });
        assert.equal(publicHost.listenerEnabled, false);
        assert(publicHost.errors.includes("NATSUME_DESKTOP_PET_LOOPBACK_HOST_REQUIRED"));

        const config = s.resolveBridgeConfig({
          NATSUME_BRIDGE_EXPOSURE_MODE: "LOOPBACK",
          NATSUME_DESKTOP_PET_BRIDGE_ENABLED: "true",
          NATSUME_DESKTOP_PET_HMAC_SECRET: secret,
          NATSUME_DESKTOP_PET_HOST: "::1",
        });
        assert.equal(config.listenerEnabled, true);
        assert.equal(config.auditRequired, true);
        assert.equal(config.auditPath.endsWith("desktop_pet_security.jsonl"), true);
        assert.equal(Object.keys(config).includes("secretBytes"), false);
        assert.equal(JSON.stringify(s.publicBridgeConfig(config)).includes(secret), false);

        const gate = s.createBridgeSecurity(config, {
          clock: () => now,
          audit: { append() {} },
          replay: new s.NonceReplayCache({
            ttlMs: config.authTtlMs,
            maxEntries: 1000,
            clock: () => now,
          }),
        });
        const rawBody = Buffer.from('{"action":"touch"}', "utf8");
        const signed = (nonce, body = rawBody, scope = s.ACTION_SCOPE) => {
          const headers = s.buildSignedHeaders({
            secret,
            keyId: config.keyId,
            method: "POST",
            requestPath: "/action",
            timestampMs: now,
            nonce,
            principal: s.DEFAULT_LOOPBACK_PRINCIPAL,
            scope,
            rawBody: body,
          });
          return Object.entries(headers).flat();
        };
        const authorize = (nonce, body = rawBody, rawHeaders = signed(nonce, body)) =>
          gate.authorize({
            method: "POST",
            requestPath: "/action",
            rawHeaders,
            rawBody: body,
            peer: "::1",
            requiredScope: s.ACTION_SCOPE,
          });

        const accepted = authorize("nonce-000000000001");
        assert.equal(accepted.scope, s.ACTION_SCOPE);
        assert.throws(
          () => authorize("nonce-000000000001"),
          (error) => error.reasonCode === "BRIDGE_REPLAY_DETECTED",
        );

        const original = Buffer.from('{"action":"show"}', "utf8");
        const tampered = Buffer.from('{"action":"hide"}', "utf8");
        assert.throws(
          () => authorize("nonce-000000000002", tampered, signed("nonce-000000000002", original)),
          (error) => error.reasonCode === "BRIDGE_SIGNATURE_INVALID",
        );

        assert.throws(
          () => gate.authorize({
            method: "POST",
            requestPath: "/action",
            rawHeaders: signed("nonce-000000000003"),
            rawBody,
            peer: "192.0.2.15",
            requiredScope: s.ACTION_SCOPE,
          }),
          (error) => error.reasonCode === "BRIDGE_LOOPBACK_PEER_REQUIRED",
        );

        const duplicate = signed("nonce-000000000004");
        duplicate.push("X-Natsume-Bridge-Signature", duplicate.at(-1));
        assert.throws(
          () => authorize("nonce-000000000004", rawBody, duplicate),
          (error) => error.reasonCode === "BRIDGE_AUTH_HEADER_AMBIGUOUS",
        );
        """
    )


def test_rate_limit_runs_before_any_action_dispatch() -> None:
    _node(
        r"""
        const assert = require("assert/strict");
        const s = require(process.env.NATSUME_DESKTOP_PET_SECURITY_MODULE);
        const secret = "0123456789abcdef0123456789abcdef";
        const now = 1783828800123;
        const config = s.resolveBridgeConfig({
          NATSUME_BRIDGE_EXPOSURE_MODE: "LOOPBACK",
          NATSUME_DESKTOP_PET_BRIDGE_ENABLED: "true",
          NATSUME_DESKTOP_PET_HMAC_SECRET: secret,
          NATSUME_BRIDGE_RATE_PER_SECOND: "1",
          NATSUME_BRIDGE_RATE_BURST: "1",
        });
        const gate = s.createBridgeSecurity(config, {
          clock: () => now,
          audit: { append() {} },
          replay: new s.NonceReplayCache({
            ttlMs: config.authTtlMs,
            maxEntries: 1000,
            clock: () => now,
          }),
        });
        const rawBody = Buffer.from("{}", "utf8");
        const request = (nonce) => ({
          method: "POST",
          requestPath: "/action",
          rawHeaders: Object.entries(s.buildSignedHeaders({
            secret,
            keyId: config.keyId,
            method: "POST",
            requestPath: "/action",
            timestampMs: now,
            nonce,
            principal: s.DEFAULT_LOOPBACK_PRINCIPAL,
            scope: s.ACTION_SCOPE,
            rawBody,
          })).flat(),
          rawBody,
          peer: "127.0.0.1",
          requiredScope: s.ACTION_SCOPE,
        });
        gate.authorize(request("nonce-000000000010"));
        assert.throws(
          () => gate.authorize(request("nonce-000000000011")),
          (error) => error.reasonCode === "BRIDGE_RATE_LIMITED",
        );
        """
    )


def test_admission_has_global_bound_and_lru_peer_recovery() -> None:
    _node(
        r"""
        const assert = require("assert/strict");
        const s = require(process.env.NATSUME_DESKTOP_PET_SECURITY_MODULE);
        const limiter = new s.TokenBucketRateLimiter({
          ratePerSecond: 1,
          burst: 1,
          maxEntries: 2,
          idleTtlMs: 1000,
          clock: () => 1000,
        });
        limiter.consume("peer-a", 1000);
        limiter.consume("peer-b", 1000);
        limiter.consume("peer-c", 1000);
        assert.equal(limiter.buckets.size, 2);
        assert.equal(limiter.buckets.has("__overflow__"), false);
        assert.equal(limiter.buckets.has("peer-c"), true);

        const config = s.resolveBridgeConfig({
          NATSUME_BRIDGE_EXPOSURE_MODE: "LOOPBACK",
          NATSUME_DESKTOP_PET_BRIDGE_ENABLED: "true",
          NATSUME_DESKTOP_PET_HMAC_SECRET: "0123456789abcdef0123456789abcdef",
        });
        const gate = s.createBridgeSecurity(config, {
          clock: () => 1000,
          audit: { append() {} },
          replay: new s.NonceReplayCache({
            ttlMs: config.authTtlMs,
            maxEntries: 1000,
            clock: () => 1000,
          }),
        });
        for (let index = 0; index < 160; index += 1) {
          gate.admitPeer(`2001:db8::${index}`);
        }
        assert.throws(
          () => gate.admitPeer("2001:db8::overflow"),
          (error) => error.reasonCode === "BRIDGE_PREAUTH_GLOBAL_RATE_LIMITED",
        );
        """
    )


def test_future_timestamp_cannot_outlive_replay_claim() -> None:
    _node(
        r"""
        const assert = require("assert/strict");
        const s = require(process.env.NATSUME_DESKTOP_PET_SECURITY_MODULE);
        const secret = "0123456789abcdef0123456789abcdef";
        const initialNow = 1783828800123;
        let now = initialNow;
        const config = s.resolveBridgeConfig({
          NATSUME_BRIDGE_EXPOSURE_MODE: "LOOPBACK",
          NATSUME_DESKTOP_PET_BRIDGE_ENABLED: "true",
          NATSUME_DESKTOP_PET_HMAC_SECRET: secret,
          NATSUME_BRIDGE_NONCE_TTL_SECONDS: "1",
        });
        const gate = s.createBridgeSecurity(config, {
          clock: () => now,
          audit: { append() {} },
          replay: new s.NonceReplayCache({
            ttlMs: config.authTtlMs,
            maxEntries: 1000,
            clock: () => now,
          }),
        });
        const rawBody = Buffer.from('{"action":"touch"}', "utf8");
        const timestampMs = initialNow + config.authTtlMs;
        const rawHeaders = Object.entries(s.buildSignedHeaders({
          secret,
          keyId: config.keyId,
          method: "POST",
          requestPath: "/action",
          timestampMs,
          nonce: "nonce-future-window-0001",
          principal: s.DEFAULT_LOOPBACK_PRINCIPAL,
          scope: s.ACTION_SCOPE,
          rawBody,
        })).flat();
        const request = () => ({
          method: "POST",
          requestPath: "/action",
          rawHeaders,
          rawBody,
          peer: "127.0.0.1",
          requiredScope: s.ACTION_SCOPE,
        });
        gate.authorize(request());
        now = initialNow + config.authTtlMs + 1;
        assert.throws(
          () => gate.authorize(request()),
          (error) => error.reasonCode === "BRIDGE_REPLAY_DETECTED",
        );
        """
    )


def test_torn_audit_log_stops_listener_before_any_effect(tmp_path: Path) -> None:
    audit_path = tmp_path / "torn-audit.jsonl"
    audit_path.write_text('{"event_id":"torn"', encoding="utf-8")
    replay_path = tmp_path / "replay.jsonl"
    _node(
        r"""
        const assert = require("assert/strict");
        const s = require(process.env.NATSUME_DESKTOP_PET_SECURITY_MODULE);
        const config = s.resolveBridgeConfig({
          NATSUME_BRIDGE_EXPOSURE_MODE: "LOOPBACK",
          NATSUME_DESKTOP_PET_BRIDGE_ENABLED: "true",
          NATSUME_DESKTOP_PET_HMAC_SECRET: "0123456789abcdef0123456789abcdef",
          NATSUME_DESKTOP_PET_AUDIT_JSONL_PATH: process.argv[1],
          NATSUME_DESKTOP_PET_REPLAY_LEDGER_PATH: process.argv[2],
        });
        assert.equal(config.listenerEnabled, true);
        assert.throws(
          () => s.createBridgeSecurity(config),
          (error) => error.reasonCode === "BRIDGE_AUDIT_UNAVAILABLE",
        );
        """,
        str(audit_path),
        str(replay_path),
    )


def test_runtime_partial_audit_write_poison_blocks_later_actions(tmp_path: Path) -> None:
    audit_path = tmp_path / "runtime-torn-audit.jsonl"
    replay_path = tmp_path / "runtime-torn-replay.jsonl"
    _node(
        r"""
        const assert = require("assert/strict");
        const fs = require("fs");
        const s = require(process.env.NATSUME_DESKTOP_PET_SECURITY_MODULE);
        const secret = "0123456789abcdef0123456789abcdef";
        const now = 1783828800123;
        const config = s.resolveBridgeConfig({
          NATSUME_BRIDGE_EXPOSURE_MODE: "LOOPBACK",
          NATSUME_DESKTOP_PET_BRIDGE_ENABLED: "true",
          NATSUME_DESKTOP_PET_HMAC_SECRET: secret,
          NATSUME_DESKTOP_PET_AUDIT_JSONL_PATH: process.argv[1],
          NATSUME_DESKTOP_PET_REPLAY_LEDGER_PATH: process.argv[2],
        });
        const gate = s.createBridgeSecurity(config, { clock: () => now });
        const rawBody = Buffer.from('{"action":"touch"}', "utf8");
        const request = (nonce) => ({
          method: "POST",
          requestPath: "/action",
          rawHeaders: Object.entries(s.buildSignedHeaders({
            secret,
            keyId: config.keyId,
            method: "POST",
            requestPath: "/action",
            timestampMs: now,
            nonce,
            principal: s.DEFAULT_LOOPBACK_PRINCIPAL,
            scope: s.ACTION_SCOPE,
            rawBody,
          })).flat(),
          rawBody,
          peer: "127.0.0.1",
          requiredScope: s.ACTION_SCOPE,
        });
        const originalWrite = fs.writeFileSync;
        let injected = false;
        fs.writeFileSync = (fd, data, options) => {
          const text = String(data);
          if (!injected && text.includes('"outcome":"accepted"')) {
            injected = true;
            originalWrite(fd, text.slice(0, 25), options);
            throw new Error("simulated partial audit write");
          }
          return originalWrite(fd, data, options);
        };
        assert.throws(
          () => gate.authorize(request("nonce-runtime-torn-0001")),
          (error) => error.reasonCode === "BRIDGE_AUDIT_UNAVAILABLE",
        );
        fs.writeFileSync = originalWrite;
        assert.throws(
          () => gate.authorize(request("nonce-runtime-torn-0002")),
          (error) => error.reasonCode === "BRIDGE_AUDIT_UNAVAILABLE",
        );
        """,
        str(audit_path),
        str(replay_path),
    )


def test_audit_and_replay_path_families_must_be_distinct(tmp_path: Path) -> None:
    _node(
        r"""
        const assert = require("assert/strict");
        const fs = require("fs");
        const path = require("path");
        const s = require(process.env.NATSUME_DESKTOP_PET_SECURITY_MODULE);
        const root = process.argv[1];
        const base = {
          NATSUME_BRIDGE_EXPOSURE_MODE: "LOOPBACK",
          NATSUME_DESKTOP_PET_BRIDGE_ENABLED: "true",
          NATSUME_DESKTOP_PET_HMAC_SECRET: "0123456789abcdef0123456789abcdef",
        };
        const auditPath = path.join(process.argv[1], "desktop-audit.jsonl");
        const config = s.resolveBridgeConfig({
          ...base,
          NATSUME_DESKTOP_PET_AUDIT_JSONL_PATH: auditPath,
          NATSUME_DESKTOP_PET_REPLAY_LEDGER_PATH: `${auditPath}.8`,
        });
        assert.equal(config.listenerEnabled, false);
        assert(config.errors.includes("NATSUME_DESKTOP_PET_STORAGE_PATH_COLLISION"));

        const realDirectory = path.join(root, "real-storage");
        const aliasDirectory = path.join(root, "alias-storage");
        fs.mkdirSync(realDirectory);
        fs.symlinkSync(
          realDirectory,
          aliasDirectory,
          process.platform === "win32" ? "junction" : "dir",
        );
        const parentAlias = s.resolveBridgeConfig({
          ...base,
          NATSUME_DESKTOP_PET_AUDIT_JSONL_PATH: path.join(realDirectory, "shared.jsonl"),
          NATSUME_DESKTOP_PET_REPLAY_LEDGER_PATH: path.join(aliasDirectory, "shared.jsonl"),
        });
        assert.equal(parentAlias.listenerEnabled, false);
        assert(parentAlias.errors.includes("NATSUME_DESKTOP_PET_STORAGE_PATH_COLLISION"));

        const hardlinkAudit = path.join(root, "hardlink-audit.jsonl");
        const hardlinkReplay = path.join(root, "hardlink-replay.jsonl");
        fs.writeFileSync(hardlinkAudit, "");
        fs.linkSync(hardlinkAudit, hardlinkReplay);
        const hardlink = s.resolveBridgeConfig({
          ...base,
          NATSUME_DESKTOP_PET_AUDIT_JSONL_PATH: hardlinkAudit,
          NATSUME_DESKTOP_PET_REPLAY_LEDGER_PATH: hardlinkReplay,
        });
        assert.equal(hardlink.listenerEnabled, false);
        assert(hardlink.errors.includes("NATSUME_DESKTOP_PET_STORAGE_PATH_COLLISION"));
        """,
        str(tmp_path),
    )


def test_remote_mode_requires_explicit_controls_and_writes_secret_free_audit(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "desktop-pet-security.jsonl"
    secret = "remote-secret-0123456789-ABCDEFGHIJK"
    approval = "approval:TASK-0494:remote-test"
    result = _node(
        r"""
        const assert = require("assert/strict");
        const s = require(process.env.NATSUME_DESKTOP_PET_SECURITY_MODULE);
        const auditPath = process.argv[1];
        const secret = "remote-secret-0123456789-ABCDEFGHIJK";
        const approval = "approval:TASK-0494:remote-test";
        const principal = "service.remote.desktop-pet";
        const now = 1783828800123;

        const incomplete = s.resolveBridgeConfig({
          NATSUME_BRIDGE_EXPOSURE_MODE: "REMOTE_APPROVED",
          NATSUME_DESKTOP_PET_BRIDGE_ENABLED: "true",
        });
        assert.equal(incomplete.listenerEnabled, false);
        for (const required of [
          "NATSUME_DESKTOP_PET_HMAC_SECRET_TOO_SHORT",
          "NATSUME_BRIDGE_REMOTE_KEY_ID_MUST_BE_EXPLICIT",
          "NATSUME_DESKTOP_PET_REMOTE_APPROVAL_REF_INVALID",
          "NATSUME_DESKTOP_PET_REMOTE_PRINCIPAL_MUST_BE_EXPLICIT",
          "NATSUME_BRIDGE_REMOTE_SCOPES_MUST_INCLUDE_READ",
          "NATSUME_BRIDGE_REMOTE_RATE_MUST_BE_EXPLICIT",
          "NATSUME_BRIDGE_REMOTE_BURST_MUST_BE_EXPLICIT",
          "NATSUME_BRIDGE_REMOTE_TTL_MUST_BE_EXPLICIT",
          "NATSUME_DESKTOP_PET_AUDIT_JSONL_PATH_REQUIRED",
        ]) assert(incomplete.errors.includes(required), required);

        const config = s.resolveBridgeConfig({
          NATSUME_BRIDGE_EXPOSURE_MODE: "REMOTE_APPROVED",
          NATSUME_DESKTOP_PET_BRIDGE_ENABLED: "true",
          NATSUME_DESKTOP_PET_HOST: "0.0.0.0",
          NATSUME_DESKTOP_PET_REMOTE_APPROVAL_REF: approval,
          NATSUME_DESKTOP_PET_HMAC_KEY_ID: "desktop-pet.remote.v1",
          NATSUME_DESKTOP_PET_HMAC_SECRET: secret,
          NATSUME_DESKTOP_PET_ALLOWED_PRINCIPAL: principal,
          NATSUME_DESKTOP_PET_ALLOWED_SCOPES: `${s.ACTION_SCOPE},${s.READ_SCOPE}`,
          NATSUME_BRIDGE_RATE_PER_SECOND: "5",
          NATSUME_BRIDGE_RATE_BURST: "5",
          NATSUME_BRIDGE_NONCE_TTL_SECONDS: "30",
          NATSUME_BRIDGE_MAX_BODY_BYTES: "4096",
          NATSUME_DESKTOP_PET_AUDIT_JSONL_PATH: auditPath,
        });
        assert.equal(config.listenerEnabled, true, config.errors.join(","));
        assert.equal(config.auditRequired, true);
        const gate = s.createBridgeSecurity(config, { clock: () => now });

        const call = ({ method, path, scope, nonce, rawBody }) => {
          const headers = s.buildSignedHeaders({
            secret,
            keyId: config.keyId,
            method,
            requestPath: path,
            timestampMs: now,
            nonce,
            principal,
            scope,
            rawBody,
          });
          return gate.authorize({
            method,
            requestPath: path,
            rawHeaders: Object.entries(headers).flat(),
            rawBody,
            peer: "198.51.100.22",
            requiredScope: path === "/health" ? s.READ_SCOPE : s.ACTION_SCOPE,
          });
        };

        const actionBody = Buffer.from('{"action":"show","note":"private-body"}', "utf8");
        const actionNonce = "nonce-remote-00000001";
        const action = call({
          method: "POST",
          path: "/action",
          scope: s.ACTION_SCOPE,
          nonce: actionNonce,
          rawBody: actionBody,
        });
        assert.equal(action.principal, principal);

        const healthNonce = "nonce-remote-00000002";
        call({
          method: "GET",
          path: "/health",
          scope: s.READ_SCOPE,
          nonce: healthNonce,
          rawBody: Buffer.alloc(0),
        });

        const rejectedNonce = "nonce-remote-00000003";
        assert.throws(
          () => call({
            method: "POST",
            path: "/action",
            scope: s.READ_SCOPE,
            nonce: rejectedNonce,
            rawBody: actionBody,
          }),
          (error) => error.reasonCode === "BRIDGE_SCOPE_FORBIDDEN",
        );

        const restartedGate = s.createBridgeSecurity(config, { clock: () => now });
        const replayHeaders = s.buildSignedHeaders({
          secret,
          keyId: config.keyId,
          method: "POST",
          requestPath: "/action",
          timestampMs: now,
          nonce: actionNonce,
          principal,
          scope: s.ACTION_SCOPE,
          rawBody: actionBody,
        });
        assert.throws(
          () => restartedGate.authorize({
            method: "POST",
            requestPath: "/action",
            rawHeaders: Object.entries(replayHeaders).flat(),
            rawBody: actionBody,
            peer: "198.51.100.22",
            requiredScope: s.ACTION_SCOPE,
          }),
          (error) => error.reasonCode === "BRIDGE_REPLAY_DETECTED",
        );
        process.stdout.write(JSON.stringify({
          actionNonce,
          healthNonce,
          rejectedNonce,
          signature: s.buildBridgeSignature({
            secret,
            keyId: config.keyId,
            method: "POST",
            requestPath: "/action",
            timestampMs: now,
            nonce: actionNonce,
            principal,
            scope: s.ACTION_SCOPE,
            rawBody: actionBody,
          }),
        }));
        """,
        str(audit_path),
    )
    secrets_from_node = json.loads(result.stdout)
    audit_text = audit_path.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in audit_text.splitlines() if line.strip()]
    assert [row["outcome"] for row in rows] == [
        "accepted",
        "accepted",
        "rejected",
        "rejected",
    ]
    assert rows[-2]["reason_code"] == "BRIDGE_SCOPE_FORBIDDEN"
    assert rows[-1]["reason_code"] == "BRIDGE_REPLAY_DETECTED"
    assert {row["path"] for row in rows} == {"/action", "/health"}
    assert all(len(row["body_sha256"]) == 64 for row in rows)
    assert all(set(row) == {
        "event_id",
        "recorded_at_ms",
        "outcome",
        "reason_code",
        "mode",
        "key_id",
        "transport",
        "method",
        "path",
        "principal",
        "scope",
        "peer",
        "body_sha256",
        "nonce_sha256",
        "approval_ref_sha256",
        "suppressed_count",
    } for row in rows)
    for forbidden in (
        secret,
        approval,
        "private-body",
        '{"action":"show"',
        secrets_from_node["actionNonce"],
        secrets_from_node["healthNonce"],
        secrets_from_node["rejectedNonce"],
        secrets_from_node["signature"],
    ):
        assert forbidden not in audit_text


def test_main_wires_auth_before_dispatch_and_keeps_health_minimal() -> None:
    source = MAIN_MODULE.read_text(encoding="utf-8")
    assert "resolveBridgeConfig(process.env)" in source
    assert "if (bridgeConfig.listenerEnabled)" in source
    assert "createWindow();\n  startHttpServer();" not in source
    assert "readJson(" not in source

    helper = source[source.index("async function readAuthorizedBody") : source.index("function parseJsonObject")]
    assert "bridgeSecurity.authorize" in helper
    action_route = source[
        source.index('req.method === "POST" && req.url === "/action"') :
        source.index("writeJson(res, 404")
    ]
    assert action_route.index("readAuthorizedBody(req, ACTION_SCOPE)") < action_route.index(
        "sendAction(payload)"
    )

    health_route = source[
        source.index('req.method === "GET" && req.url === "/health"') :
        source.index('req.method === "POST" && req.url === "/action"')
    ]
    assert "EXPOSURE_MODES.REMOTE_APPROVED" in health_route
    assert "readAuthorizedBody(req, READ_SCOPE)" in health_route
    assert 'writeJson(res, 200, { status: "ok" })' in health_route
    for leaked_detail in ("windowStatus", "rendererState", "port: PORT"):
        assert leaked_detail not in health_route
