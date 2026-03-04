from host_brain.release.release_manifest import generate_m10e_manifest
import json, pathlib

manifest = generate_m10e_manifest(
    root=".",
    test_passed_count=1090,
    service_registry_snapshot=[
        {"service_id": "host.audit.sqlite_ledger", "owner": "host", "required": True},
        {"service_id": "host.governor.decision", "owner": "host", "required": True},
        {"service_id": "host.skills.loader", "owner": "host", "required": True},
        {"service_id": "host.integrations.llm_gateway", "owner": "host", "required": True},
        {"service_id": "host.retrieval.embedding", "owner": "host", "required": True},
        {"service_id": "host.retrieval.reranker", "owner": "host", "required": True},
        {"service_id": "host.vision.screen_ocr", "owner": "host", "required": True},
        {"service_id": "host.vision.camera_recognizer", "owner": "host", "required": True},
        {"service_id": "host.memory.canon_sync", "owner": "host", "required": True},
        {"service_id": "host.canon.proposals_api", "owner": "host", "required": True},
        {"service_id": "host.canon.hotwords_api", "owner": "host", "required": True},
        {"service_id": "edge.rpc.gateway", "owner": "edge", "required": True},
        {"service_id": "edge.rpc.asr", "owner": "edge", "required": True},
        {"service_id": "edge.rpc.tts", "owner": "edge", "required": True},
        {"service_id": "edge.rpc.barge_in", "owner": "edge", "required": True},
        {"service_id": "edge.rpc.fallback_llm", "owner": "edge", "required": True},
        {"service_id": "edge.rpc.resource_gate", "owner": "edge", "required": True},
        {"service_id": "host.memory.distillation", "owner": "host", "required": True},
        {"service_id": "edge.asr.hotwords_cache", "owner": "edge", "required": False},
    ],
    ts_ms=1772628206000,
)

out = pathlib.Path("release/manifests/m10e_release.json")
out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
print("Generated:", out)
print("milestone:", manifest["milestone"])
print("test_passed_count:", manifest["test_passed_count"])
print("asr_hash:", manifest["asr_hash"])
print("hotwords_cache_ttl_test_hash:", manifest["hotwords_cache_ttl_test_hash"])
print("migration_file_count:", manifest["migration_file_count"])
print("generated_at:", manifest["generated_at"])
