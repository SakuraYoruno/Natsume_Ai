"""用 TestClient 直接测试路由，捕获完整 traceback"""
import sys
sys.path.insert(0, ".")

from fastapi.testclient import TestClient
from host_brain.app.asgi import app

client = TestClient(app, raise_server_exceptions=True)

print("=== 测试 GET / ===")
try:
    r = client.get("/")
    print(f"[{r.status_code}] {r.text[:300]}")
except Exception as e:
    import traceback
    print(f"[EXCEPTION] {e}")
    traceback.print_exc()

print()
print("=== 测试 GET /health ===")
try:
    r = client.get("/health")
    print(f"[{r.status_code}] {r.text[:500]}")
except Exception as e:
    import traceback
    print(f"[EXCEPTION] {e}")
    traceback.print_exc()

print()
print("=== 测试 GET /openapi.json ===")
try:
    r = client.get("/openapi.json")
    print(f"[{r.status_code}] {r.text[:300]}")
except Exception as e:
    import traceback
    print(f"[EXCEPTION] {e}")
    traceback.print_exc()
