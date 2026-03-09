import urllib.request, json

for path in ["/ready", "/health/canon_sync"]:
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:18080{path}")
        data = json.loads(r.read())
        print(f"\n=== {path} ===")
        print(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"\n=== {path} ERROR: {e} ===")
