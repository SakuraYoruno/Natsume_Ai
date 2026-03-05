import urllib.request, time, sys
time.sleep(2)
try:
    r = urllib.request.urlopen("http://127.0.0.1:8000/api/v2/heartbeat", timeout=5)
    print("ChromaDB status:", r.status, r.read().decode())
except Exception as e:
    print("ChromaDB not reachable:", e)
    sys.exit(1)
