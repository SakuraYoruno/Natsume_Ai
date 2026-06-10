#!/usr/bin/env python
"""Pipeline v4 — Python requests for image download with auth cookie."""

import sys, json, time, urllib.request, websocket, os, base64, re, uuid, shutil, zipfile, requests
from html import escape as html_escape

OUT = 'D:/Natsume_Ai/runtime/browser'

# ===== CDP =====
tabs = json.loads(urllib.request.urlopen('http://127.0.0.1:9222/json', timeout=5).read())
WS = None
for t in tabs:
    if t.get('type') == 'page': WS = t['webSocketDebuggerUrl']; break
if not WS: raise RuntimeError("No Chrome tab")

def cdp(expr, timeout=30, await_promise=False):
    ws = websocket.create_connection(WS, timeout=10)
    params = {"expression": expr, "returnByValue": True}
    if await_promise: params["awaitPromise"] = True
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": params}))
    for _ in range(20):
        msg = json.loads(ws.recv())
        if msg.get('id') == 1:
            ws.close()
            r = msg['result']['result']
            return r.get('value')
    ws.close()
    return None

def nav(url, wait=5):
    ws = websocket.create_connection(WS, timeout=10)
    ws.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": url}}))
    for _ in range(5):
        if json.loads(ws.recv()).get('id') == 2: break
    ws.close()
    time.sleep(wait)

# Get auth cookie
ws = websocket.create_connection(WS, timeout=10)
ws.send(json.dumps({"id": 1, "method": "Network.getCookies", "params": {"urls": ["https://www.lightnovel.fun/"]}}))
AUTH_COOKIE = ''
for _ in range(10):
    msg = json.loads(ws.recv())
    if msg.get('id') == 1:
        for c in msg['result']['cookies']:
            if c['name'] == 'token':
                AUTH_COOKIE = f"{c['name']}={c['value']}"
        break
ws.close()
print(f"CDP OK. Auth: {'YES' if AUTH_COOKIE else 'NO'}\n")

def download_img(src):
    """Download image with auth cookie. Returns path or None."""
    headers = {
        'Cookie': AUTH_COOKIE,
        'Referer': 'https://www.lightnovel.fun/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    try:
        r = requests.get(src, headers=headers, timeout=30)
        if r.status_code == 200 and len(r.content) > 500:
            ext = os.path.splitext(src.split('?')[0])[1][:5]
            if ext not in ('.jpg','.jpeg','.png','.gif','.webp'): ext = '.jpg'
            return (ext, r.content)
    except:
        pass
    return None

# ============================================================
# STEP 1+2: Scrape, enrich, rank, select
# ============================================================
print("=" * 50)
print("STEP 1+2: Scrape + Rank")
nav("https://www.lightnovel.fun/cn/category/3/106?type=1", 6)

ids = json.loads(cdp("""(function() {
  var ids=[], seen={};
  document.querySelectorAll('a[href*="/cn/detail/"]').forEach(function(a) {
    var id=a.href.split('/').pop().split('?')[0];
    if(!seen[id]){seen[id]=true;
      var t=a.textContent.trim().replace(/\\s+/g,' ').slice(0,150);
      if(!t.startsWith('置顶') && t.length>8) ids.push({id:id,text:t});
    }
  });
  return JSON.stringify(ids.slice(0,5));
})()"""))
print(f"Candidates: {len(ids)}")

enriched = []
for i, c in enumerate(ids):
    print(f"  [{i+1}] {c['id']}...", flush=True)
    nav(f"https://www.lightnovel.fun/cn/detail/{c['id']}", 4)
    info = json.loads(cdp("""(function() {
      var body = document.body.innerText;
      var art = document.querySelector('.article-content');
      var artImgs = art ? art.querySelectorAll('img') : [];
      var bigImgs = 0;
      for (var i=0; i<artImgs.length; i++) {
        var src = artImgs[i].src || '';
        if (src.includes('res.lightnovel.fun/images/') && !src.includes('smiley')) bigImgs++;
      }
      return JSON.stringify({
        title: document.title.split('-')[0].trim().slice(0,80),
        bodyLen: body.length,
        articleImgs: bigImgs,
        hasCoin: body.includes('投币'),
        dlCount: (function(){var n=0;document.querySelectorAll('a').forEach(function(a){
          if(a.href&&(a.href.includes('pan.baidu')||a.href.includes('lanzou')||a.href.includes('.epub')))n++;
        });return n;})()
      });
    })()"""))
    enriched.append({**c, **info})
    print(f"      {info['title'][:45]} | body={info['bodyLen']} imgs={info['articleImgs']} dl={info['dlCount']}")

scored = []
for item in enriched:
    s = min(item['bodyLen']/500, 40) + item['articleImgs'] * 10 + item['dlCount'] * 20
    if item['hasCoin']: s += 5
    scored.append((s, item))
scored.sort(key=lambda x: x[0], reverse=True)

print(f"\n  TOP 3:")
for rank, (s, item) in enumerate(scored[:3], 1):
    print(f"  #{rank} s={s:.0f} imgs={item['articleImgs']} {item['title'][:45]}")

selected = scored[0][1]
print(f"\n  Selected: {selected['title'][:60]}")

# ============================================================
# STEP 3: Full scrape + image download
# ============================================================
print(f"\n{'=' * 50}")
print("STEP 3: Scrape + Download")
nav(f"https://www.lightnovel.fun/cn/detail/{selected['id']}", 6)

# Scroll to load lazy images
print("  Scrolling for lazy-load...")
cdp("window.scrollTo(0,0)")
time.sleep(1)
for y in [2000, 5000, 10000, 20000, 40000, 80000, 120000]:
    cdp(f"window.scrollTo(0,{y})")
    time.sleep(0.6)
cdp("window.scrollTo(0,0)")
time.sleep(2)

full = json.loads(cdp("""(function() {
  var art = document.querySelector('.article-content');
  var fullText = (art || document.body).innerText.slice(0, 100000);

  var imgs = [];
  (art||document).querySelectorAll('img').forEach(function(img) {
    var src = img.src || '';
    if (src.includes('res.lightnovel.fun/images/') && !src.includes('smiley')) {
      imgs.push({src: src, width: img.naturalWidth||img.width||0});
    }
  });

  var dls = [];
  document.querySelectorAll('a').forEach(function(a) {
    if (a.href && (a.href.includes('pan.baidu')||a.href.includes('lanzou')||a.href.includes('.epub')))
      dls.push({text: a.textContent.trim().slice(0,60), href: a.href});
  });

  var body = document.body.innerText;
  var pwIdx = body.indexOf('密码')>=0 ? body.indexOf('密码') : body.indexOf('提取码');
  var pwd = pwIdx>=0 ? body.slice(Math.max(0,pwIdx-5), pwIdx+40) : '';

  return JSON.stringify({title: document.title.split('-')[0].trim(), fullText: fullText,
    images: imgs, downloads: dls, password: pwd});
})()"""))

print(f"  Title: {full['title'][:60]}")
print(f"  Text: {len(full['fullText'])} chars")
print(f"  Images: {len(full['images'])}")
print(f"  Downloads: {len(full['downloads'])}")

# Download ALL images via Python requests
os.makedirs(f'{OUT}/novel_images', exist_ok=True)
import glob
for old in glob.glob(f'{OUT}/novel_images/*'): os.remove(old)

downloaded = []
for idx, img in enumerate(full['images']):
    src = img['src']
    result = download_img(src)
    if result:
        ext, data = result
        fpath = os.path.join(OUT, 'novel_images', f'img_{idx:03d}{ext}')
        with open(fpath, 'wb') as f: f.write(data)
        sz = os.path.getsize(fpath)
        downloaded.append(fpath)
        print(f"  [{idx:02d}] img_{idx:03d}{ext} {sz//1024}KB", flush=True)
    else:
        print(f"  [{idx:02d}] FAIL {src[:80]}", flush=True)

print(f"\n  Downloaded: {len(downloaded)}/{len(full['images'])}")
with open(f'{OUT}/novel_full_text.txt', 'w', encoding='utf-8') as f:
    f.write(full['fullText'])

# ============================================================
# STEP 4: Build EPUB
# ============================================================
print(f"\n{'=' * 50}")
print("STEP 4: EPUB")

title = full['title'][:60]
title_safe = re.sub(r'[\\/:*?"<>|]', '-', title)[:40]
text = full['fullText']
paragraphs = [p.strip() for p in text.split('\n') if p.strip() and len(p.strip()) > 2][:800]

epub_dir = f'{OUT}/epub_output'
for d in [f'{epub_dir}/OEBPS/images', f'{epub_dir}/META-INF']:
    os.makedirs(d, exist_ok=True)

with open(f'{epub_dir}/mimetype', 'w') as f: f.write('application/epub+zip')
with open(f'{epub_dir}/META-INF/container.xml', 'w', encoding='utf-8') as f:
    f.write('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')

uid = str(uuid.uuid4())

img_tags = []
for i in range(len(downloaded)):
    ext = os.path.splitext(downloaded[i])[1][1:]
    label = '封面' if i == 0 else f'插图{i}'
    img_tags.append(f'    <p><img src="images/fig_{i:03d}.{ext}" alt="{label}"/></p>')

dl_html = ''
if full['downloads']:
    items = '\n'.join(f'      <li><a href="{d["href"]}">{d["text"][:60]}</a></li>' for d in full['downloads'][:8])
    dl_html = f'<h2>下载链接</h2><ul>\n{items}\n</ul>'
    if full.get('password'):
        dl_html += f'\n<p>提取信息: {full["password"][:120]}</p>'

paras_html = '\n'.join(f'    <p>{html_escape(p)}</p>' for p in paragraphs[:600])

content = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{html_escape(title)}</title><meta charset="utf-8"/></head>
<body><h1>{html_escape(title)}</h1>
<p><em>Natsume Agent | {time.strftime('%Y-%m-%d %H:%M')} | {len(text)} 字 | {len(downloaded)} 图</em></p>
{dl_html}<hr/>
{paras_html}
{chr(10).join(img_tags)}
</body></html>'''
with open(f'{epub_dir}/OEBPS/content.xhtml', 'w', encoding='utf-8') as f: f.write(content)

img_items = []
for i in range(len(downloaded)):
    ext = os.path.splitext(downloaded[i])[1][1:]
    mt = {'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png','gif':'image/gif','webp':'image/webp'}.get(ext,'image/jpeg')
    img_items.append(f'    <item id="img{i}" href="images/fig_{i:03d}.{ext}" media-type="{mt}"/>')

opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="book-id" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">{uid}</dc:identifier>
    <dc:title>{html_escape(title)}</dc:title>
    <dc:creator>Natsume Agent</dc:creator>
    <dc:language>zh</dc:language>
    <dc:date>{time.strftime('%Y-%m-%d')}</dc:date>
    <dc:description>{len(text)} 字 {len(downloaded)} 图 {len(full['downloads'])} 下载链接</dc:description>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
{chr(10).join(img_items)}
  </manifest>
  <spine toc="ncx"><itemref idref="content"/></spine>
</package>'''
with open(f'{epub_dir}/OEBPS/content.opf', 'w', encoding='utf-8') as f: f.write(opf)

with open(f'{epub_dir}/OEBPS/toc.ncx', 'w', encoding='utf-8') as f:
    f.write(f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="{uid}"/></head>
<docTitle><text>{html_escape(title)}</text></docTitle>
<navMap><navPoint id="navpoint-1" playOrder="1"><navLabel><text>{html_escape(title)}</text></navLabel><content src="content.xhtml"/></navPoint></navMap>
</ncx>''')

for i in range(len(downloaded)):
    ext = os.path.splitext(downloaded[i])[1][1:]
    shutil.copy2(downloaded[i], f'{epub_dir}/OEBPS/images/fig_{i:03d}.{ext}')

epub_path = f'{OUT}/epub_output/{title_safe}.epub'
with zipfile.ZipFile(epub_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.write(f'{epub_dir}/mimetype', 'mimetype')
    for root, dirs, files in os.walk(epub_dir):
        for fname in files:
            if fname.endswith('.epub'): continue
            zf.write(os.path.join(root, fname), os.path.relpath(os.path.join(root, fname), epub_dir).replace('\\', '/'))

size_kb = os.path.getsize(epub_path) / 1024
total_img_kb = sum(os.path.getsize(ip) for ip in downloaded) / 1024

print(f"\n  EPUB: {epub_path}")
print(f"  Size: {size_kb:.0f} KB (images: {total_img_kb:.0f} KB)")
print(f"  Text: {len(text)} chars in {len(paragraphs)} paragraphs")
print(f"  Images: {len(downloaded)}")

print(f"\n{'=' * 50}")
print("FULL PIPELINE DONE")
print(f"{'=' * 50}")
