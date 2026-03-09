import urllib.request

for url in ["https://hf-mirror.com", "https://huggingface.co"]:
    try:
        r = urllib.request.urlopen(url, timeout=8)
        print(f"OK  {url}  status={r.status}")
    except Exception as e:
        print(f"FAIL {url}  {e}")
