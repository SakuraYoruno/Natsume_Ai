"""Frontend audit executor."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Iterable


STATUS_OK = "OK"
STATUS_FAILED = "FAILED"
_SOURCE_SUFFIXES = frozenset({".js", ".jsx", ".ts", ".tsx"})
_COMPONENT_SUFFIXES = frozenset({".jsx", ".tsx"})
_IGNORED_DIRS = frozenset({"node_modules", "dist", "build", ".git", ".vite", "coverage"})

_ROUTE_JSX_RE = re.compile(r"<Route\b(?P<body>[^>]*)>", re.IGNORECASE | re.DOTALL)
_ROUTE_PATH_RE = re.compile(r"\bpath\s*=\s*[\"'](?P<path>[^\"']+)[\"']", re.IGNORECASE)
_OBJECT_PATH_RE = re.compile(r"\bpath\s*:\s*[\"'](?P<path>/?[^\"']+)[\"']", re.IGNORECASE)
_NAV_ATTR_RE = re.compile(r"\b(?:to|href)\s*=\s*[\"'](?P<path>#?/[^\"']*)[\"']", re.IGNORECASE)
_NAV_OBJECT_RE = re.compile(r"\b(?:to|href)\s*:\s*[\"'](?P<path>#?/[^\"']*)[\"']", re.IGNORECASE)
_NAVIGATE_RE = re.compile(r"\bnavigate\(\s*[`\"'](?P<path>/[^`\"')${}]+)", re.IGNORECASE)
_LOADER_KEY_RE = re.compile(r"[\"'](?P<path>/[^\"']+)[\"']\s*:\s*\(\)\s*=>\s*import\(", re.IGNORECASE)
_TEXT_NODE_RE = re.compile(r">([^<>{}\n][^<>{}\n]{1,64})<")
_PROP_LABEL_RE = re.compile(r"\b(?:title|label|hint|emptyLabel|description)\s*=\s*[\"']([^\"']{1,80})[\"']", re.IGNORECASE)
_COMPONENT_FUNC_RE = re.compile(
    r"\b(?:export\s+default\s+)?function\s+([A-Z][A-Za-z0-9_]*)\s*\("
    r"|\b(?:export\s+)?const\s+([A-Z][A-Za-z0-9_]*)\s*=",
)
_LOW_VALUE_MARKER_RE = re.compile(
    r"\b(?:todo|fixme|lorem|placeholder|coming soon|demo|mock|stub|fake)\b|(?:占位|示例|样例|待接入|模拟数据)",
    re.IGNORECASE,
)
_CARD_SURFACE_RE = re.compile(r"\b(?:card|card-hover|rounded-2xl|rounded-xl|shadow(?:-[a-z0-9]+)?)\b")
_CLASS_CARD_RE = re.compile(r"className\s*=\s*([\"'`])(?P<class>[^\"'`]*(?:card|rounded-|shadow)[^\"'`]*)\1", re.IGNORECASE)


@dataclass(frozen=True)
class _TextHit:
    path: str
    line: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line, "text": self.text}


def execute_frontend_skill(payload: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    _ = context
    action = str((payload or {}).get("action") or "").strip().lower()
    manifest_ref = str((payload or {}).get("manifest_ref") or f"frontend::{action}")
    try:
        if action == "route_audit":
            detail = _execute_route_audit(payload or {})
        elif action == "component_value_audit":
            detail = _execute_component_value_audit(payload or {})
        else:
            detail = {"issue": "unknown frontend action", "action": action}
        return {
            "status": STATUS_OK,
            "manifest_ref": manifest_ref,
            "action": action,
            "detail": detail,
        }
    except Exception as exc:
        return {
            "status": STATUS_FAILED,
            "manifest_ref": manifest_ref,
            "action": action,
            "error": str(exc),
        }


def _iter_source_files(root: Path, *, max_files: int, suffixes: Iterable[str] = _SOURCE_SUFFIXES) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    suffix_set = frozenset(suffixes)
    files: list[Path] = []
    for path in root.rglob("*"):
        if len(files) >= max_files:
            break
        if any(part in _IGNORED_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in suffix_set:
            files.append(path)
    return sorted(files)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _normalize_route_path(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if value.startswith("#"):
        value = value[1:]
    if not value.startswith("/"):
        value = f"/{value}"
    value = value.split("?", 1)[0].split("#", 1)[0]
    value = re.sub(r"/+", "/", value)
    if len(value) > 1:
        value = value.rstrip("/")
    return value or "/"


def _route_matches_target(route: str, target: str) -> bool:
    route = _normalize_route_path(route)
    target = _normalize_route_path(target)
    if not route or not target:
        return False
    if route == target:
        return True
    if route.endswith("/*"):
        prefix = route[:-2].rstrip("/")
        return target == prefix or target.startswith(f"{prefix}/")
    route_parts = [part for part in route.strip("/").split("/") if part]
    target_parts = [part for part in target.strip("/").split("/") if part]
    if len(route_parts) != len(target_parts):
        return False
    for route_part, target_part in zip(route_parts, target_parts, strict=False):
        if route_part.startswith(":"):
            continue
        if route_part != target_part:
            return False
    return True


def _path_is_dynamic(path: str) -> bool:
    return ":" in path or "*" in path or "${" in path


def _extract_route_entries(files: list[Path], source_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in files:
        text = _read_text(path)
        rel_path = _rel(path, source_root)
        for match in _ROUTE_JSX_RE.finditer(text):
            body = match.group("body") or ""
            path_match = _ROUTE_PATH_RE.search(body)
            if not path_match:
                continue
            raw_path = str(path_match.group("path") or "").strip()
            route_path = _normalize_route_path(raw_path)
            is_relative = bool(raw_path and not raw_path.startswith("/"))
            entries.append(
                {
                    "path": route_path,
                    "raw_path": raw_path,
                    "file": rel_path,
                    "line": _line_for_offset(text, match.start()),
                    "kind": "route",
                    "redirect": "Navigate" in body,
                    "relative": is_relative,
                    "local_subroute": is_relative and Path(rel_path).name.lower() not in {"app.jsx", "app.tsx", "main.jsx", "main.tsx"},
                }
            )
        if "route" in rel_path.lower() or "router" in rel_path.lower():
            for match in _OBJECT_PATH_RE.finditer(text):
                raw_path = str(match.group("path") or "").strip()
                route_path = _normalize_route_path(raw_path)
                if not route_path:
                    continue
                entries.append(
                    {
                        "path": route_path,
                        "raw_path": raw_path,
                        "file": rel_path,
                        "line": _line_for_offset(text, match.start()),
                        "kind": "object_path",
                        "redirect": False,
                        "relative": bool(raw_path and not raw_path.startswith("/")),
                        "local_subroute": False,
                    }
                )
    return _dedupe_entries(entries)


def _extract_navigation_targets(files: list[Path], source_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    patterns = (_NAV_ATTR_RE, _NAV_OBJECT_RE, _NAVIGATE_RE)
    for path in files:
        text = _read_text(path)
        rel_path = _rel(path, source_root)
        for pattern in patterns:
            for match in pattern.finditer(text):
                nav_path = _normalize_route_path(match.group("path"))
                if not nav_path:
                    continue
                entries.append(
                    {
                        "path": nav_path,
                        "file": rel_path,
                        "line": _line_for_offset(text, match.start()),
                        "kind": "navigation",
                    }
                )
    return _dedupe_entries(entries)


def _extract_lazy_loaders(files: list[Path], source_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in files:
        text = _read_text(path)
        rel_path = _rel(path, source_root)
        for match in _LOADER_KEY_RE.finditer(text):
            loader_path = _normalize_route_path(match.group("path"))
            if not loader_path:
                continue
            entries.append(
                {
                    "path": loader_path,
                    "file": rel_path,
                    "line": _line_for_offset(text, match.start()),
                    "kind": "lazy_loader",
                }
            )
    return _dedupe_entries(entries)


def _dedupe_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, int, str]] = set()
    result: list[dict[str, Any]] = []
    for entry in entries:
        key = (
            str(entry.get("path") or ""),
            str(entry.get("file") or ""),
            int(entry.get("line") or 0),
            str(entry.get("kind") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def _execute_route_audit(payload: dict[str, Any]) -> dict[str, Any]:
    app_root = Path(str(payload.get("app_root") or Path.cwd())).expanduser().resolve()
    source_root = Path(str(payload.get("source_root") or app_root / "src")).expanduser().resolve()
    max_files = int(payload.get("max_files") or 600)
    issues: list[dict[str, Any]] = []

    if not source_root.exists() or not source_root.is_dir():
        return {
            "audit_status": "FAILED",
            "app_root": str(app_root),
            "source_root": str(source_root),
            "issues": [{"code": "FRONTEND_SOURCE_ROOT_MISSING", "severity": "error", "message": "source root does not exist"}],
        }

    files = _iter_source_files(source_root, max_files=max_files)
    route_entries = _extract_route_entries(files, source_root)
    nav_entries = _extract_navigation_targets(files, source_root)
    loader_entries = _extract_lazy_loaders(files, source_root)
    global_route_entries = [entry for entry in route_entries if not entry.get("local_subroute")]
    local_route_entries = [entry for entry in route_entries if entry.get("local_subroute")]
    route_paths = sorted({entry["path"] for entry in global_route_entries})
    nav_paths = sorted({entry["path"] for entry in nav_entries})
    loader_paths = sorted({entry["path"] for entry in loader_entries})
    redirect_routes = sorted({entry["path"] for entry in route_entries if entry.get("redirect")})

    dead_nav_targets = [
        path
        for path in nav_paths
        if not any(_route_matches_target(route_path, path) for route_path in route_paths)
    ]
    for path in dead_nav_targets[:20]:
        issues.append(
            {
                "code": "FRONTEND_NAV_TARGET_UNROUTED",
                "severity": "warning",
                "path": path,
                "message": "navigation target does not match a declared route",
            }
        )

    unlinked_routes = [
        path
        for path in route_paths
        if path not in {"/", *redirect_routes}
        and not _path_is_dynamic(path)
        and not any(_route_matches_target(path, nav_path) for nav_path in nav_paths)
    ]
    for path in unlinked_routes[:20]:
        issues.append(
            {
                "code": "FRONTEND_ROUTE_WITHOUT_NAV_ENTRY",
                "severity": "info",
                "path": path,
                "message": "declared route has no static navigation entry",
            }
        )

    loader_missing_routes = [
        path
        for path in route_paths
        if path not in {"/", *redirect_routes}
        and not _path_is_dynamic(path)
        and not any(_route_matches_target(loader_path, path) for loader_path in loader_paths)
    ]
    for path in loader_missing_routes[:20]:
        issues.append(
            {
                "code": "FRONTEND_ROUTE_WITHOUT_LAZY_LOADER",
                "severity": "info",
                "path": path,
                "message": "declared static route has no matching lazy loader key",
            }
        )

    severity_rank = {"error": 3, "warning": 2, "info": 1}
    max_severity = max((severity_rank.get(str(item.get("severity") or ""), 0) for item in issues), default=0)
    return {
        "audit_status": "FAIL" if max_severity >= 3 else "CHECK" if max_severity >= 1 else "PASS",
        "app_root": str(app_root),
        "source_root": str(source_root),
        "scanned_file_count": len(files),
        "route_count": len(route_paths),
        "navigation_target_count": len(nav_paths),
        "lazy_loader_count": len(loader_paths),
        "routes": route_entries,
        "global_routes": global_route_entries,
        "local_subroutes": local_route_entries,
        "navigation_targets": nav_entries[:200],
        "lazy_loaders": loader_entries,
        "dead_nav_targets": dead_nav_targets,
        "unlinked_routes": unlinked_routes,
        "loader_missing_routes": loader_missing_routes,
        "lazy_loader_only_paths": [path for path in loader_paths if not any(_route_matches_target(route, path) for route in route_paths)],
        "issues": issues,
    }


def _extract_components(text: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for match in _COMPONENT_FUNC_RE.finditer(text):
        name = next((group for group in match.groups() if group), "")
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _clean_visible_text(raw: str) -> str:
    value = re.sub(r"\s+", " ", str(raw or "")).strip(" \t\r\n")
    if not value:
        return ""
    if value in {"-", "n/a", "N/A", "unknown"}:
        return ""
    if not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", value):
        return ""
    if len(value) > 80:
        return ""
    return value


def _extract_visible_labels(text: str, path: str) -> list[_TextHit]:
    hits: list[_TextHit] = []
    for pattern in (_TEXT_NODE_RE, _PROP_LABEL_RE):
        for match in pattern.finditer(text):
            value = _clean_visible_text(match.group(1))
            if not value:
                continue
            hits.append(_TextHit(path=path, line=_line_for_offset(text, match.start()), text=value))
    return hits


def _line_hits_for_regex(text: str, pattern: re.Pattern[str], *, limit: int = 20) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for match in pattern.finditer(text):
        hits.append({"line": _line_for_offset(text, match.start()), "text": match.group(0)[:120]})
        if len(hits) >= limit:
            break
    return hits


def _is_low_value_false_positive(line: str, marker: str) -> bool:
    normalized = line.strip().lower()
    marker_text = marker.lower()
    if marker_text == "placeholder" and re.search(r"\bplaceholder\s*=", normalized):
        return True
    if marker_text == "placeholder" and re.search(r"\bplaceholder\s*[,:]", normalized):
        return True
    if marker_text == "mock" and (
        "mock-fallback" in normalized
        or "use_mock" in normalized
        or "ismock" in normalized
        or "mock_doc_count" in normalized
    ):
        return True
    return False


def _low_value_marker_hits(text: str, *, limit: int = 20) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    lines = text.splitlines()
    for match in _LOW_VALUE_MARKER_RE.finditer(text):
        line_number = _line_for_offset(text, match.start())
        line = lines[line_number - 1] if 0 <= line_number - 1 < len(lines) else ""
        marker = match.group(0)[:120]
        if _is_low_value_false_positive(line, marker):
            continue
        hits.append({"line": line_number, "text": marker})
        if len(hits) >= limit:
            break
    return hits


def _execute_component_value_audit(payload: dict[str, Any]) -> dict[str, Any]:
    source_root = Path(str(payload.get("source_root") or Path.cwd())).expanduser().resolve()
    max_files = int(payload.get("max_files") or 800)
    min_repeated = int(payload.get("min_repeated_label_count") or 5)
    max_card_surfaces = int(payload.get("max_card_surfaces_per_file") or 35)
    issues: list[dict[str, Any]] = []

    if not source_root.exists() or not source_root.is_dir():
        return {
            "audit_status": "FAILED",
            "source_root": str(source_root),
            "issues": [{"code": "FRONTEND_SOURCE_ROOT_MISSING", "severity": "error", "message": "source root does not exist"}],
        }

    files = _iter_source_files(source_root, max_files=max_files, suffixes=_SOURCE_SUFFIXES)
    component_files: list[dict[str, Any]] = []
    all_labels: list[_TextHit] = []
    component_count = 0
    card_surface_total = 0
    marker_hits: list[dict[str, Any]] = []

    for path in files:
        rel_path = _rel(path, source_root)
        text = _read_text(path)
        line_count = text.count("\n") + 1
        is_component_source = path.suffix.lower() in _COMPONENT_SUFFIXES
        components = _extract_components(text) if is_component_source else []
        labels = _extract_visible_labels(text, rel_path) if is_component_source else []
        card_surface_count = len(_CARD_SURFACE_RE.findall(text)) if is_component_source else 0
        class_card_count = len(_CLASS_CARD_RE.findall(text)) if is_component_source else 0
        empty_state_count = len(re.findall(r"\bEmptyStateCard\b", text)) if is_component_source else 0
        notice_count = len(re.findall(r"\bNoticeBanner\b", text)) if is_component_source else 0
        metric_count = len(re.findall(r"\bMetricCard\b", text)) if is_component_source else 0
        low_markers = _low_value_marker_hits(text, limit=12)

        if components or labels or card_surface_count or low_markers:
            component_files.append(
                {
                    "path": rel_path,
                    "is_component_source": is_component_source,
                    "line_count": line_count,
                    "components": components[:40],
                    "component_count": len(components),
                    "visible_label_count": len(labels),
                    "card_surface_count": card_surface_count,
                    "class_card_count": class_card_count,
                    "empty_state_count": empty_state_count,
                    "notice_banner_count": notice_count,
                    "metric_card_count": metric_count,
                    "low_value_marker_count": len(low_markers),
                }
            )

        component_count += len(components)
        card_surface_total += card_surface_count
        all_labels.extend(labels)
        for hit in low_markers:
            marker_hits.append({"path": rel_path, **hit})

        if is_component_source and card_surface_count > max_card_surfaces:
            issues.append(
                {
                    "code": "FRONTEND_DENSE_CARD_FILE",
                    "severity": "info",
                    "path": rel_path,
                    "count": card_surface_count,
                    "message": "file has many card-like surfaces and may benefit from consolidation",
                }
            )
        if is_component_source and line_count > 900 and card_surface_count > 20:
            issues.append(
                {
                    "code": "FRONTEND_HEAVY_PAGE_FILE",
                    "severity": "info",
                    "path": rel_path,
                    "line_count": line_count,
                    "message": "large page file mixes many UI sections",
                }
            )
        if low_markers:
            issues.append(
                {
                    "code": "FRONTEND_LOW_VALUE_MARKER",
                    "severity": "warning",
                    "path": rel_path,
                    "count": len(low_markers),
                    "message": "placeholder/demo/mock marker found in frontend source",
                }
            )

    label_counter = Counter(hit.text for hit in all_labels)
    duplicate_labels: list[dict[str, Any]] = []
    label_hits_by_text: dict[str, list[_TextHit]] = {}
    for hit in all_labels:
        label_hits_by_text.setdefault(hit.text, []).append(hit)
    for label, count in label_counter.most_common(40):
        if count < min_repeated:
            continue
        duplicate_labels.append(
            {
                "text": label,
                "count": count,
                "examples": [hit.to_dict() for hit in label_hits_by_text[label][:6]],
            }
        )
        issues.append(
            {
                "code": "FRONTEND_REPEATED_VISIBLE_LABEL",
                "severity": "info",
                "text": label,
                "count": count,
                "message": "same visible label repeats across frontend files",
            }
        )

    recommendations = _build_component_recommendations(
        duplicate_labels=duplicate_labels,
        marker_hits=marker_hits,
        component_files=component_files,
    )
    severity_rank = {"error": 3, "warning": 2, "info": 1}
    max_severity = max((severity_rank.get(str(item.get("severity") or ""), 0) for item in issues), default=0)
    return {
        "audit_status": "FAIL" if max_severity >= 3 else "CHECK" if max_severity >= 1 else "PASS",
        "source_root": str(source_root),
        "scanned_file_count": len(files),
        "component_file_count": len(component_files),
        "component_count": component_count,
        "card_surface_total": card_surface_total,
        "duplicate_labels": duplicate_labels,
        "low_value_markers": marker_hits[:80],
        "dense_card_files": [
            item for item in component_files if int(item.get("card_surface_count") or 0) > max_card_surfaces
        ][:40],
        "heavy_files": [
            item for item in component_files if bool(item.get("is_component_source")) and int(item.get("line_count") or 0) > 900
        ][:40],
        "component_files": component_files[:200],
        "issues": issues[:160],
        "recommendations": recommendations,
    }


def _build_component_recommendations(
    *,
    duplicate_labels: list[dict[str, Any]],
    marker_hits: list[dict[str, Any]],
    component_files: list[dict[str, Any]],
) -> list[str]:
    recommendations: list[str] = []
    if marker_hits:
        recommendations.append("Replace placeholder/demo/mock UI copy with real state, empty-state, or diagnostic data contracts.")
    if duplicate_labels:
        recommendations.append("Review repeated visible labels and consolidate copy through shared components or constants where they represent the same concept.")
    dense = [
        item
        for item in component_files
        if bool(item.get("is_component_source")) and int(item.get("card_surface_count") or 0) > 35
    ]
    if dense:
        recommendations.append("Audit dense card files first; merge low-value cards into tabs, tables, or focused summary panels.")
    heavy = [
        item
        for item in component_files
        if bool(item.get("is_component_source")) and int(item.get("line_count") or 0) > 900
    ]
    if heavy:
        recommendations.append("Split heavy page files around user workflows so route-level pages stay easy to verify.")
    if not recommendations:
        recommendations.append("No obvious low-value component signal found in the static audit.")
    return recommendations
