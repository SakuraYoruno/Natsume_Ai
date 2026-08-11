"""历史 Bond 投影 REST API 与旧写入口退役测试。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.bond_routes import create_bond_router
from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.bond.bond_stage_engine import STAGE_FRIEND, BondStageEngine
from host_brain.bond.user_identity import get_canonical_bond_user_id
from host_brain.composition.host_bond_runtime import RetiredBondReadProjection


@pytest.fixture
def engine() -> BondStageEngine:
    return BondStageEngine()


@pytest.fixture
def client(engine: BondStageEngine) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_bond_router(
            HostRoutePorts(bond_engine_provider=lambda: engine),
        ),
    )
    return TestClient(app)


class TestHistoricalBondReadProjection:
    def test_health_and_empty_projection_are_available(self, client: TestClient) -> None:
        health = client.get("/bond/health")
        users = client.get("/bond/users")

        assert health.status_code == 200
        assert health.json()["status"] == "OK"
        assert users.status_code == 200
        assert users.json()["users"] == []
        assert users.json()["total"] == 0

    def test_new_user_read_is_historical_baseline(self, client: TestClient) -> None:
        response = client.get("/bond/stage/alice")

        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == get_canonical_bond_user_id()
        assert data["current_stage"] == STAGE_FRIEND
        assert data["total_score"] == 0
        assert data["event_count"] == 0

    def test_summary_is_read_only_and_hides_score(self, client: TestClient) -> None:
        response = client.get("/bond/summary/carol")

        assert response.status_code == 200
        data = response.json()
        assert {"stage", "trend", "capability_hint"} <= set(data)
        assert "total_score" not in data


def test_canonical_host_retires_legacy_bond_reads_before_init_mutation() -> None:
    app = FastAPI()
    retired = RetiredBondReadProjection()
    app.include_router(
        create_bond_router(
            HostRoutePorts(bond_engine_provider=lambda: retired),
        ),
    )

    with TestClient(app) as client:
        for path in ("/bond/health", "/bond/users", "/bond/stage/alice", "/bond/summary/alice"):
            response = client.get(path)
            assert response.status_code == 410
            assert response.headers["x-reason-code"] == "LEGACY_BOND_READ_RETIRED"
            assert response.json()["reason_code"] == "LEGACY_BOND_READ_RETIRED"

    assert retired.__dict__ == {}


class TestRetiredBondWriteRoute:
    @staticmethod
    def _legacy_payload(**overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "user_id": "legacy-user",
            "update_kind": "DEEP_TALK",
            "evidence_refs": ["evidence://legacy/turn-1"],
            "emotion_tag": "JOY",
            "emotion_intensity": 0.9,
        }
        payload.update(overrides)
        return payload

    def test_write_is_gone_without_resolving_a_writer(self, client: TestClient) -> None:
        response = client.post("/bond/event", json=self._legacy_payload())

        assert response.status_code == 410
        assert response.headers["x-reason-code"] == "LEGACY_BOND_WRITE_RETIRED"
        assert response.json() == {
            "status": "RETIRED",
            "reason_code": "LEGACY_BOND_WRITE_RETIRED",
            "detail": (
                "Legacy Bond score writes are retired; relationship changes are "
                "owned by canonical Digital Life lifecycle."
            ),
            "request_id": response.json()["request_id"],
            "timestamp": response.json()["timestamp"],
        }

    def test_retired_write_cannot_mutate_historical_projection(
        self,
        client: TestClient,
    ) -> None:
        before = client.get("/bond/stage/legacy-user").json()
        response = client.post("/bond/event", json=self._legacy_payload())
        after = client.get("/bond/stage/legacy-user").json()

        assert response.status_code == 410
        assert after["current_stage"] == before["current_stage"] == STAGE_FRIEND
        assert after["total_score"] == before["total_score"] == 0
        assert after["event_count"] == before["event_count"] == 0

    def test_retirement_does_not_depend_on_legacy_evidence_validation(
        self,
        client: TestClient,
    ) -> None:
        response = client.post(
            "/bond/event",
            json=self._legacy_payload(evidence_refs=[]),
        )

        assert response.status_code == 410
        assert response.json()["reason_code"] == "LEGACY_BOND_WRITE_RETIRED"

    def test_retired_response_keeps_request_id_for_old_clients(
        self,
        client: TestClient,
    ) -> None:
        response = client.post(
            "/bond/event",
            json=self._legacy_payload(request_id="legacy-request-001"),
        )

        assert response.status_code == 410
        assert response.json()["request_id"] == "legacy-request-001"

    def test_request_shape_still_rejects_invalid_intensity(self, client: TestClient) -> None:
        response = client.post(
            "/bond/event",
            json=self._legacy_payload(emotion_intensity=1.1),
        )

        assert response.status_code == 422


def test_route_ports_expose_no_bond_writer_provider() -> None:
    assert "bond_writer_provider" not in HostRoutePorts.__dataclass_fields__
