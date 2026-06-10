#!/usr/bin/env python
"""Complete light novel pipeline v2 — robust CDP, handles no-illustration novels gracefully."""

import sys, json, time, urllib.request, websocket, os, base64, re, uuid, shutil, zipfile

sys.path.insert(0, 'D:/Natsume_Ai')
OUT = 'D:/Natsume_Ai/runtime/browser'

# ===== CDP session =====
_WS_URL = None

def _init_ws():
    global _WS_URL
    tabs = json.loads(urllib.request.urlopen('http://127.0.0.1:9222/json', timeout=5).read())
    for t in tabs:
        if t.get('type') == 'page':
            _WS_URL = t['webSocketDebuggerUrl']
            return
    raise RuntimeError("No Chrome tab")

def cdp_eval(expr, timeout=30):
    global _WS_URL
    ws = websocket.create_connection(_WS_URL, timeout=10)
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
        "params": {"expression": expr, "returnByValue": True}}))
    for _ in range(20):
        msg = json.loads(ws.recv())
        if msg.get('id') == 1:
            ws.close()
            r = msg['result']['result']
            return r.get('value')
    ws.close()
    return None

def cdp_navigate(url, wait=5):
    global _WS_URL
    ws = websocket.create_connection(_WS_URL, timeout=10)
    ws.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": url}}))
    for _ in range(5):
        if json.loads(ws.recv()).get('id') == 2: break
    ws.close()
    time.sleep(wait)

def cdp_json(expr, timeout=30):
    v = cdp_eval(expr, timeout=timeout)
    if v is None: raise RuntimeError("JS returned null")
    if isinstance(v, (dict, list)): return v
    if isinstance(v, str) and v.strip()[:1] in '{[': return json.loads(v)
    raise RuntimeError(f"Unexpected: {str(v)[:100]}")

_init_ws()
print("CDP OK\n")

# ============================================================
# STEP 1: Scrape + enrich + rank
# ============================================================
print("=" * 60)
print("STEP 1: 抓取轻小说列表 + 智能排名")
print("=" * 60)

cdp_navigate("https://www.lightnovel.fun/cn/category/3/106?type=1", 6)

all_ids = cdp_json("""(function() {
  var ids = [], seen = {};
  document.querySelectorAll('a[href*="/cn/detail/"]').forEach(function(a) {
    var id = a.href.split('/').pop().split('?')[0];
    if (!seen[id]) {
      seen[id] = true;
      var t = a.textContent.trim().replace(/\\s+/g, ' ').slice(0, 150);
      if (!t.startsWith('置顶') && t.length > 8) ids.push({id: id, text: t});
    }
  });
  return JSON.stringify(ids.slice(0, 5));
})()""")
print(f"Found {len(all_ids)} candidates\n")

# Visit each for enrichment
enriched = []
for i, c in enumerate(all_ids):
    pid = c['id']
    print(f"[{i+1}/5] {pid}...", end=' ', flush=True)
    cdp_navigate(f"https://www.lightnovel.fun/cn/detail/{pid}", 4)

    info = cdp_json("""(function() {
      var body = document.body.innerText;
      return JSON.stringify({
        title: document.title.split('-')[0].trim(),
        bodyLen: body.length,
        coverImg: (function() {
          var best = {src: '', w: 0};
          document.querySelectorAll('img').forEach(function(img) {
            var s = img.src || '', w = img.naturalWidth || img.width || 0;
            if (w > best.w && s.startsWith('http') && !s.includes('smiley') &&
                !s.includes('_nuxt') && !s.includes('data:image')) {
              best = {src: s, w: w};
            }
          });
          return best.src;
        })(),
        articleImgCount: (function() {
          var art = document.querySelector('.article-content');
          return art ? art.querySelectorAll('img').length : 0;
        })(),
        downloadLinks: (function() {
          var links = [];
          document.querySelectorAll('a').forEach(function(a) {
            if (a.href && (a.href.includes('pan.baidu') || a.href.includes('lanzou') || a.href.includes('.epub'))) {
              links.push({text: a.textContent.trim().slice(0, 60), href: a.href});
              if (links.length >= 5) return;
            }
          });
          return links;
        })(),
        hasCoin: body.includes('投币'),
        hasBuy: body.includes('购买'),
        replyCount: document.querySelectorAll('.message-body, .post-message, [class*="reply-content"]').length
      });
    })()""")
    enriched.append({**c, **info})
    print(f"{info['title'][:50]}")
    print(f"     body={info['bodyLen']} imgs={info['articleImgCount']} dl={len(info['downloadLinks'])} coin={info['hasCoin']}")

# Score and rank
scored = []
for item in enriched:
    s = 0.0
    s += min(item['bodyLen'] / 500, 40)
    s += item['articleImgCount'] * 5
    s += len(item['downloadLinks']) * 20
    s += item['replyCount'] * 0.5
    if item['hasCoin'] or item['hasBuy']: s += 10
    title = item.get('title', '')
    if '[' in title: s += 3
    if '完' in title: s += 5
    scored.append((s, item))
scored.sort(key=lambda x: x[0], reverse=True)

print(f"\n{'=' * 60}")
print("STEP 2: TOP 3 推荐")
print("=" * 60)
for rank, (s, item) in enumerate(scored[:3], 1):
    print(f"  #{rank} score={s:.1f}  {item['title'][:55]}")
    print(f"       imgs={item['articleImgCount']} dl={len(item['downloadLinks'])} replies={item['replyCount']}")

selected = scored[0][1]
print(f"\n  >>> 选中: {selected['title'][:60]} <<<")

# ============================================================
# STEP 3: Scrape full content + download images
# ============================================================
print(f"\n{'=' * 60}")
print("STEP 3: 抓取完整内容 + 下载图片")
print("=" * 60)

cdp_navigate(f"https://www.lightnovel.fun/cn/detail/{selected['id']}", 6)

full = cdp_json("""(function() {
  var art = document.querySelector('.article-content, .message-content, article, main');
  var fullText = (art || document.body).innerText.slice(0, 100000);

  var allImgs = [];
  document.querySelectorAll('.article-content img, .message-content img, article img, .message-body img, .post-message img').forEach(function(img) {
    var s = img.src || '', w = img.naturalWidth || img.width || 0;
    if (w > 80 && s.startsWith('http') && !s.includes('smiley') &&
        !s.includes('avatar') && !s.includes('_nuxt') && !s.includes('qrcode') &&
        !s.includes('favicon') && !s.includes('data:image')) {
      allImgs.push({src: s, width: w, alt: img.alt || ''});
    }
  });

  var dlLinks = [];
  document.querySelectorAll('a').forEach(function(a) {
    if (a.href && (a.href.includes('pan.baidu') || a.href.includes('lanzou') || a.href.includes('.epub'))) {
      dlLinks.push({text: a.textContent.trim().slice(0, 80), href: a.href});
      if (dlLinks.length >= 10) return;
    }
  });

  // Password hints
  var body = document.body.innerText;
  var pwdIdx = body.indexOf('密码') >= 0 ? body.indexOf('密码') : body.indexOf('提取码');
  var pwd = pwdIdx >= 0 ? body.slice(Math.max(0, pwdIdx - 10), pwdIdx + 40) : '';

  return JSON.stringify({
    title: document.title.split('-')[0].trim(),
    fullText: fullText,
    allImages: allImgs,
    downloadLinks: dlLinks,
    passwordHint: pwd,
    bodyLen: body.length
  });
})()""")

title = full['title']
text = full['fullText']
imgs = full['allImages']
dl_links = full['downloadLinks']
pwd = full['passwordHint']

print(f"   Title: {title[:60]}")
print(f"   Text: {len(text)} chars")
print(f"   Images: {len(imgs)}")
print(f"   Downloads: {len(dl_links)}")
if pwd: print(f"   Password: {pwd[:80]}")

# Download images via browser fetch
os.makedirs(f'{OUT}/novel_images', exist_ok=True)
downloaded = []

for img in imgs[:20]:
    src = img['src']
    if not src.startswith('http'): continue
    try:
        b64 = cdp_eval(f"""
(function() {{
  return fetch({json.dumps(src)})
    .then(function(r) {{ return r.blob(); }})
    .then(function(blob) {{
      return new Promise(function(ok) {{
        var reader = new FileReader();
        reader.onload = function() {{ ok(reader.result.split(',')[1]); }};
        reader.readAsDataURL(blob);
      }});
    }})
    .catch(function(e) {{ return null; }});
}})()
""", timeout=25)
        if b64 and isinstance(b64, str) and len(b64) > 200:
            ext = os.path.splitext(src.split('?')[0])[1][:5]
            if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'): ext = '.jpg'
            fpath = os.path.join(OUT, 'novel_images', f"img_{len(downloaded):03d}{ext}")
            with open(fpath, 'wb') as f:
                f.write(base64.b64decode(b64))
            sz = os.path.getsize(fpath)
            downloaded.append(fpath)
            print(f"   OK: img_{len(downloaded)-1:03d}{ext} ({sz} bytes)")
    except Exception as e:
        print(f"   SKIP: {src[:60]}...")

# Also try to download cover
cover_url = selected.get('coverImg', '')
if cover_url and not any(cover_url in d for d in downloaded):
    try:
        b64 = cdp_eval(f"""
(function() {{
  return fetch({json.dumps(cover_url)})
    .then(function(r) {{ return r.blob(); }})
    .then(function(blob) {{
      return new Promise(function(ok) {{
        var reader = new FileReader();
        reader.onload = function() {{ ok(reader.result.split(',')[1]); }};
        reader.readAsDataURL(blob);
      }});
    }})
    .catch(function(e) {{ return null; }});
}})()
""", timeout=25)
        if b64 and isinstance(b64, str) and len(b64) > 200:
            ext = os.path.splitext(cover_url.split('?')[0])[1][:5]
            if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'): ext = '.jpg'
            fpath = os.path.join(OUT, 'novel_images', f'cover{ext}')
            with open(fpath, 'wb') as f:
                f.write(base64.b64decode(b64))
            downloaded.insert(0, fpath)  # cover first
            print(f"   OK: cover{ext} ({os.path.getsize(fpath)} bytes)")
    except:
        pass

print(f"\n   Downloaded: {len(downloaded)} images")

# Save text
with open(f'{OUT}/novel_full_text.txt', 'w', encoding='utf-8') as f:
    f.write(text)

# ============================================================
# STEP 4: Build EPUB
# ============================================================
print(f"\n{'=' * 60}")
print("STEP 4: 封装 EPUB")
print("=" * 60)

title_safe = re.sub(r'[\\/:*?"<>|]', '-', title)[:40]
paragraphs = [p.strip() for p in text.split('\n') if p.strip() and len(p.strip()) > 2][:800]

epub_dir = f'{OUT}/epub_output'
for d in [f'{epub_dir}/OEBPS/images', f'{epub_dir}/META-INF']:
    os.makedirs(d, exist_ok=True)

# mimetype
with open(f'{epub_dir}/mimetype', 'w') as f:
    f.write('application/epub+zip')

# container.xml
with open(f'{epub_dir}/META-INF/container.xml', 'w', encoding='utf-8') as f:
    f.write('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')

book_uid = str(uuid.uuid4())

# content.xhtml
img_html = []
for i, ip in enumerate(downloaded):
    ext = os.path.splitext(ip)[1][1:]
    label = '封面' if i == 0 else f'插图{i}'
    img_html.append(f'    <p><img src="images/img_{i:03d}.{ext}" alt="{label}"/></p>')

dl_html = ''
if dl_links:
    items = '\n'.join(f'      <li><a href="{dl["href"]}">{dl["text"][:60]}</a></li>' for dl in dl_links[:8])
    dl_html = f'<h2>下载链接</h2><ul>\n{items}\n    </ul>'
    if pwd:
        dl_html += f'\n    <p>提取信息: {pwd[:120]}</p>'

paras_html = '\n'.join(f'    <p>{p}</p>' for p in paragraphs[:600])

content_xhtml = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{title}</title><meta charset="utf-8"/></head>
<body>
  <h1>{title}</h1>
  <p><em>Natsume Agent 自动抓取 | {time.strftime('%Y-%m-%d %H:%M')} | 轻之国度</em></p>
  <p>正文 {len(text)} 字 | 插图 {len(downloaded)} 张 | 下载链接 {len(dl_links)} 个</p>
{dl_html}
  <hr/>
{paras_html}
{chr(10).join(img_html)}
</body>
</html>'''
with open(f'{epub_dir}/OEBPS/content.xhtml', 'w', encoding='utf-8') as f:
    f.write(content_xhtml)

# content.opf
img_items = []
for i, ip in enumerate(downloaded):
    ext = os.path.splitext(ip)[1][1:]
    mt = {'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png','gif':'image/gif','webp':'image/webp'}.get(ext,'image/jpeg')
    img_items.append(f'    <item id="img{i}" href="images/img_{i:03d}.{ext}" media-type="{mt}"/>')

content_opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="book-id" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">{book_uid}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator>Natsume Agent</dc:creator>
    <dc:language>zh</dc:language>
    <dc:date>{time.strftime('%Y-%m-%d')}</dc:date>
    <dc:publisher>lightnovel.fun</dc:publisher>
    <dc:description>由 Natsume Agent 从轻之国度自动抓取。{len(dl_links)} 个下载链接。</dc:description>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
{chr(10).join(img_items)}
  </manifest>
  <spine toc="ncx">
    <itemref idref="content"/>
  </spine>
</package>'''
with open(f'{epub_dir}/OEBPS/content.opf', 'w', encoding='utf-8') as f:
    f.write(content_opf)

# toc.ncx
with open(f'{epub_dir}/OEBPS/toc.ncx', 'w', encoding='utf-8') as f:
    f.write(f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{book_uid}"/></head>
  <docTitle><text>{title}</text></docTitle>
  <navMap><navPoint id="navpoint-1" playOrder="1"><navLabel><text>{title}</text></navLabel><content src="content.xhtml"/></navPoint></navMap>
</ncx>''')

# Copy images
for i, ip in enumerate(downloaded):
    ext = os.path.splitext(ip)[1][1:]
    shutil.copy2(ip, f'{epub_dir}/OEBPS/images/img_{i:03d}.{ext}')

# ZIP
epub_path = f'{OUT}/epub_output/{title_safe}.epub'
with zipfile.ZipFile(epub_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.write(f'{epub_dir}/mimetype', 'mimetype')
    for root, dirs, files in os.walk(epub_dir):
        for fname in files:
            if fname.endswith('.epub'): continue
            fpath = os.path.join(root, fname)
            zf.write(fpath, os.path.relpath(fpath, epub_dir).replace('\\', '/'))

size_kb = os.path.getsize(epub_path) / 1024

print(f"\n   EPUB : {epub_path}")
print(f"   Size : {size_kb:.0f} KB")
print(f"   Text : {len(paragraphs)} paragraphs")
print(f"   Imgs : {len(downloaded)}")
print(f"   DLs  : {len(dl_links)}")

print(f"\n{'=' * 60}")
print("COMPLETE")
print(f"{'=' * 60}")
print(f"  1. 推荐 3 本: #{scored[0][1]['id']}, #{scored[1][1]['id']}, #{scored[2][1]['id']}")
print(f"  2. 选中: {title[:50]}")
print(f"  3. 抓取: {len(text)} 字 + {len(downloaded)} 图")
print(f"  4. EPUB: {epub_path} ({size_kb:.0f} KB)")
print(f"{'=' * 60}")
