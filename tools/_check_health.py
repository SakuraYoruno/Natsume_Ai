"""查看 Host Brain 所有服务健康状态"""
import urllib.request, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

r = urllib.request.urlopen("http://127.0.0.1:18080/health", timeout=10)
data = json.loads(r.read())
print("status:", data.get("status"))
print()
svcs = data.get("details", {}).get("services", [])
if isinstance(svcs, list):
    for v in svcs:
        k = v.get("name", v.get("service", "?"))
        st = v.get("status", "?")
        msg = v.get("message", "")
        print(f"  {'✓' if st == 'UP' else '✗'} {k}: {st}  {msg[:60] if msg else ''}")
else:
    for k, v in svcs.items():
        st = v.get("status", "?")
        msg = v.get("message", "")
        print(f"  {'✓' if st == 'UP' else '✗'} {k}: {st}  {msg[:60] if msg else ''}")
print()
summary = data.get("details", {}).get("summary", {})
print("summary:", json.dumps(summary, ensure_ascii=False))
