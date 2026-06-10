#!/usr/bin/env python
"""Build EPUB from original article HTML — preserves all formatting."""

import os, re, uuid, time, shutil, zipfile, glob, json, base64
from html import escape as html_escape
import urllib.request, websocket, requests

OUT = 'D:/Natsume_Ai/runtime/browser'

# ===== CDP: get raw HTML + cookie =====
tabs = json.loads(urllib.request.urlopen('http://127.0.0.1:9222/json', timeout=3).read())
ws_url = next((t['webSocketDebuggerUrl'] for t in tabs if t.get('type') == 'page' and not t.get('url','').startswith('chrome-')), None)
if not ws_url:
    ws_url = next((t['webSocketDebuggerUrl'] for t in tabs if t.get('type') == 'page'), None)
assert ws_url, "No Chrome tab"

def cdp(expr, timeout=20):
    ws = websocket.create_connection(ws_url, timeout=10)
    ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr,"returnByValue":True}}))
    for _ in range(15):
        msg = json.loads(ws.recv())
        if msg.get('id') == 1:
            ws.close()
            return msg['result']['result'].get('value')
    ws.close()

# Cookie
ws = websocket.create_connection(ws_url, timeout=10)
ws.send(json.dumps({"id":1,"method":"Network.getCookies","params":{"urls":["https://www.lightnovel.fun/"]}}))
COOKIE = ''
for _ in range(10):
    msg = json.loads(ws.recv())
    if msg.get('id') == 1:
        for c in msg['result']['cookies']:
            if c['name'] == 'token': COOKIE = f"{c['name']}={c['value']}"
        break
ws.close()
HEADERS = {'Cookie': COOKIE, 'Referer': 'https://www.lightnovel.fun/', 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# ===== Get article data =====
print("Fetching article...")
data = json.loads(cdp("""(function() {
  var title = document.title.split('-')[0].trim();
  var body = document.body.innerText;
  var author = '';
  var m = body.match(/作者[:\\uff1a]\\s*(.+)/);
  if (m) author = m[1].split(/[\\n\\u3001\\/]/)[0].trim().slice(0,40);

  var art = document.querySelector('.article-content');
  var html = '';
  if (art) {
    html = art.innerHTML;
    html = html.replace(/ style="[^"]*"/gi, '');
    html = html.replace(/ loading="[^"]*"/gi, '');
    html = html.replace(/ img-width="[^"]*"/gi, '');
    html = html.replace(/ img-height="[^"]*"/gi, '');
    html = html.replace(/ data-[^=]*="[^"]*"/gi, '');
    html = html.replace(/^<article[^>]*>/i, '');
    html = html.replace(/<\\/article>$/i, '');
    // Fix self-closing tags for XHTML
    html = html.replace(/<br>/gi, '<br/>');
    html = html.replace(/<hr>/gi, '<hr/>');
    // Fix img tags without closing slash
    html = html.replace(/(<img[^>]*?)>/gi, '$1/>');
  }

  var imgs = [];
  if (art) {
    art.querySelectorAll('img').forEach(function(img) {
      if ((img.src||'').includes('res.lightnovel.fun/images/') && !(img.src||'').includes('smiley'))
        imgs.push(img.src);
    });
  }

  return JSON.stringify({title: title, author: author, html: html, images: imgs});
})()"""))

title = data['title'][:100]
author = data.get('author', '') or 'Unknown'
html = data['html']
imgs = data['images']

print(f"Title : {title[:60]}")
print(f"Author: {author}")
print(f"HTML  : {len(html)} chars")
print(f"Images: {len(imgs)}")

# ===== Download images =====
img_dir = os.path.join(OUT, 'novel_images')
os.makedirs(img_dir, exist_ok=True)
for old in glob.glob(os.path.join(img_dir, '*')):
    try: os.remove(old)
    except: pass

img_map = {}
for i, src in enumerate(imgs):
    try:
        r = requests.get(src, headers=HEADERS, timeout=30)
        if r.status_code == 200 and len(r.content) > 500:
            ext = os.path.splitext(src.split('?')[0])[1][:5]
            if ext not in ('.jpg','.jpeg','.png','.gif','.webp'): ext = '.jpg'
            fname = f'img_{i:03d}{ext}'
            with open(os.path.join(img_dir, fname), 'wb') as f: f.write(r.content)
            img_map[src] = fname
            print(f"  [{i:02d}] {fname} {len(r.content)//1024}KB")
        else:
            print(f"  [{i:02d}] HTTP {r.status_code} {len(r.content)} bytes")
    except Exception as e:
        print(f"  [{i:02d}] ERR: {e}")

print(f"\nDownloaded: {len(img_map)}/{len(imgs)}")

# ===== Replace image URLs =====
for orig_src, fname in img_map.items():
    html = html.replace(orig_src, f'images/{fname}')
    # URL-encoded &
    enc = orig_src.replace('&', '&amp;')
    if enc != orig_src:
        html = html.replace(enc, f'images/{fname}')

print(f"HTML after replacement: {len(html)} chars")
print(f"First 300: {html[:300]}")

# ===== Build EPUB =====
title_safe = re.sub(r'[\\/:*?"<>|]', '-', title)[:50]
build_dir = os.path.join(OUT, 'epub_build')
if os.path.exists(build_dir):
    shutil.rmtree(build_dir, ignore_errors=True)
for d in [f'{build_dir}/OEBPS/images', f'{build_dir}/META-INF']:
    os.makedirs(d, exist_ok=True)
uid = str(uuid.uuid4())

# CSS - clean, reader-friendly
css = '''body { font-family: "Microsoft YaHei","PingFang SC","SimSun","MS Mincho",serif; line-height:1.9; margin:0.3em; color:#333; }
h1 { text-align:center; font-size:1.5em; margin:1em 0 0.3em 0; }
b, strong { color:#222; }
img { max-width:100%; height:auto; display:block; margin:0.8em auto; }
.meta { text-align:center; font-size:0.85em; color:#999; margin:0.5em 0 1.5em 0; }
.cover { text-align:center; padding:3em 1em; page-break-after:always; }
.cover h1 { font-size:1.6em; }
.cover .author { font-size:1.2em; color:#555; }
.cover img { max-width:90%; height:auto; margin:1.5em auto; }
.cover .meta { font-size:0.85em; color:#999; }'''
with open(f'{build_dir}/OEBPS/style.css', 'w', encoding='utf-8') as f:
    f.write(css)

# cover.xhtml
cover_fname = list(img_map.values())[0] if img_map else ''
cover_xhtml = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{html_escape(title)}</title><meta charset="utf-8"/><link rel="stylesheet" href="style.css" type="text/css"/></head>
<body><div class="cover">
<h1>{html_escape(title)}</h1>
<p class="author">作者: {html_escape(author)}</p>
<p class="meta">轻之国度 lightnovel.fun | Natsume Agent 制作 | {time.strftime('%Y-%m-%d')}</p>
<img src="images/{html_escape(cover_fname)}" alt="封面"/>
</div></body></html>'''
with open(f'{build_dir}/OEBPS/cover.xhtml', 'w', encoding='utf-8') as f:
    f.write(cover_xhtml)

# content.xhtml — the raw HTML as-is
content_xhtml = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{html_escape(title)}</title><meta charset="utf-8"/><link rel="stylesheet" href="style.css" type="text/css"/></head>
<body>
<h1>{html_escape(title)}</h1>
<p class="meta">作者: {html_escape(author)} | {time.strftime('%Y-%m-%d')}</p>
<hr/>
{html}
</body></html>'''
with open(f'{build_dir}/OEBPS/content.xhtml', 'w', encoding='utf-8') as f:
    f.write(content_xhtml)

# content.opf
cover_item = ''
if cover_fname:
    cover_item = f'    <item id="cover-image" href="images/{cover_fname}" media-type="image/jpeg" properties="cover-image"/>'

img_items = []
for i, fname in enumerate(img_map.values()):
    ext = os.path.splitext(fname)[1][1:]
    mt = {'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png','gif':'image/gif','webp':'image/webp'}.get(ext,'image/jpeg')
    img_items.append(f'    <item id="img{i}" href="images/{fname}" media-type="{mt}"/>')

opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="book-id" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">{uid}</dc:identifier>
    <dc:title>{html_escape(title)}</dc:title>
    <dc:creator>{html_escape(author)}</dc:creator>
    <dc:publisher>lightnovel.fun</dc:publisher>
    <dc:language>zh</dc:language>
    <dc:date>{time.strftime('%Y-%m-%d')}</dc:date>
    <meta name="cover" content="cover-image"/>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="style" href="style.css" media-type="text/css"/>
    <item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>
    <item id="content-page" href="content.xhtml" media-type="application/xhtml+xml"/>
{cover_item}
{chr(10).join(img_items)}
  </manifest>
  <spine toc="ncx">
    <itemref idref="cover-page"/>
    <itemref idref="content-page"/>
  </spine>
  <guide>
    <reference type="cover" title="封面" href="cover.xhtml"/>
  </guide>
</package>'''
with open(f'{build_dir}/OEBPS/content.opf', 'w', encoding='utf-8') as f:
    f.write(opf)

# toc.ncx
with open(f'{build_dir}/OEBPS/toc.ncx', 'w', encoding='utf-8') as f:
    f.write(f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="{uid}"/></head>
<docTitle><text>{html_escape(title)}</text></docTitle>
<navMap>
  <navPoint id="nav-cover" playOrder="1"><navLabel><text>封面</text></navLabel><content src="cover.xhtml"/></navPoint>
  <navPoint id="nav-content" playOrder="2"><navLabel><text>正文</text></navLabel><content src="content.xhtml"/></navPoint>
</navMap>
</ncx>''')

with open(f'{build_dir}/mimetype', 'w') as f: f.write('application/epub+zip')
with open(f'{build_dir}/META-INF/container.xml', 'w', encoding='utf-8') as f:
    f.write('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')

# Copy images
for fname in img_map.values():
    shutil.copy2(os.path.join(img_dir, fname), os.path.join(build_dir, 'OEBPS', 'images', fname))

# ZIP
epub_dir = os.path.join(OUT, 'epub_output')
epub_path = os.path.join(epub_dir, f'{title_safe}.epub')
with zipfile.ZipFile(epub_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.write(os.path.join(build_dir, 'mimetype'), 'mimetype', compress_type=zipfile.ZIP_STORED)
    for root, dirs, files in os.walk(build_dir):
        for fn in files:
            if fn == 'mimetype': continue
            zf.write(os.path.join(root, fn), os.path.relpath(os.path.join(root, fn), build_dir).replace('\\', '/'))

sz = os.path.getsize(epub_path) / 1024
print(f"\nEPUB: {epub_path}")
print(f"Size: {sz:.0f} KB | Imgs: {len(img_map)} | Cover: YES")
print("Done!")
