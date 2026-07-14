from __future__ import annotations

import time

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.governor.tokens import TokenService
from host_brain.skills.loader import SkillLoader
from host_brain.skills.registry import SkillMeta, SkillRegistry


def _noop_handler(_args: dict, _context: dict | None) -> dict:
    return {"status": "prepared", "tool": "demo"}


def _make_loader() -> tuple[SkillLoader, TokenService]:
    registry = SkillRegistry()
    registry.register(
        SkillMeta(
            skill_name="demo_reversible",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            handler=_noop_handler,
        )
    )
    tokens = TokenService(approval_ttl_ms=20_000, confirm_ttl_ms=20_000, undo_ttl_ms=20_000)
    loader = SkillLoader(registry=registry, token_service=tokens, ledger=SQLiteLedger(db_path=":memory:"))
    return loader, tokens


def _make_intent() -> dict:
    return {
        "id": "intent-demo-001",
        "request_id": "req-demo-001",
        "session_id": "session-demo",
        "seq": 1,
        "action": "demo_reversible",
        "args": {},
    }


def _issue_approval(loader: SkillLoader, tokens: TokenService, intent: dict) -> str:
    token = tokens.issue_approval_token(
        intent_id=intent["id"],
        skill_name=intent["action"],
        session_id=intent["session_id"],
        seq=intent["seq"],
        now_ts_ms=1000,
    )
    now_value = int(time.time() * 1000)
    binding = build_governed_action_binding(
        intent_envelope={
            **intent,
            "target": intent["action"],
            "person_id": intent["session_id"],
            "principal_id": intent["session_id"],
        },
        approval_token_ref=token,
        risk_level=1,
        issued_at_ms=now_value,
        expires_at_ms=now_value + 20_000,
    )
    loader.ledger.log_governor(
        {
            "request_id": intent["request_id"],
            "intent_id": intent["id"],
            "decision": "APPROVE",
            "tokens": {"approval_token_ref": token},
            "approved_action_binding": binding,
            "ts_ms": now_value,
        }
    )
    return token


def test_loader_preserves_rejected_executor_result(monkeypatch) -> None:
    loader, tokens = _make_loader()
    intent = _make_intent()
    approval = _issue_approval(loader, tokens, intent)
    monkeypatch.setattr("host_brain.skills.loader._dispatch_executor", lambda _payload, **_kwargs: {"status": "REJECTED", "reason_code": "FILE_NOT_FOUND", "detail": "missing"})

    result = loader.execute(intent=intent, governor_output={"decision": "APPROVE", "tokens": {"approval_token_ref": approval}}, now_ts_ms=1000)

    assert result.status == "REJECTED"
    assert result.result_code == "FILE_NOT_FOUND"


def test_loader_preserves_degraded_executor_result(monkeypatch) -> None:
    loader, tokens = _make_loader()
    intent = _make_intent()
    approval = _issue_approval(loader, tokens, intent)
    monkeypatch.setattr("host_brain.skills.loader._dispatch_executor", lambda _payload, **_kwargs: {"status": "DEGRADED", "reason_code": "BROWSER_UNAVAILABLE", "detail": "playwright not installed"})

    result = loader.execute(intent=intent, governor_output={"decision": "APPROVE", "tokens": {"approval_token_ref": approval}}, now_ts_ms=1000)

    assert result.status == "FAILED"
    assert result.result_code == "BROWSER_UNAVAILABLE"
