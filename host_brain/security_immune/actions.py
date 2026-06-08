from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

from host_brain.security_immune.models import (
    ActionExecutionResult,
    ActionStatus,
    AssetSnapshot,
    IncidentRecord,
    ResponseAction,
    SecuritySignal,
    now_ms,
)
from host_brain.security_immune.policy import SecurityImmunePolicy


class SecurityResponseExecutor:
    """Execute only policy-approved, defensive response actions."""

    def __init__(self, policy: SecurityImmunePolicy) -> None:
        self.policy = policy

    def execute_incident(self, incident: IncidentRecord, asset: AssetSnapshot | None) -> IncidentRecord:
        updated_actions: list[ResponseAction] = []
        results: list[ActionExecutionResult] = []
        auto_action_ids = {
            action.action_id.strip().lower()
            for action in incident.recommended_actions
            if action.status == ActionStatus.AUTO_ALLOWED
        }
        handled_execution_keys: dict[str, str] = {}
        for action in incident.recommended_actions:
            result = self._execute_action(
                action,
                incident,
                asset,
                auto_action_ids=auto_action_ids,
                handled_execution_keys=handled_execution_keys,
            )
            results.append(result)
            if action.status == ActionStatus.AUTO_ALLOWED:
                updated_actions.append(replace(action, status=result.status))
            else:
                updated_actions.append(action)
        return replace(
            incident,
            recommended_actions=updated_actions,
            action_results=results,
            updated_ts_ms=now_ms(),
        )

    def _execute_action(
        self,
        action: ResponseAction,
        incident: IncidentRecord,
        asset: AssetSnapshot | None,
        *,
        auto_action_ids: set[str],
        handled_execution_keys: dict[str, str],
    ) -> ActionExecutionResult:
        started = now_ms()
        if action.status != ActionStatus.AUTO_ALLOWED:
            return self._result(
                action,
                action.status,
                f"Policy status is {action.status.value}; action was not auto-executed.",
                started,
            )
        action_id = action.action_id.strip().lower()
        execution_key = self._execution_key(action_id)
        if action_id == "quick_scan" and "run_defender_quick_scan" in auto_action_ids:
            return self._result(
                action,
                ActionStatus.SKIPPED,
                "Specific run_defender_quick_scan action will handle this scan.",
                started,
            )
        if execution_key in handled_execution_keys:
            return self._result(
                action,
                ActionStatus.SKIPPED,
                f"Equivalent action already handled by {handled_execution_keys[execution_key]}.",
                started,
            )
        try:
            if action_id in {"record", "collect_more_evidence", "review_defender_threat_history"}:
                result = self._write_evidence_record(action, incident, asset, started)
                handled_execution_keys[execution_key] = action.action_id
                return result
            if action_id in {"quick_scan", "run_defender_quick_scan"}:
                result = self._run_defender_quick_scan(action, started)
                if result.status == ActionStatus.EXECUTED:
                    handled_execution_keys[execution_key] = action.action_id
                return result
            if action_id == "update_defender_signatures":
                result = self._update_defender_signatures(action, started)
                if result.status == ActionStatus.EXECUTED:
                    handled_execution_keys[execution_key] = action.action_id
                return result
            if action_id == "verify_defender_remediation":
                result = self._verify_remediation(action, incident, started)
                handled_execution_keys[execution_key] = action.action_id
                return result
            if action_id == "isolate_file_copy":
                result = self._copy_files_to_quarantine(action, incident, started)
                if result.status == ActionStatus.EXECUTED:
                    handled_execution_keys[execution_key] = action.action_id
                return result
        except Exception as exc:
            return self._result(action, ActionStatus.SKIPPED, str(exc)[:300], started)
        return self._result(
            action,
            ActionStatus.SKIPPED,
            f"Unsupported auto action: {action.action_id}",
            started,
        )

    @staticmethod
    def _execution_key(action_id: str) -> str:
        if action_id in {"quick_scan", "run_defender_quick_scan"}:
            return "defender_quick_scan"
        return action_id

    def _write_evidence_record(
        self,
        action: ResponseAction,
        incident: IncidentRecord,
        asset: AssetSnapshot | None,
        started: int,
    ) -> ActionExecutionResult:
        evidence_dir = self.policy.state_dir / "action_evidence" / self._safe_name(incident.incident_id)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = evidence_dir / f"{self._safe_name(action.action_id)}-{started}.json"
        payload = {
            "action_id": action.action_id,
            "incident": incident.as_dict(),
            "asset": asset.as_dict() if asset is not None else None,
            "ts_ms": now_ms(),
        }
        evidence_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._result(
            action,
            ActionStatus.EXECUTED,
            "Evidence record written.",
            started,
            {"evidence_path": str(evidence_path)},
        )

    def _run_defender_quick_scan(self, action: ResponseAction, started: int) -> ActionExecutionResult:
        if not self.policy.allow_local_defender_scan:
            return self._result(
                action,
                ActionStatus.SKIPPED,
                "SECURITY_IMMUNE_ALLOW_LOCAL_DEFENDER_SCAN=false",
                started,
            )
        if platform.system().lower() != "windows":
            return self._result(action, ActionStatus.SKIPPED, "Defender quick scan is Windows-only.", started)
        command = "Start-MpScan -ScanType QuickScan -ErrorAction Stop; Write-Output '{\"status\":\"started\"}'"
        return self._run_powershell_action(action, command, started)

    def _update_defender_signatures(self, action: ResponseAction, started: int) -> ActionExecutionResult:
        if platform.system().lower() != "windows":
            return self._result(action, ActionStatus.SKIPPED, "Defender signature update is Windows-only.", started)
        command = "Update-MpSignature -ErrorAction Stop; Write-Output '{\"status\":\"updated\"}'"
        return self._run_powershell_action(action, command, started)

    def _run_powershell_action(self, action: ResponseAction, command: str, started: int) -> ActionExecutionResult:
        result = subprocess.run(  # noqa: S603
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=float(self.policy.response_action_timeout_seconds),
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "PowerShell action failed")[:300]
            return self._result(action, ActionStatus.SKIPPED, detail, started)
        return self._result(
            action,
            ActionStatus.EXECUTED,
            (result.stdout.strip() or "PowerShell action completed")[:300],
            started,
        )

    def _verify_remediation(
        self,
        action: ResponseAction,
        incident: IncidentRecord,
        started: int,
    ) -> ActionExecutionResult:
        paths = self._paths_from_signals(incident.signals)
        states = []
        for path in paths:
            try:
                exists = path.exists()
            except OSError:
                exists = None
            states.append({"path": str(path), "exists": exists})
        if not states:
            return self._result(action, ActionStatus.SKIPPED, "No local file resources found to verify.", started)
        present = [state for state in states if state.get("exists") is True]
        detail = "No suspicious file resources remain." if not present else "Some suspicious file resources still exist."
        return self._result(action, ActionStatus.EXECUTED, detail, started, {"resource_states": states})

    def _copy_files_to_quarantine(
        self,
        action: ResponseAction,
        incident: IncidentRecord,
        started: int,
    ) -> ActionExecutionResult:
        quarantine_dir = self.policy.state_dir / "quarantine" / self._safe_name(incident.incident_id)
        copied = []
        skipped = []
        for path in self._paths_from_signals(incident.signals):
            try:
                if not path.is_file():
                    skipped.append({"path": str(path), "reason": "not_found_or_not_file"})
                    continue
                size = path.stat().st_size
                if size > int(self.policy.max_quarantine_file_bytes):
                    skipped.append({"path": str(path), "reason": "too_large", "size": size})
                    continue
                digest = self._sha256_file(path)
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                destination = quarantine_dir / f"{digest[:16]}-{self._safe_name(path.name)}"
                shutil.copy2(path, destination)
                copied.append(
                    {
                        "source_path": str(path),
                        "quarantine_path": str(destination),
                        "sha256": digest,
                        "size": size,
                    }
                )
            except OSError as exc:
                skipped.append({"path": str(path), "reason": str(exc)[:160]})
        if not copied:
            return self._result(
                action,
                ActionStatus.SKIPPED,
                "No eligible suspicious file resources were copied.",
                started,
                {"copied": copied, "skipped": skipped},
            )
        return self._result(
            action,
            ActionStatus.EXECUTED,
            f"Copied {len(copied)} suspicious file(s) to quarantine evidence.",
            started,
            {"copied": copied, "skipped": skipped},
        )

    def _paths_from_signals(self, signals: list[SecuritySignal]) -> list[Path]:
        paths: list[Path] = []
        seen: set[str] = set()
        for signal in signals:
            for path in self._paths_from_evidence(signal.evidence):
                normalized = str(path)
                if normalized not in seen:
                    paths.append(path)
                    seen.add(normalized)
        return paths

    def _paths_from_evidence(self, evidence: dict[str, Any]) -> list[Path]:
        paths: list[Path] = []
        for state in evidence.get("resource_states") or []:
            if not isinstance(state, dict):
                continue
            path = state.get("path")
            if path:
                paths.append(Path(str(path)))
        for resource in self._as_resource_list(evidence.get("resources")):
            path = self._path_from_resource(resource)
            if path is not None:
                paths.append(path)
        return paths

    @staticmethod
    def _path_from_resource(resource: str) -> Path | None:
        text = str(resource or "").strip()
        lowered = text.lower()
        if lowered.startswith("file:_"):
            return Path(text[6:])
        if lowered.startswith("file:"):
            return Path(text[5:])
        return None

    @staticmethod
    def _as_resource_list(resources: Any) -> list[str]:
        if resources is None:
            return []
        if isinstance(resources, list):
            return [str(item) for item in resources]
        return [str(resources)]

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _safe_name(value: str) -> str:
        text = str(value or "item")
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)
        return safe[:120] or "item"

    @staticmethod
    def _result(
        action: ResponseAction,
        status: ActionStatus,
        detail: str,
        started: int,
        params: dict[str, Any] | None = None,
    ) -> ActionExecutionResult:
        return ActionExecutionResult(
            action_id=action.action_id,
            status=status,
            detail=detail,
            started_ts_ms=started,
            completed_ts_ms=now_ms(),
            params=params or {},
        )
