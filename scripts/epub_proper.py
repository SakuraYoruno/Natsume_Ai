#!/usr/bin/env python
"""Use the existing lightnovel_epub_builder with CDP-obtained HTML."""
import sys, os, json, time, urllib.request, websocket
from pathlib import Path
sys.path.insert(0, 'D:/Natsume_Ai')
OUT = Path('D:/Natsume_Ai/runtime/browser')
PAGE_URL = "https://www.lightnovel.fun/cn/detail/1139371"

# ---- CDP: get page HTML ----
tabs = json.loads(urllib.request.urlopen('http://127.0.0.1:9222/json', timeout=5).read())
# Find ANY page tab (or create one)
ws_url = None
for t in tabs:
    if t.get('type') == 'page' and t.get('url', '').startswith('http'):
        ws_url = t['webSocketDebuggerUrl']
        break
if not ws_url:
    for t in tabs:
        if t.get('type') == 'page':
            ws_url = t['webSocketDebuggerUrl']
            break
assert ws_url, "No Chrome tab available"

ws = websocket.create_connection(ws_url, timeout=10)
ws.send(json.dumps({"id":1,"method":"Page.navigate","params":{"url":PAGE_URL}}))
for _ in range(10):
    if json.loads(ws.recv()).get('id') == 1: break
ws.close()
time.sleep(6)

# Scroll for lazy images
ws = websocket.create_connection(ws_url, timeout=10)
for y in range(2000, 40000, 2000):
    ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":f"window.scrollTo(0,{y})","returnByValue":True}}))
    for _ in range(5):
        if json.loads(ws.recv()).get('id')==1: break
    time.sleep(0.2)
ws.close()
time.sleep(2)

# Get full page HTML
ws = websocket.create_connection(ws_url, timeout=10)
ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":"document.documentElement.outerHTML","returnByValue":True}}))
html_content = None
for _ in range(15):
    msg = json.loads(ws.recv())
    if msg.get('id')==1:
        html_content = msg['result']['result']['value']
        break
ws.close()

# Save HTML
html_path = OUT / 'page_full.html'
html_path.write_text(html_content, encoding='utf-8')
print(f"HTML saved: {html_path} ({len(html_content)} chars)")

# ---- Use existing builder ----
from host_brain.agent_runtime.lightnovel_epub_builder import build_lightnovel_epub_package

run_dir = OUT / 'epub_run'
result = build_lightnovel_epub_package(
    input_pack={
        "url": PAGE_URL,
        "html_path": str(html_path),
    },
    run_dir=run_dir,
)

print(f"\nStatus: {result['status']}")
print(f"Title: {result['title']}")
print(f"Chapters: {result.get('chapter_count', 0)}")
print(f"Images: {result.get('image_count', 0)}")
print(f"Text chars: {result.get('text_char_count', 0)}")
print(f"EPUB: {result.get('epub_path', 'NONE')}")
print(f"Size: {(result.get('epub_size_bytes',0)/1024):.0f} KB")
print(f"Validation: {json.dumps(result.get('validation',{}), indent=2, ensure_ascii=False)}")

# Copy EPUB to output
import shutil
if result.get('epub_path'):
    epub_src = Path(result['epub_path'])
    epub_dst = OUT / 'epub_output' / epub_src.name
    OUT.joinpath('epub_output').mkdir(parents=True, exist_ok=True)
    shutil.copy2(epub_src, epub_dst)
    print(f"\nCopied to: {epub_dst}")
