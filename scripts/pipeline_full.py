#!/usr/bin/env python
"""Full light novel pipeline: recommend 3, select 1, scrape, build EPUB.

Uses CDP (Chrome DevTools Protocol) directly to avoid agent-browser quirks.
"""

import sys, json, time, urllib.request, websocket, os, base64, re, uuid, shutil, zipfile

sys.path.insert(0, 'D:/Natsume_Ai')
OUT = 'D:/Natsume_Ai/runtime/browser'

# ===== CDP session =====
_WS_URL = None
_REQ_ID = 0

def _init_ws():
    global _WS_URL
    tabs = json.loads(urllib.request.urlopen('http://127.0.0.1:9222/json', timeout=5).read())
    for t in tabs:
        if t.get('type') == 'page':
            _WS_URL = t['webSocketDebuggerUrl']
            return
    raise RuntimeError("No Chrome page tab found")

def _send_recv(payload, timeout=30):
    global _WS_URL, _REQ_ID
    _REQ_ID += 1
    mid = _REQ_ID
    payload['id'] = mid

    for attempt in range(3):
        try:
            ws = websocket.create_connection(_WS_URL, timeout=10)
            ws.send(json.dumps(payload))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                ws.settimeout(timeout)
                msg = json.loads(ws.recv())
                if msg.get('id') == mid:
                    ws.close()
                    if 'error' in msg:
                        raise RuntimeError(f"CDP: {msg['error'].get('message', msg['error'])}")
                    return msg['result']
            ws.close()
            raise TimeoutError(payload.get('method', '?'))
        except (websocket.WebSocketException, ConnectionRefusedError, TimeoutError, OSError):
            if attempt == 2: raise
            time.sleep(1.5)
            _init_ws()

def navigate(url, wait_sec=5):
    _send_recv({"method": "Page.navigate", "params": {"url": url}})
    time.sleep(wait_sec)

def jseval(js_code, timeout=20):
    """Evaluate JS and return the value. Returns None on error."""
    result = _send_recv({
        "method": "Runtime.evaluate",
        "params": {"expression": js_code, "returnByValue": True}
    }, timeout=timeout)
    # result is the CDP response: {result: {type, value}}
    inner = result.get('result', {})
    if inner.get('type') == 'undefined':
        return None
    if 'value' in inner:
        return inner['value']
    # Check for exception
    if 'exceptionDetails' in result:
        exc = result['exceptionDetails']
        err_text = exc.get('text', '') or exc.get('exception', {}).get('description', 'JS Error')
        raise RuntimeError(f"JS: {err_text}")
    return None

def jseval_json(js_code, timeout=20):
    v = jseval(js_code, timeout=timeout)
    if v is None:
        raise RuntimeError(f"JS returned null: {js_code[:100]}...")
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str) and v.strip()[:1] in '{[':
        return json.loads(v)
    raise RuntimeError(f"Unexpected JS result: {str(v)[:100]}")

# Initialize
_init_ws()
print(f"CDP connected")


# ============================================================
# STEP 1: Scrape light novel listing, enrich top candidates
# ============================================================
print("=" * 60)
print("STEP 1: 抓取轻小说列表 + 富化 TOP 5")
print("=" * 60)

navigate("https://www.lightnovel.fun/cn/category/3/106?type=1", 6)

# Simple: get all detail IDs
ids_raw = jseval("""
(function() {
  var ids = [];
  var seen = {};
  var links = document.querySelectorAll('a[href*="/cn/detail/"]');
  for (var i = 0; i < links.length; i++) {
    var href = links[i].href;
    var id = href.split('/').pop().split('?')[0];
    if (!seen[id]) {
      seen[id] = true;
      var text = links[i].textContent.trim().replace(/\\s+/g, ' ').slice(0, 150);
      if (!text.startsWith('置顶') && text.length > 8) {
        ids.push({id: id, text: text});
      }
    }
  }
  return JSON.stringify(ids.slice(0, 5));
})()
""")
candidates = json.loads(ids_raw)
print(f"   Candidates: {len(candidates)}")
for i, c in enumerate(candidates):
    print(f"   [{i+1}] {c['id']}: {c['text'][:60]}")

# Visit each detail page for enrichment
enriched = []
for i, c in enumerate(candidates):
    pid = c['id']
    print(f"\n   [{i+1}/5] 富化 {pid}...")
    navigate(f"https://www.lightnovel.fun/cn/detail/{pid}", 5)

    info = jseval_json("""
(function() {
  var body = document.body.innerText;

  // Cover image
  var coverImg = '';
  var imgs = document.querySelectorAll('img');
  for (var i = 0; i < imgs.length; i++) {
    var src = imgs[i].src || '';
    var w = imgs[i].naturalWidth || imgs[i].width || 0;
    if (w > 150 && src.startsWith('http') && !src.includes('avatar') && !src.includes('logo') && !src.includes('qr_code') && !src.includes('favicon')) {
      coverImg = src;
      break;
    }
  }

  // Article images count
  var articleImgs = document.querySelectorAll(
    '.article-content img, .message-content img, article img, main img, .message-body img, .post-message img'
  );
  var imgCount = 0;
  for (var j = 0; j < articleImgs.length; j++) {
    var w2 = articleImgs[j].naturalWidth || articleImgs[j].width || 0;
    if (w2 > 80) imgCount++;
  }

  // Download links
  var dlLinks = [];
  var allLinks = document.querySelectorAll('a');
  for (var k = 0; k < allLinks.length; k++) {
    var h = allLinks[k].href;
    if (h && (h.includes('pan.baidu') || h.includes('lanzou') || h.includes('.epub') || h.includes('download'))) {
      dlLinks.push({text: allLinks[k].textContent.trim().slice(0, 40), href: h.slice(0, 200)});
      if (dlLinks.length >= 5) break;
    }
  }

  return JSON.stringify({
    title: document.title.split('-')[0].trim(),
    bodyLen: body.length,
    coverImg: coverImg,
    imgCount: imgCount,
    downloadLinks: dlLinks,
    hasCoin: body.includes('投币'),
    hasBuy: body.includes('购买'),
    replyCount: document.querySelectorAll('.message-body, .post-message, [class*="reply-content"]').length
  });
})()
""")
    enriched.append({**c, **info})
    print(f"      {info['title'][:50]}")
    print(f"      body={info['bodyLen']}  imgs={info['imgCount']}  replies={info['replyCount']}  coin={info['hasCoin']}")

with open(f'{OUT}/step1_enriched.json', 'w', encoding='utf-8') as f:
    json.dump(enriched, f, indent=2, ensure_ascii=False)
print(f"\n   Saved enriched data")


# ============================================================
# STEP 2: Rank and pick
# ============================================================
print(f"\n{'=' * 60}")
print("STEP 2: 智能排名 TOP 3")
print("=" * 60)

scored = []
for item in enriched:
    s = 0.0
    s += min(item.get('bodyLen', 0) / 500, 40)
    s += item.get('imgCount', 0) * 8
    s += len(item.get('downloadLinks', [])) * 15
    s += item.get('replyCount', 0) * 0.5
    if item.get('hasCoin') or item.get('hasBuy'):
        s += 10
    title = item.get('title', '')
    if '[' in title or '【' in title:
        s += 3
    if '完' in title:
        s += 5
    scored.append((s, item))
scored.sort(key=lambda x: x[0], reverse=True)

for rank, (s, item) in enumerate(scored[:3], 1):
    print(f"   #{rank}  score={s:.1f}  {item['title'][:50]}")
    print(f"         imgs={item['imgCount']}  dl={len(item['downloadLinks'])}  replies={item['replyCount']}")

selected = scored[0][1]
print(f"\n   >>> 选中购买: {selected['title'][:60]} <<<")


# ============================================================
# STEP 3: Scrape full content + download images
# ============================================================
print(f"\n{'=' * 60}")
print("STEP 3: 抓取完整内容 + 下载图片")
print("=" * 60)

navigate(f"https://www.lightnovel.fun/cn/detail/{selected['id']}", 6)

full = jseval_json("""
(function() {
  // Full article text
  var art = document.querySelector('.article-content, .message-content, article, main');
  var fullText = (art || document.body).innerText.slice(0, 80000);

  // All article images
  var allImgs = [];
  var imgEls = document.querySelectorAll(
    '.article-content img, .message-content img, article img, main img, .message-body img, .post-message img'
  );
  for (var i = 0; i < imgEls.length; i++) {
    var img = imgEls[i];
    var src = img.src || '';
    var w = img.naturalWidth || img.width || 0;
    if (w > 60 && src.startsWith('http') && !src.includes('avatar') && !src.includes('qr_code') && !src.includes('favicon')) {
      allImgs.push({
        index: allImgs.length,
        src: src,
        alt: img.alt || img.title || '',
        width: w,
        height: img.naturalHeight || img.height || 0
      });
    }
  }

  // Reply texts (each reply = one chapter typically)
  var replies = [];
  var replyEls = document.querySelectorAll('.message-body, .post-message, [class*="reply-content"]');
  for (var j = 0; j < replyEls.length; j++) {
    var t = replyEls[j].innerText;
    if (t.length > 80) {
      replies.push(t.slice(0, 5000));
      if (replies.length >= 15) break;
    }
  }

  return JSON.stringify({
    fullText: fullText,
    allImages: allImgs,
    replies: replies
  });
})()
""")

text_len = len(full['fullText'])
img_count = len(full['allImages'])
reply_count = len(full['replies'])
print(f"   正文: {text_len} chars")
print(f"   插图: {img_count} 张")
print(f"   章节: {reply_count} 个")

# Download images
print(f"\n   下载图片...")
os.makedirs(f'{OUT}/novel_images', exist_ok=True)
downloaded = []
for img in full['allImages'][:20]:
    src = img['src']
    if not src.startswith('http'):
        continue
    try:
        fetch_js = f"""
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
    .catch(function(e) {{ return 'ERR:' + e.message; }});
}})()
"""
        b64 = jseval(fetch_js, timeout=30)
        if b64 and isinstance(b64, str) and not b64.startswith('ERR:'):
            ext = os.path.splitext(src.split('?')[0])[1][:5]
            if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
                ext = '.jpg'
            fname = f"img_{img['index']:03d}{ext}"
            fpath = os.path.join(OUT, 'novel_images', fname)
            data = base64.b64decode(b64)
            with open(fpath, 'wb') as f:
                f.write(data)
            downloaded.append(fpath)
            print(f"      OK: {fname} ({len(data)} bytes)")
        else:
            print(f"      SKIP: {src[:70]}...")
    except Exception as e:
        print(f"      ERR: {src[:60]}... ({e})")

print(f"\n   图片下载: {len(downloaded)}/{img_count}")

# Save text
with open(f'{OUT}/novel_full_text.txt', 'w', encoding='utf-8') as f:
    f.write(full['fullText'])


# ============================================================
# STEP 4: Build EPUB
# ============================================================
print(f"\n{'=' * 60}")
print("STEP 4: 封装 EPUB")
print("=" * 60)

title = selected['title'][:60]
title_safe = re.sub(r'[\\/:*?"<>|]', '-', title)[:40]
text = full['fullText']
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

uid = str(uuid.uuid4())

# content.xhtml
img_tags = []
for i, img_path in enumerate(downloaded):
    ext = os.path.splitext(img_path)[1][1:]
    img_tags.append(f'    <p><img src="images/img_{i:03d}.{ext}" alt="插图 {i+1}"/></p>')

paras_html = '\n'.join(f'    <p>{p}</p>' for p in paragraphs)

content_xhtml = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{title}</title><meta charset="utf-8"/></head>
<body>
  <h1>{title}</h1>
{paras_html}
{chr(10).join(img_tags)}
</body>
</html>'''
with open(f'{epub_dir}/OEBPS/content.xhtml', 'w', encoding='utf-8') as f:
    f.write(content_xhtml)

# content.opf
img_items = []
for i, img_path in enumerate(downloaded):
    ext = os.path.splitext(img_path)[1][1:]
    mt_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
              'gif': 'image/gif', 'webp': 'image/webp'}
    mt = mt_map.get(ext, 'image/jpeg')
    img_items.append(f'    <item id="img{i}" href="images/img_{i:03d}.{ext}" media-type="{mt}"/>')

content_opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="book-id" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">{uid}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator>Natsume Agent</dc:creator>
    <dc:language>zh</dc:language>
    <dc:date>{time.strftime('%Y-%m-%d')}</dc:date>
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
toc_ncx = f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{uid}"/></head>
  <docTitle><text>{title}</text></docTitle>
  <navMap>
    <navPoint id="navpoint-1" playOrder="1">
      <navLabel><text>{title}</text></navLabel>
      <content src="content.xhtml"/>
    </navPoint>
  </navMap>
</ncx>'''
with open(f'{epub_dir}/OEBPS/toc.ncx', 'w', encoding='utf-8') as f:
    f.write(toc_ncx)

# Copy images
for i, img_path in enumerate(downloaded):
    ext = os.path.splitext(img_path)[1][1:]
    shutil.copy2(img_path, f'{epub_dir}/OEBPS/images/img_{i:03d}.{ext}')

# ZIP into EPUB
epub_path = f'{OUT}/epub_output/{title_safe}.epub'
with zipfile.ZipFile(epub_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.write(f'{epub_dir}/mimetype', 'mimetype')
    for root, dirs, files in os.walk(epub_dir):
        for fname in files:
            if fname.endswith('.epub'):
                continue
            fpath = os.path.join(root, fname)
            arcname = os.path.relpath(fpath, epub_dir).replace('\\', '/')
            zf.write(fpath, arcname)

size_kb = os.path.getsize(epub_path) / 1024

print(f"\n   EPUB  : {epub_path}")
print(f"   大小  : {size_kb:.0f} KB")
print(f"   标题  : {title}")
print(f"   段落  : {len(paragraphs)}")
print(f"   插图  : {len(downloaded)}")

print(f"\n{'=' * 60}")
print("全流程完成!")
print(f"{'=' * 60}")
print(f"  STEP 1: 抓取+富化 5 本候选 ✅")
print(f"  STEP 2: 智能排名 TOP 3，选中 #{scored[0][1]['id']} ✅")
print(f"  STEP 3: 抓取 {text_len} 字正文 + {len(downloaded)} 张图片 ✅")
print(f"  STEP 4: EPUB 封装完成 ✅")
print(f"\n  输出文件: {epub_path} ({size_kb:.0f} KB)")
print(f"  图片目录: {OUT}/novel_images/")
print(f"{'=' * 60}")
