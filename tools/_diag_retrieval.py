"""直接测试 retrieval pipeline"""
import sys, io, os, traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 加载 .env
env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value

print("CHROMA_ENDPOINT:", os.environ.get("CHROMA_ENDPOINT"))
print("HOST_RETRIEVAL_ENABLED:", os.environ.get("HOST_RETRIEVAL_ENABLED"))

try:
    from host_brain.app.dependencies import get_retrieval_pipeline
    pipeline = get_retrieval_pipeline()
    print("\nretrieval pipeline: OK")
    print("  type:", type(pipeline).__name__)

    # 测试 run
    result = pipeline.run(
        request_id="test-retr-001",
        query="你好，夏目，今天天气怎么样？",
        intent_id=None,
        session_id="test-session-retr",
        top_k=3,
        ts_ms=None,
        emotion_context=None,
    )
    print(f"\nretrieval result:")
    print(f"  status: {result.status}")
    print(f"  reason_code: {result.reason_code}")
    print(f"  output keys: {list(result.output.keys()) if result.output else 'None'}")
except Exception as e:
    print(f"\nERROR: {type(e).__name__}: {e}")
    traceback.print_exc()
