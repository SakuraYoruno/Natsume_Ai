from __future__ import annotations

from pathlib import Path

from host_brain.skills.frontend_registry import FrontendSkillProvider
from host_brain.skills.impl.frontend_executor import execute_frontend_skill
from host_brain.skills.registry import SkillRegistry


def test_frontend_route_audit_detects_unrouted_navigation_target(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "App.jsx").write_text(
        """
        import { Link, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
        export default function App() {
          const navigate = useNavigate()
          return (
            <>
              <Link to="/dashboard">Dashboard</Link>
              <Link to="/missing">Missing</Link>
              <button onClick={() => navigate('/settings/123')}>Open settings</button>
              <Routes>
                <Route path="/" element={<Navigate to="/dashboard" replace />} />
                <Route path="dashboard" element={<div>Dashboard</div>} />
                <Route path="settings/:id" element={<div>Settings</div>} />
                <Route path="old-home" element={<Navigate to="/dashboard" replace />} />
              </Routes>
            </>
          )
        }
        """,
        encoding="utf-8",
    )
    provider = FrontendSkillProvider()
    payload = provider.frontend_route_audit(
        {
            "app_root": str(tmp_path),
            "source_root": str(src),
            "max_files": 20,
        },
        {"request_id": "req-frontend-route"},
    )

    result = execute_frontend_skill(payload, payload["_context"])
    detail = result["detail"]

    assert result["status"] == "OK"
    assert "/dashboard" in {item["path"] for item in detail["routes"]}
    assert "/settings/:id" in {item["path"] for item in detail["routes"]}
    assert "/missing" in detail["dead_nav_targets"]
    assert "/settings/123" not in detail["dead_nav_targets"]
    assert any(item["code"] == "FRONTEND_NAV_TARGET_UNROUTED" for item in detail["issues"])


def test_frontend_component_value_audit_finds_repeated_labels_and_markers(tmp_path: Path) -> None:
    src = tmp_path / "src"
    pages = src / "pages"
    pages.mkdir(parents=True)
    (pages / "Dashboard.jsx").write_text(
        """
        export default function Dashboard() {
          return (
            <div>
              {/* TODO placeholder demo block */}
              <section className="card card-hover rounded-2xl rounded-xl shadow shadow-sm shadow-card p-4">
                <h2>Status</h2>
                <p>Status</p>
                <button title="Status">Status</button>
              </section>
            </div>
          )
        }
        """,
        encoding="utf-8",
    )
    provider = FrontendSkillProvider()
    payload = provider.frontend_component_value_audit(
        {
            "source_root": str(src),
            "min_repeated_label_count": 2,
            "max_card_surfaces_per_file": 5,
            "max_files": 20,
        },
        {"request_id": "req-frontend-components"},
    )

    result = execute_frontend_skill(payload, payload["_context"])
    detail = result["detail"]

    assert result["status"] == "OK"
    assert detail["component_count"] == 1
    assert any(item["text"] == "Status" for item in detail["duplicate_labels"])
    assert any(item["path"] == "pages/Dashboard.jsx" for item in detail["low_value_markers"])
    assert any(item["code"] == "FRONTEND_DENSE_CARD_FILE" for item in detail["issues"])
    assert any(item["code"] == "FRONTEND_LOW_VALUE_MARKER" for item in detail["issues"])


def test_frontend_registry_exposes_low_risk_audit_skills() -> None:
    registry = SkillRegistry()
    registry.register_decorated(FrontendSkillProvider())

    route_meta = registry.require("frontend_route_audit")
    component_meta = registry.require("frontend_component_value_audit")

    assert route_meta.risk_level == 1
    assert route_meta.require_confirm is False
    assert route_meta.family == "code"
    assert "frontend" in route_meta.capability_tags
    assert component_meta.risk_level == 1
    assert component_meta.require_confirm is False
    assert component_meta.family == "code"
    assert "ux-audit" in component_meta.capability_tags
