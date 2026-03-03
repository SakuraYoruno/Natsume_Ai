import json
from pathlib import Path
from host_brain.release.release_manifest import generate_m8_manifest
from host_brain.obs.metrics import MetricsCollector
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.services import build_default_service_registry

ledger = SQLiteLedger(db_path=":memory:")
collector = MetricsCollector(ledger=ledger)
baseline = collector.collect_latency_baseline()

registry = build_default_service_registry()
service_snapshot = [
    {"service_id": s.service_id, "owner": s.owner, "required": s.required}
    for s in registry.all()
]

manifest = generate_m8_manifest(
    root=Path("."),
    test_passed_count=914,
    latency_baseline_snapshot=baseline,
    service_registry_snapshot=service_snapshot,
)

out_dir = Path("release/manifests")
out_dir.mkdir(parents=True, exist_ok=True)
out_path = out_dir / "m8_release.json"
out_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
print("Generated:", out_path)
print("migration_file_count:", manifest["migration_file_count"])
print("git_commit_hash:", manifest["git_commit_hash"])
print("generated_at:", manifest["generated_at"])
