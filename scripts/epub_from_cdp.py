#!/usr/bin/env python
"""Get page HTML via CDP, feed to existing EPUB builder."""
import sys, json, time, urllib.request, websocket, shutil
from pathlib import Path
sys.path.insert(0, 'D:/Natsume_Ai')
OUT = Path('D:/Natsume_Ai/runtime/browser')

PAGE_URL = "https://www.lightnovel.fun/cn/detail/1139371"

# ---- Get any page tab WS URL ----
tabs = json.loads(urllib.request.urlopen('http://127.0.0.1:9222/json', timeout=5).read())
ws_url = None
for t in tabs:
    if t.get('type') == 'page' and t.get('url','').startswith('http') and 'chrome-extension' not in t.get('url',''):
        ws_url = t['webSocketDebuggerUrl']
        print(f"Using existing tab: {t['title'][:40]}")
        break
if not ws_url:
    for t in tabs:
        if t.get('type') == 'page':
            ws_url = t['webSocketDebuggerUrl']
            break
assert ws_url, "No tab"

def cdp_call(method, params=None, timeout=60):
    """Single CDP call with reconnection on failure."""
    global ws_url
    for attempt in range(3):
        try:
            ws = websocket.create_connection(ws_url, timeout=15)
            ws.settimeout(timeout)
            ws.send(json.dumps({"id":1,"method":method,"params":params or {}}))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                msg = json.loads(ws.recv())
                if msg.get('id') == 1:
                    ws.close()
                    return msg.get('result', {})
            ws.close()
            raise TimeoutError(f"{method}: no response in {timeout}s")
        except (TimeoutError, websocket.WebSocketTimeoutException) as e:
            if attempt == 2: raise
            print(f"  CDP retry {attempt+1}: {e}")
            time.sleep(2)
            # Refresh WS URL
            tabs2 = json.loads(urllib.request.urlopen('http://127.0.0.1:9222/json', timeout=5).read())
            for t2 in tabs2:
                if t2.get('type') == 'page':
                    ws_url = t2['webSocketDebuggerUrl']
                    break

# 1. Navigate
print("Navigating...")
result = cdp_call("Page.navigate", {"url": PAGE_URL})
print(f"  frameId: {result.get('frameId','?')[:20]}")

# 2. Wait for page to fully render
print("Waiting for SPA render...")
for i in range(15):
    time.sleep(1.5)
    try:
        r = cdp_call("Runtime.evaluate", {
            "expression": "document.body && document.body.innerText && document.body.innerText.length",
            "returnByValue": True
        }, timeout=10)
        body_len = r.get('result',{}).get('value', 0)
        if body_len and body_len > 1000:
            print(f"  Page ready: {body_len} chars body")
            break
        print(f"  ...{body_len} chars")
    except Exception as e:
        print(f"  ...retrying: {e}")

# 3. Scroll for lazy images
print("Scrolling for lazy images...")
for y in [2000, 5000, 10000, 20000, 40000]:
    cdp_call("Runtime.evaluate", {
        "expression": f"window.scrollTo(0,{y})",
        "returnByValue": True
    }, timeout=10)
    time.sleep(0.5)

# 4. Get outerHTML
print("Getting page HTML...")
r = cdp_call("Runtime.evaluate", {
    "expression": "document.documentElement.outerHTML",
    "returnByValue": True
}, timeout=30)
html = r.get('result',{}).get('value','')
if not html:
    raise RuntimeError("Failed to get page HTML")
print(f"  HTML: {len(html)} chars")

# 5. Save
html_path = OUT / 'page_rendered.html'
html_path.write_text(html, encoding='utf-8')
print(f"  Saved to: {html_path}")

# 6. Use existing EPUB builder
print("\nBuilding EPUB with lightnovel_epub_builder...")
from host_brain.agent_runtime.lightnovel_epub_builder import build_lightnovel_epub_package

run_dir = OUT / 'epub_run'
# Get actual title from page
title_r = cdp_call("Runtime.evaluate", {
    "expression": "document.title.split('-')[0].trim()",
    "returnByValue": True
}, timeout=10)
actual_title = title_r.get('result', {}).get('value', 'Light Novel')
print(f"  Title from page: {actual_title[:80]}")

result = build_lightnovel_epub_package(
    input_pack={
        "url": PAGE_URL,
        "html_path": str(html_path),
        "title": actual_title,
    },
    run_dir=run_dir,
)

print(f"\n{'='*50}")
print(f"Status:     {result['status']}")
print(f"Title:      {result['title'][:60]}")
print(f"Chapters:   {result.get('chapter_count', 0)}")
print(f"Images:     {result.get('image_count', 0)}")
print(f"Text:       {result.get('text_char_count', 0)} chars")
print(f"EPUB:       {result.get('epub_path', 'NONE')}")
if result.get('epub_size_bytes'):
    print(f"Size:       {result['epub_size_bytes']/1024:.0f} KB")
print(f"Validation: {json.dumps(result.get('validation',{}), indent=2, ensure_ascii=False)}")

# 7. Copy to output
if result.get('epub_path'):
    epub_src = Path(result['epub_path'])
    OUT.joinpath('epub_output').mkdir(parents=True, exist_ok=True)
    epub_dst = OUT / 'epub_output' / epub_src.name
    shutil.copy2(epub_src, epub_dst)
    print(f"\nCopied: {epub_dst}")
