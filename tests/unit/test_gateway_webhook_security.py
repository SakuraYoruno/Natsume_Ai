from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
from Crypto.Cipher import AES

from edge_client.gateway.webhook_security import (
    FEISHU_NONCE_HEADER,
    FEISHU_SIGNATURE_HEADER,
    FEISHU_TIMESTAMP_HEADER,
    GW_BODY_INVALID,
    GW_HEADER_AMBIGUOUS,
    GW_HEADER_MISSING,
    GW_PLATFORM_MISMATCH,
    GW_QUERY_AMBIGUOUS,
    GW_QUERY_MISSING,
    GW_SECRET_MISSING,
    GW_SIGNATURE_INVALID,
    GW_TIMESTAMP_FUTURE,
    GW_TIMESTAMP_INVALID,
    GW_TIMESTAMP_STALE,
    GW_VERIFICATION_FAILED,
    GW_VERIFIER_UNAVAILABLE,
    INTERNAL_NONCE_HEADER,
    INTERNAL_SIGNATURE_HEADER,
    INTERNAL_TIMESTAMP_HEADER,
    LINE_SIGNATURE_HEADER,
    FeishuWebhookVerifier,
    InternalHmacWebhookVerifier,
    LineWebhookVerifier,
    RawWebhookEnvelope,
    VerifierRegistry,
    WeComWebhookVerifier,
    WebhookVerificationError,
    build_internal_signature,
)


NOW_MS = 1_750_000_000_000
NOW_S = str(NOW_MS // 1000)


def _line_signature(secret: str, body: bytes) -> str:
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")


def _feishu_signature(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    return hashlib.sha256(
        timestamp.encode("utf-8")
        + nonce.encode("utf-8")
        + secret.encode("utf-8")
        + body
    ).hexdigest()


def _wecom_signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    canonical = "".join(sorted((token, timestamp, nonce, encrypted)))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()  # noqa: S324


def _pkcs7(value: bytes, block_size: int) -> bytes:
    padding = block_size - (len(value) % block_size)
    return value + bytes((padding,)) * padding


def _feishu_encrypted_body(
    encrypt_key: str,
    payload: dict,
    *,
    plaintext: bytes | None = None,
) -> tuple[bytes, bytes]:
    resolved_plaintext = plaintext or json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = b"feishu-test-iv!!"
    ciphertext = AES.new(key, AES.MODE_CBC, iv=iv).encrypt(
        _pkcs7(resolved_plaintext, 16)
    )
    encrypted = base64.b64encode(iv + ciphertext).decode("ascii")
    body = json.dumps({"encrypt": encrypted}, separators=(",", ":")).encode("utf-8")
    return body, resolved_plaintext


def _wecom_encoding_key() -> tuple[str, bytes]:
    key = bytes(range(1, 33))
    return base64.b64encode(key).decode("ascii").rstrip("="), key


def _wecom_encrypt(
    aes_key: bytes,
    receive_id: str,
    message: bytes,
    *,
    valid_padding: bool = True,
) -> str:
    framed = (
        b"0123456789abcdef"
        + len(message).to_bytes(4, "big")
        + message
        + receive_id.encode("utf-8")
    )
    padded = _pkcs7(framed, 32) if valid_padding else framed + bytes(16 - len(framed) % 16)
    ciphertext = AES.new(aes_key, AES.MODE_CBC, iv=aes_key[:16]).encrypt(padded)
    return base64.b64encode(ciphertext).decode("ascii")


def _internal_signature(
    secret: str,
    *,
    platform: str,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> str:
    canonical = (
        "natsume-webhook-v1\n"
        f"{platform}\n"
        f"{method.upper()}\n"
        f"{path}\n"
        f"{timestamp}\n"
        f"{nonce}\n"
    ).encode("utf-8") + body
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def _registry(*verifiers):
    return VerifierRegistry(verifiers, now_ms_fn=lambda: NOW_MS)


def _feishu_envelope(
    *,
    body: bytes = b'{"event":{"type":"message"}}',
    secret: str = "feishu-test-key",
    timestamp: str = NOW_S,
    nonce: str = "nonce-feishu-1",
    signature: str | None = None,
) -> RawWebhookEnvelope:
    resolved_signature = signature or _feishu_signature(secret, timestamp, nonce, body)
    return RawWebhookEnvelope(
        platform="feishu",
        method="POST",
        path="/v1/gateway/feishu/webhook",
        raw_body=body,
        headers=(
            (FEISHU_TIMESTAMP_HEADER, timestamp),
            (FEISHU_NONCE_HEADER, nonce),
            (FEISHU_SIGNATURE_HEADER, resolved_signature),
        ),
        received_at_ms=NOW_MS,
        request_id="req-feishu-1",
    )


def _internal_envelope(
    *,
    platform: str = "qq",
    body: bytes = b'{"post_type":"message"}',
    secret: str = "internal-test-key",
    timestamp: str = NOW_S,
    nonce: str = "nonce-internal-1",
    signature: str | None = None,
) -> RawWebhookEnvelope:
    path = "/v1/gateway/reply" if platform == "internal_reply" else f"/v1/gateway/{platform}/webhook"
    resolved_signature = signature or _internal_signature(
        secret,
        platform=platform,
        method="POST",
        path=path,
        timestamp=timestamp,
        nonce=nonce,
        body=body,
    )
    return RawWebhookEnvelope(
        platform=platform,
        method="POST",
        path=path,
        raw_body=body,
        headers=(
            (INTERNAL_TIMESTAMP_HEADER, timestamp),
            (INTERNAL_NONCE_HEADER, nonce),
            (INTERNAL_SIGNATURE_HEADER, resolved_signature),
        ),
        received_at_ms=NOW_MS,
    )


def test_registry_has_no_implicit_or_insecure_fallback() -> None:
    envelope = RawWebhookEnvelope(
        platform="line",
        method="POST",
        path="/v1/gateway/line/webhook",
        raw_body=b"{}",
    )
    with pytest.raises(WebhookVerificationError) as caught:
        _registry().verify(envelope)
    assert caught.value.reason_code == GW_VERIFIER_UNAVAILABLE


def test_line_verifies_exact_raw_body_and_returns_safe_evidence() -> None:
    secret = "line-test-secret"
    body = b'{"events":[{"message":{"text":"hello"}}]}'
    envelope = RawWebhookEnvelope(
        platform="LINE",
        method="post",
        path="/v1/gateway/line/webhook",
        raw_body=body,
        headers={"X-Line-Signature": _line_signature(secret, body)},
        received_at_ms=NOW_MS,
        request_id="req-line-1",
    )

    result = _registry(LineWebhookVerifier(secret)).verify(envelope)

    assert result.platform == "line"
    assert result.profile_id == "line-hmac-sha256-v1"
    assert result.body_sha256 == hashlib.sha256(body).hexdigest()
    assert result.replay_key.startswith("gw:line:")
    assert result.payload_bytes == body
    assert result.claims["signature_scheme"] == "hmac-sha256-base64"
    assert body.decode() not in repr(result)


def test_line_rejects_mutated_raw_body() -> None:
    secret = "line-test-secret"
    signed_body = b'{"value":1}'
    envelope = RawWebhookEnvelope(
        platform="line",
        method="POST",
        path="/v1/gateway/line/webhook",
        raw_body=b'{"value": 1}',
        headers=((LINE_SIGNATURE_HEADER, _line_signature(secret, signed_body)),),
    )
    with pytest.raises(WebhookVerificationError) as caught:
        _registry(LineWebhookVerifier(secret)).verify(envelope)
    assert caught.value.reason_code == GW_SIGNATURE_INVALID


def test_line_missing_secret_and_signature_fail_closed() -> None:
    envelope = RawWebhookEnvelope(
        platform="line",
        method="POST",
        path="/v1/gateway/line/webhook",
        raw_body=b"{}",
    )
    with pytest.raises(WebhookVerificationError) as missing_secret:
        _registry(LineWebhookVerifier()).verify(envelope)
    assert missing_secret.value.reason_code == GW_SECRET_MISSING

    with pytest.raises(WebhookVerificationError) as missing_header:
        _registry(LineWebhookVerifier("configured")).verify(envelope)
    assert missing_header.value.reason_code == GW_HEADER_MISSING


def test_feishu_verifies_raw_body_timestamp_nonce_and_key() -> None:
    secret = "feishu-test-key"
    envelope = _feishu_envelope(secret=secret)
    result = _registry(
        FeishuWebhookVerifier(secret, verify_only=True)
    ).verify(envelope)

    assert result.principal_id == "feishu:webhook"
    assert result.claims["timestamp_s"] == int(NOW_S)
    assert result.claims["nonce"] == "nonce-feishu-1"
    assert result.claims["nonce_sha256"] == hashlib.sha256(b"nonce-feishu-1").hexdigest()


def test_feishu_rejects_json_reserialization_after_signing() -> None:
    secret = "feishu-test-key"
    signed_body = b'{"a":1,"b":2}'
    delivered_body = b'{"b":2,"a":1}'
    signature = _feishu_signature(secret, NOW_S, "nonce-feishu-1", signed_body)
    envelope = _feishu_envelope(
        body=delivered_body,
        secret=secret,
        signature=signature,
    )
    with pytest.raises(WebhookVerificationError) as caught:
        _registry(FeishuWebhookVerifier(secret)).verify(envelope)
    assert caught.value.reason_code == GW_SIGNATURE_INVALID


@pytest.mark.parametrize(
    ("timestamp", "reason_code"),
    [
        (str(int(NOW_S) - 301), GW_TIMESTAMP_STALE),
        (str(int(NOW_S) + 31), GW_TIMESTAMP_FUTURE),
        ("17.5", GW_TIMESTAMP_INVALID),
    ],
)
def test_feishu_rejects_stale_future_and_invalid_timestamps(
    timestamp: str,
    reason_code: str,
) -> None:
    secret = "feishu-test-key"
    envelope = _feishu_envelope(secret=secret, timestamp=timestamp)
    with pytest.raises(WebhookVerificationError) as caught:
        _registry(FeishuWebhookVerifier(secret)).verify(envelope)
    assert caught.value.reason_code == reason_code


def test_feishu_authenticated_decrypt_returns_plaintext_and_keeps_raw_digest() -> None:
    encrypt_key = "feishu-authenticated-encrypt-key"
    verification_token = "feishu-verification-token"
    payload = {
        "schema": "2.0",
        "header": {
            "event_id": "evt-1",
            "event_type": "im.message.receive_v1",
            "token": verification_token,
        },
        "event": {"message": {"message_id": "om-1"}},
    }
    body, plaintext = _feishu_encrypted_body(encrypt_key, payload)
    envelope = _feishu_envelope(body=body, secret=encrypt_key)

    result = _registry(
        FeishuWebhookVerifier(
            encrypt_key=encrypt_key,
            verification_token=verification_token,
        )
    ).verify(envelope)

    assert result.payload_bytes == plaintext
    assert json.loads(result.payload_bytes) == payload
    assert result.body_sha256 == hashlib.sha256(body).hexdigest()
    assert result.claims["authenticated_decrypt"] is True
    assert result.claims["verification_token_verified"] is True
    assert verification_token not in repr(result)


def test_feishu_verification_token_mismatch_fails_closed_without_leak() -> None:
    encrypt_key = "feishu-authenticated-encrypt-key"
    configured_token = "configured-verification-token"
    body, _plaintext = _feishu_encrypted_body(
        encrypt_key,
        {"token": "presented-wrong-token", "type": "event_callback"},
    )
    envelope = _feishu_envelope(body=body, secret=encrypt_key)

    with pytest.raises(WebhookVerificationError) as caught:
        _registry(
            FeishuWebhookVerifier(
                encrypt_key=encrypt_key,
                verification_token=configured_token,
                verify_only=False,
            )
        ).verify(envelope)
    assert caught.value.reason_code == GW_SIGNATURE_INVALID
    rendered = str(caught.value)
    assert configured_token not in rendered
    assert "presented-wrong-token" not in rendered


@pytest.mark.parametrize(
    "case",
    ["base64", "length", "padding", "json", "utf8", "duplicate-json", "outer-extra"],
)
def test_feishu_rejects_bad_authenticated_ciphertext_without_leak(case: str) -> None:
    encrypt_key = "feishu-authenticated-encrypt-key"
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = b"feishu-test-iv!!"
    if case == "base64":
        encrypted = "not-valid-base64$$"
        body = json.dumps({"encrypt": encrypted}, separators=(",", ":")).encode()
    elif case == "length":
        encrypted = base64.b64encode(iv).decode("ascii")
        body = json.dumps({"encrypt": encrypted}, separators=(",", ":")).encode()
    elif case == "padding":
        cipher = AES.new(key, AES.MODE_CBC, iv=iv).encrypt(b"\x00" * 16)
        encrypted = base64.b64encode(iv + cipher).decode("ascii")
        body = json.dumps({"encrypt": encrypted}, separators=(",", ":")).encode()
    else:
        valid_body, _ = _feishu_encrypted_body(
            encrypt_key,
            {},
            plaintext={
                "json": b"not-json",
                "utf8": b'\xff{"token":"x"}',
                "duplicate-json": b'{"token":"x","Token":"x"}',
            }.get(case),
        )
        if case == "outer-extra":
            encrypted = json.loads(valid_body)["encrypt"]
            body = json.dumps(
                {"encrypt": encrypted, "extra": "must-reject"},
                separators=(",", ":"),
            ).encode()
        else:
            encrypted = json.loads(valid_body)["encrypt"]
            body = valid_body
    envelope = _feishu_envelope(body=body, secret=encrypt_key)

    with pytest.raises(WebhookVerificationError) as caught:
        _registry(
            FeishuWebhookVerifier(encrypt_key=encrypt_key, verify_only=False)
        ).verify(envelope)
    assert caught.value.reason_code == GW_BODY_INVALID
    rendered = str(caught.value)
    assert encrypt_key not in rendered
    assert encrypted not in rendered


def test_wecom_verifies_signed_encrypt_field_without_exposing_it() -> None:
    token = "wecom-test-token"
    nonce = "nonce-wecom-1"
    encrypted = "BASE64_ENCRYPTED_CALLBACK_VALUE"
    body = f"<xml><ToUserName>corp</ToUserName><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>".encode()
    signature = _wecom_signature(token, NOW_S, nonce, encrypted)
    envelope = RawWebhookEnvelope(
        platform="wecom",
        method="POST",
        path="/v1/gateway/wecom/webhook",
        raw_body=body,
        query=(
            ("msg_signature", signature),
            ("timestamp", NOW_S),
            ("nonce", nonce),
        ),
        received_at_ms=NOW_MS,
    )

    result = _registry(
        WeComWebhookVerifier(token, verify_only=True)
    ).verify(envelope)

    assert result.claims["timestamp_s"] == int(NOW_S)
    assert result.claims["nonce"] == nonce
    assert result.claims["encrypted_body_sha256"] == hashlib.sha256(encrypted.encode()).hexdigest()
    assert encrypted not in repr(result)


def test_wecom_accepts_strict_json_envelope() -> None:
    token = "wecom-test-token"
    nonce = "nonce-wecom-json"
    encrypted = "JSON_ENCRYPTED_VALUE"
    body = b'{"Encrypt":"JSON_ENCRYPTED_VALUE"}'
    envelope = RawWebhookEnvelope(
        platform="wecom",
        method="POST",
        path="/v1/gateway/wecom/webhook",
        raw_body=body,
        query={
            "msg_signature": _wecom_signature(token, NOW_S, nonce, encrypted),
            "timestamp": NOW_S,
            "nonce": nonce,
        },
    )
    assert (
        _registry(WeComWebhookVerifier(token, verify_only=True)).verify(envelope).platform
        == "wecom"
    )


def test_wecom_authenticated_decrypt_validates_corp_and_canonicalizes_xml() -> None:
    token = "wecom-test-token"
    corp_id = "ww-corp-test"
    encoding_key, aes_key = _wecom_encoding_key()
    nonce = "nonce-wecom-auth"
    message = (
        b"<xml><ToUserName><![CDATA[ww-corp-test]]></ToUserName>"
        b"<FromUserName><![CDATA[user-1]]></FromUserName>"
        b"<CreateTime>1750000000</CreateTime><MsgType><![CDATA[text]]></MsgType>"
        b"<Content><![CDATA[hello]]></Content><MsgId>msg-1</MsgId></xml>"
    )
    encrypted = _wecom_encrypt(aes_key, corp_id, message)
    body = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>".encode()
    envelope = RawWebhookEnvelope(
        platform="wecom",
        method="POST",
        path="/v1/gateway/wecom/webhook",
        raw_body=body,
        query={
            "msg_signature": _wecom_signature(token, NOW_S, nonce, encrypted),
            "timestamp": NOW_S,
            "nonce": nonce,
        },
    )

    result = _registry(
        WeComWebhookVerifier(
            token=token,
            encoding_aes_key=encoding_key,
            corp_id=corp_id,
        )
    ).verify(envelope)

    assert json.loads(result.payload_bytes) == {
        "Content": "hello",
        "CreateTime": "1750000000",
        "FromUserName": "user-1",
        "MsgId": "msg-1",
        "MsgType": "text",
        "ToUserName": corp_id,
    }
    assert result.payload_bytes == json.dumps(
        json.loads(result.payload_bytes),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert result.body_sha256 == hashlib.sha256(body).hexdigest()
    assert result.claims["authenticated_decrypt"] is True
    assert result.claims["receive_id_verified"] is True
    assert encrypted not in repr(result)


def test_wecom_get_url_verification_decrypts_echo_and_checks_time_window() -> None:
    token = "wecom-test-token"
    corp_id = "ww-corp-test"
    encoding_key, aes_key = _wecom_encoding_key()
    nonce = "nonce-wecom-get"
    echo = b"verified-echo-value"
    encrypted = _wecom_encrypt(aes_key, corp_id, echo)
    envelope = RawWebhookEnvelope(
        platform="wecom",
        method="GET",
        path="/v1/gateway/wecom/webhook",
        raw_body=b"",
        query={
            "msg_signature": _wecom_signature(token, NOW_S, nonce, encrypted),
            "timestamp": NOW_S,
            "nonce": nonce,
            "echostr": encrypted,
        },
    )

    result = _registry(
        WeComWebhookVerifier(
            token=token,
            encoding_aes_key=encoding_key,
            corp_id=corp_id,
            verify_only=False,
        )
    ).verify(envelope)

    assert result.payload_bytes == echo
    assert result.body_sha256 == hashlib.sha256(b"").hexdigest()
    assert result.claims["url_verification"] is True
    assert result.claims["receive_id_verified"] is True

    stale_timestamp = str(int(NOW_S) - 301)
    stale = RawWebhookEnvelope(
        platform="wecom",
        method="GET",
        path="/v1/gateway/wecom/webhook",
        raw_body=b"",
        query={
            "msg_signature": _wecom_signature(
                token,
                stale_timestamp,
                nonce,
                encrypted,
            ),
            "timestamp": stale_timestamp,
            "nonce": nonce,
            "echostr": encrypted,
        },
    )
    with pytest.raises(WebhookVerificationError) as caught:
        _registry(
            WeComWebhookVerifier(
                token=token,
                encoding_aes_key=encoding_key,
                corp_id=corp_id,
                verify_only=False,
            )
        ).verify(stale)
    assert caught.value.reason_code == GW_TIMESTAMP_STALE


def test_wecom_decrypt_mode_requires_encoding_key_and_corp_id() -> None:
    token = "wecom-test-token"
    nonce = "nonce-wecom-required"
    encrypted = "signed-but-not-decrypted"
    body = f"<xml><Encrypt>{encrypted}</Encrypt></xml>".encode()
    envelope = RawWebhookEnvelope(
        platform="wecom",
        method="POST",
        path="/v1/gateway/wecom/webhook",
        raw_body=body,
        query={
            "msg_signature": _wecom_signature(token, NOW_S, nonce, encrypted),
            "timestamp": NOW_S,
            "nonce": nonce,
        },
    )
    encoding_key, _aes_key = _wecom_encoding_key()

    with pytest.raises(WebhookVerificationError) as missing_key:
        _registry(
            WeComWebhookVerifier(
                token=token,
                corp_id="ww-corp",
                verify_only=False,
            )
        ).verify(envelope)
    assert missing_key.value.reason_code == GW_SECRET_MISSING

    with pytest.raises(WebhookVerificationError) as missing_corp:
        _registry(
            WeComWebhookVerifier(
                token=token,
                encoding_aes_key=encoding_key,
                verify_only=False,
            )
        ).verify(envelope)
    assert missing_corp.value.reason_code == GW_SECRET_MISSING


@pytest.mark.parametrize("case", ["corp", "padding", "duplicate-xml", "unsafe-xml"])
def test_wecom_rejects_bad_decrypted_envelope_without_leak(case: str) -> None:
    token = "wecom-test-token"
    corp_id = "ww-corp-test"
    encoding_key, aes_key = _wecom_encoding_key()
    nonce = f"nonce-wecom-{case}"
    if case == "duplicate-xml":
        message = b"<xml><MsgType>text</MsgType><MsgType>image</MsgType></xml>"
    elif case == "unsafe-xml":
        message = b'<!DOCTYPE xml [<!ENTITY xxe SYSTEM "file:///secret">]><xml><Content>&xxe;</Content></xml>'
    else:
        message = b"<xml><MsgType>text</MsgType><Content>hello</Content></xml>"
    encrypted = _wecom_encrypt(
        aes_key,
        "wrong-corp" if case == "corp" else corp_id,
        message,
        valid_padding=case != "padding",
    )
    body = f"<xml><Encrypt>{encrypted}</Encrypt></xml>".encode()
    envelope = RawWebhookEnvelope(
        platform="wecom",
        method="POST",
        path="/v1/gateway/wecom/webhook",
        raw_body=body,
        query={
            "msg_signature": _wecom_signature(token, NOW_S, nonce, encrypted),
            "timestamp": NOW_S,
            "nonce": nonce,
        },
    )

    with pytest.raises(WebhookVerificationError) as caught:
        _registry(
            WeComWebhookVerifier(
                token=token,
                encoding_aes_key=encoding_key,
                corp_id=corp_id,
                verify_only=False,
            )
        ).verify(envelope)
    assert caught.value.reason_code in {GW_BODY_INVALID, GW_SIGNATURE_INVALID}
    rendered = str(caught.value)
    assert encrypted not in rendered
    assert token not in rendered
    assert corp_id not in rendered


@pytest.mark.parametrize(
    "body",
    [
        b"<xml><Content>no encrypt</Content></xml>",
        b"<xml><Encrypt>a</Encrypt><Encrypt>b</Encrypt></xml>",
        b'{"Encrypt":"a","encrypt":"b"}',
        b'<!DOCTYPE xml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><xml><Encrypt>&xxe;</Encrypt></xml>',
    ],
)
def test_wecom_rejects_missing_ambiguous_or_unsafe_encrypt_body(body: bytes) -> None:
    envelope = RawWebhookEnvelope(
        platform="wecom",
        method="POST",
        path="/v1/gateway/wecom/webhook",
        raw_body=body,
        query=(
            ("msg_signature", "0" * 40),
            ("timestamp", NOW_S),
            ("nonce", "nonce"),
        ),
    )
    with pytest.raises(WebhookVerificationError) as caught:
        _registry(WeComWebhookVerifier("token")).verify(envelope)
    assert caught.value.reason_code == GW_BODY_INVALID


def test_wecom_missing_query_fails_closed_before_body_use() -> None:
    envelope = RawWebhookEnvelope(
        platform="wecom",
        method="POST",
        path="/v1/gateway/wecom/webhook",
        raw_body=b"<xml><Encrypt>value</Encrypt></xml>",
    )
    with pytest.raises(WebhookVerificationError) as caught:
        _registry(WeComWebhookVerifier("token")).verify(envelope)
    assert caught.value.reason_code == GW_QUERY_MISSING


@pytest.mark.parametrize("platform", ["qq", "wechat", "telegram", "internal_reply"])
def test_internal_hmac_profile_verifies_all_internal_surfaces(platform: str) -> None:
    secret = "internal-test-key"
    envelope = _internal_envelope(platform=platform, secret=secret)
    result = _registry(InternalHmacWebhookVerifier(platform, secret)).verify(envelope)

    assert result.platform == platform
    assert result.profile_id == "internal-hmac-sha256-v1"
    assert result.claims["timestamp_s"] == int(NOW_S)
    assert result.claims["nonce"] == "nonce-internal-1"
    assert result.replay_key.startswith(f"gw:{platform}:")


def test_internal_signature_builder_matches_independent_canonicalization() -> None:
    body = b'{"message":"hello"}'
    expected = _internal_signature(
        "key",
        platform="qq",
        method="POST",
        path="/v1/gateway/qq/webhook",
        timestamp=NOW_S,
        nonce="nonce",
        body=body,
    )
    actual = build_internal_signature(
        shared_secret="key",
        platform="qq",
        method="POST",
        path="/v1/gateway/qq/webhook",
        timestamp=NOW_S,
        nonce="nonce",
        raw_body=body,
    )
    assert actual == expected


def test_internal_hmac_binds_method_path_platform_and_body() -> None:
    envelope = _internal_envelope(platform="qq")
    changed_path = RawWebhookEnvelope(
        platform="qq",
        method="POST",
        path="/v1/gateway/telegram/webhook",
        raw_body=envelope.raw_body,
        headers=envelope.headers,
    )
    with pytest.raises(WebhookVerificationError) as caught:
        _registry(InternalHmacWebhookVerifier("qq", "internal-test-key")).verify(changed_path)
    assert caught.value.reason_code == GW_SIGNATURE_INVALID


def test_internal_hmac_missing_secret_and_bad_signature_fail_closed() -> None:
    envelope = _internal_envelope(platform="qq", signature="0" * 64)
    with pytest.raises(WebhookVerificationError) as missing:
        _registry(InternalHmacWebhookVerifier("qq")).verify(envelope)
    assert missing.value.reason_code == GW_SECRET_MISSING

    with pytest.raises(WebhookVerificationError) as invalid:
        _registry(InternalHmacWebhookVerifier("qq", "internal-test-key")).verify(envelope)
    assert invalid.value.reason_code == GW_SIGNATURE_INVALID


def test_duplicate_headers_are_rejected_case_insensitively() -> None:
    body = b"{}"
    signature = _line_signature("secret", body)
    envelope = RawWebhookEnvelope(
        platform="line",
        method="POST",
        path="/v1/gateway/line/webhook",
        raw_body=body,
        headers=(("X-Line-Signature", signature), ("x-line-signature", signature)),
    )
    with pytest.raises(WebhookVerificationError) as caught:
        _registry(LineWebhookVerifier("secret")).verify(envelope)
    assert caught.value.reason_code == GW_HEADER_AMBIGUOUS


def test_mapping_sequence_values_preserve_and_reject_duplicate_headers() -> None:
    envelope = RawWebhookEnvelope(
        platform="line",
        method="POST",
        path="/v1/gateway/line/webhook",
        raw_body=b"{}",
        headers={"X-Line-Signature": ["one", "two"]},
    )
    with pytest.raises(WebhookVerificationError) as caught:
        _registry(LineWebhookVerifier("secret")).verify(envelope)
    assert caught.value.reason_code == GW_HEADER_AMBIGUOUS


def test_duplicate_query_parameters_are_rejected_case_insensitively() -> None:
    envelope = RawWebhookEnvelope(
        platform="wecom",
        method="POST",
        path="/v1/gateway/wecom/webhook",
        raw_body=b"<xml><Encrypt>value</Encrypt></xml>",
        query=(
            ("msg_signature", "0" * 40),
            ("timestamp", NOW_S),
            ("Timestamp", NOW_S),
            ("nonce", "nonce"),
        ),
    )
    with pytest.raises(WebhookVerificationError) as caught:
        _registry(WeComWebhookVerifier("token")).verify(envelope)
    assert caught.value.reason_code == GW_QUERY_AMBIGUOUS


def test_replay_key_is_stable_and_changes_with_nonce() -> None:
    verifier = InternalHmacWebhookVerifier("qq", "internal-test-key")
    registry = _registry(verifier)
    first = registry.verify(_internal_envelope(nonce="nonce-a"))
    repeated = registry.verify(_internal_envelope(nonce="nonce-a"))
    second = registry.verify(_internal_envelope(nonce="nonce-b"))
    assert first.replay_key == repeated.replay_key
    assert first.replay_key != second.replay_key


def test_verified_claims_are_immutable() -> None:
    result = _registry(
        InternalHmacWebhookVerifier("qq", "internal-test-key")
    ).verify(_internal_envelope())
    with pytest.raises(TypeError):
        result.claims["nonce"] = "changed"  # type: ignore[index]


def test_profile_mismatch_is_rejected() -> None:
    envelope = _internal_envelope(platform="qq")
    with pytest.raises(WebhookVerificationError) as caught:
        InternalHmacWebhookVerifier("telegram", "internal-test-key").verify(
            envelope,
            now_ms=NOW_MS,
        )
    assert caught.value.reason_code == GW_PLATFORM_MISMATCH


def test_raw_and_verified_representations_redact_body_headers_and_secret() -> None:
    body = b"sensitive-body-value"
    secret = "super-secret-material"
    envelope = RawWebhookEnvelope(
        platform="line",
        method="POST",
        path="/v1/gateway/line/webhook",
        raw_body=body,
        headers=((LINE_SIGNATURE_HEADER, "presented-signature"),),
        query=(("token", "query-secret"),),
    )
    verifier = LineWebhookVerifier(secret)
    assert body.decode() not in repr(envelope)
    assert "presented-signature" not in repr(envelope)
    assert "query-secret" not in repr(envelope)
    assert secret not in repr(verifier)

    with pytest.raises(WebhookVerificationError) as caught:
        _registry(verifier).verify(envelope)
    rendered = str(caught.value)
    assert body.decode() not in rendered
    assert secret not in rendered
    assert "presented-signature" not in rendered


def test_registry_masks_unexpected_custom_verifier_errors() -> None:
    class BrokenVerifier:
        platform = "line"
        profile_id = "broken"

        def verify(self, envelope, *, now_ms=None):
            raise RuntimeError("sensitive-body super-secret-material")

    envelope = RawWebhookEnvelope(
        platform="line",
        method="POST",
        path="/v1/gateway/line/webhook",
        raw_body=b"sensitive-body",
    )
    with pytest.raises(WebhookVerificationError) as caught:
        _registry(BrokenVerifier()).verify(envelope)
    assert caught.value.reason_code == GW_VERIFICATION_FAILED
    assert "sensitive" not in str(caught.value)
    assert "secret" not in str(caught.value)


def test_registry_rejects_duplicate_profile_registration() -> None:
    registry = _registry(LineWebhookVerifier("one"))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(LineWebhookVerifier("two"))
