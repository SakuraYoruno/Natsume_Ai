"""完整模拟 handle_chat 流程，捕获详细异常"""
import sys, io, os, traceback, time, asyncio
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

from host_brain.app.schemas import ChatRequest

class FakeRequest:
    class state:
        request_id = "test-full-001"

async def run():
    from host_brain.app.chat_handler import handle_chat
    from host_brain.observability.metrics import MetricsCollector
    from host_brain.composition.process_services import get_ledger

    metrics = MetricsCollector(ledger=get_ledger())
    req_body = ChatRequest(
        message="你好，夏目，今天天气怎么样？",
        session_id=f"test-session-full-{int(time.time())}",
    )
    fake_req = FakeRequest()

    print("=== 调用 handle_chat ===\n")
    try:
        result = await handle_chat(req_body, fake_req, metrics)
        print(f"decision: {result.decision}")
        print(f"reason_code: {result.reason_code}")
        print(f"quality: {result.quality}")
        print(f"narrative: {str(result.narrative)[:100]}")
        if result.audit_refs:
            llm = result.audit_refs.get("llm", {})
            print(f"llm.status: {llm.get('status')}")
            print(f"llm.provider: {llm.get('provider')}")
            print(f"llm.model_id: {llm.get('model_id')}")
    except Exception as e:
        print(f"\nEXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()

asyncio.run(run())
