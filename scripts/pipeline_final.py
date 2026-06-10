#!/usr/bin/env python
"""Final pipeline — proper EPUB layout from article HTML + images."""

import sys, json, time, urllib.request, websocket, os, base64, re, uuid, shutil, zipfile, requests, glob
from html import escape as html_escape

OUT = 'D:/Natsume_Ai/runtime/browser'

# ===== CDP =====
tabs = json.loads(urllib.request.urlopen('http://127.0.0.1:9222/json', timeout=5).read())
WS = None
for t in tabs:
    if t.get('type') == 'page': WS = t['webSocketDebuggerUrl']; break
if not WS: raise RuntimeError("No Chrome tab")

def cdp(expr, timeout=30):
    ws = websocket.create_connection(WS, timeout=10)
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
        "params": {"expression": expr, "returnByValue": True}}))
    for _ in range(20):
        msg = json.loads(ws.recv())
        if msg.get('id') == 1:
            ws.close()
            return msg['result']['result'].get('value')
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
COOKIE = ''
for _ in range(10):
    msg = json.loads(ws.recv())
    if msg.get('id') == 1:
        for c in msg['result']['cookies']:
            if c['name'] == 'token':
                COOKIE = f"{c['name']}={c['value']}"
        break
ws.close()
REQ_HEADERS = {
    'Cookie': COOKIE,
    'Referer': 'https://www.lightnovel.fun/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}

print(f"CDP OK. Auth: {'YES' if COOKIE else 'NO'}\n")

def download_img(src):
    try:
        r = requests.get(src, headers=REQ_HEADERS, timeout=30)
        if r.status_code == 200 and len(r.content) > 500:
            ext = os.path.splitext(src.split('?')[0])[1][:5]
            if ext not in ('.jpg','.jpeg','.png','.gif','.webp'): ext = '.jpg'
            return (ext, r.content)
    except:
        pass
    return None


# ============================================================
# STEP 1+2: Scrape + Rank
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
      var replyCount = document.querySelectorAll('.comment-list-item').length;
      return JSON.stringify({
        title: document.title.split('-')[0].trim().slice(0,80),
        bodyLen: body.length,
        artImgs: bigImgs,
        replyCount: replyCount,
        hasCoin: body.includes('投币')
      });
    })()"""))
    enriched.append({**c, **info})
    print(f"      {info['title'][:40]} | body={info['bodyLen']} imgs={info['artImgs']} replies={info['replyCount']}")

scored = []
for item in enriched:
    s = min(item['bodyLen']/500, 40) + item['artImgs'] * 8 + item['replyCount'] * 2
    if item['hasCoin']: s += 5
    scored.append((s, item))
scored.sort(key=lambda x: x[0], reverse=True)

for rank, (s, item) in enumerate(scored[:3], 1):
    print(f"  #{rank} s={s:.0f} {item['title'][:45]}")
selected = scored[0][1]
print(f"\n  Selected: {selected['title'][:60]}")


# ============================================================
# STEP 3: Full content + images
# ============================================================
print(f"\n{'=' * 50}")
print("STEP 3: Content + Images")
nav(f"https://www.lightnovel.fun/cn/detail/{selected['id']}", 6)

# Scroll for lazy images
cdp("window.scrollTo(0,0)")
time.sleep(1)
for y in range(2000, 150000, 2000):
    cdp(f"window.scrollTo(0,{y})")
    time.sleep(0.3)
cdp("window.scrollTo(0,0)")
time.sleep(2)

full = json.loads(cdp("""(function() {
  var art = document.querySelector('.article-content');

  // Get CLEAN HTML — remove inline styles but keep structure
  var cleanHTML = '';
  if (art) {
    cleanHTML = art.innerHTML
      .replace(/ style="[^"]*"/g, '')
      .replace(/ loading="[^"]*"/g, '')
      .replace(/ img-width="[^"]*"/g, '')
      .replace(/ img-height="[^"]*"/g, '');
  }

  // All illustration images with their src
  var imgs = [];
  (art||document).querySelectorAll('img').forEach(function(img) {
    var src = img.src || '';
    if (src.includes('res.lightnovel.fun/images/') && !src.includes('smiley'))
      imgs.push({src: src});
  });

  // Reply chapters
  var replies = [];
  document.querySelectorAll('.comment-list-item').forEach(function(el) {
    var text = el.innerText.trim();
    if (text.length > 20) {
      // Clean reply HTML
      var html = el.innerHTML.replace(/ style="[^"]*"/g, '').replace(/ loading="[^"]*"/g, '');
      replies.push({text: text, html: html});
      if (replies.length >= 20) return;
    }
  });

  // Download links
  var dls = [];
  document.querySelectorAll('a').forEach(function(a) {
    if (a.href && (a.href.includes('pan.baidu')||a.href.includes('lanzou')||a.href.includes('.epub')))
      dls.push({text: a.textContent.trim().slice(0,60), href: a.href});
  });

  // Author info from article header
  var body = document.body.innerText;
  var authorMatch = body.match(/作者[：:]\\s*(.+)/);
  var author = authorMatch ? authorMatch[1].split(/[\\n，,]/)[0].trim().slice(0, 40) : '';

  return JSON.stringify({
    title: document.title.split('-')[0].trim(),
    author: author,
    cleanHTML: cleanHTML,
    images: imgs,
    replies: replies,
    downloads: dls,
    bodyText: (art||document.body).innerText
  });
})()"""))

print(f"  HTML: {len(full['cleanHTML'])} chars")
print(f"  Images: {len(full['images'])}")
print(f"  Replies: {len(full['replies'])}")
print(f"  Author: {full['author']}")

# Download images
os.makedirs(f'{OUT}/novel_images', exist_ok=True)
for old in glob.glob(f'{OUT}/novel_images/*'):
    try: os.remove(old)
    except: pass

img_map = {}  # original_src -> local_filename
for idx, img in enumerate(full['images']):
    src = img['src']
    result = download_img(src)
    if result:
        ext, data = result
        fname = f'img_{idx:03d}{ext}'
        fpath = os.path.join(OUT, 'novel_images', fname)
        with open(fpath, 'wb') as f: f.write(data)
        img_map[src] = (fname, ext, len(data))
        print(f"  [{idx:02d}] {fname} {len(data)//1024}KB", flush=True)
    else:
        print(f"  [{idx:02d}] FAIL", flush=True)

print(f"\n  Downloaded: {len(img_map)}/{len(full['images'])}")


# ============================================================
# STEP 4: Build EPUB with proper layout
# ============================================================
print(f"\n{'=' * 50}")
print("STEP 4: EPUB with layout")

title = full['title'][:60]
author = full['author'] or 'Unknown'
title_safe = re.sub(r'[\\/:*?"<>|]', '-', title)[:40]

epub_dir = f'{OUT}/epub_output'
for d in [f'{epub_dir}/OEBPS/images', f'{epub_dir}/META-INF']:
    os.makedirs(d, exist_ok=True)

# Build clean article HTML with local image references
article_html = full['cleanHTML']
for orig_src, (fname, ext, _) in img_map.items():
    # Match the original src in various forms (url-encoded, etc)
    # Simple approach: replace the basename
    article_html = re.sub(
        re.escape(orig_src.split('?')[0]) + r'[^"\'>]*',
        f'images/{fname}',
        article_html
    )
    article_html = re.sub(
        re.escape(orig_src),
        f'images/{fname}',
        article_html
    )

# Build reply chapters HTML
reply_html = ''
if full['replies']:
    reply_html += '<h2>评论区章节</h2>\n'
    for i, r in enumerate(full['replies'][:15]):
        reply_html += f'<section class="chapter"><h3>回复 {i+1}</h3>\n'
        reply_html += f'<div>{r["html"]}</div>\n</section>\n'

# CSS
css = '''
body { font-family: "Microsoft YaHei", "SimSun", serif; line-height: 1.8; margin: 1em; }
h1 { text-align: center; font-size: 1.5em; margin-bottom: 0.5em; }
h2 { font-size: 1.3em; margin-top: 2em; border-bottom: 1px solid #ccc; }
h3 { font-size: 1.1em; margin-top: 1.5em; color: #555; }
p { text-indent: 2em; margin: 0.3em 0; }
img { max-width: 100%; height: auto; display: block; margin: 1em auto; }
.chapter { margin: 1em 0; padding: 0.5em; border-left: 3px solid #ddd; }
.meta { text-align: center; font-size: 0.85em; color: #888; margin: 1em 0; }
b, strong { color: #333; }
'''

content_xhtml = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
  <title>{html_escape(title)}</title>
  <meta charset="utf-8"/>
  <style type="text/css">{css}</style>
</head>
<body>
  <h1>{html_escape(title)}</h1>
  <p class="meta">作者: {html_escape(author)} | Natsume Agent 制作 | {time.strftime('%Y-%m-%d')}</p>
  <p class="meta">轻之国度 lightnovel.fun | 仅供个人学习交流</p>

  <hr/>

  <div class="article-content">
    {article_html}
  </div>

  <hr/>
  {reply_html}
</body>
</html>'''

with open(f'{epub_dir}/OEBPS/content.xhtml', 'w', encoding='utf-8') as f:
    f.write(content_xhtml)

# Standard EPUB scaffolding
with open(f'{epub_dir}/mimetype', 'w') as f:
    f.write('application/epub+zip')

with open(f'{epub_dir}/META-INF/container.xml', 'w', encoding='utf-8') as f:
    f.write('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')

uid = str(uuid.uuid4())

img_items = []
for i, (orig_src, (fname, ext, _)) in enumerate(img_map.items()):
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
    <dc:description>由 Natsume Agent 从轻之国度自动抓取封装。{len(img_map)} 张插图。仅供个人学习交流。</dc:description>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
{chr(10).join(img_items)}
  </manifest>
  <spine toc="ncx"><itemref idref="content"/></spine>
</package>'''
with open(f'{epub_dir}/OEBPS/content.opf', 'w', encoding='utf-8') as f:
    f.write(opf)

with open(f'{epub_dir}/OEBPS/toc.ncx', 'w', encoding='utf-8') as f:
    f.write(f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="{uid}"/></head>
<docTitle><text>{html_escape(title)}</text></docTitle>
<navMap><navPoint id="navpoint-1" playOrder="1"><navLabel><text>{html_escape(title)}</text></navLabel><content src="content.xhtml"/></navPoint></navMap>
</ncx>''')

# Copy images
for i, (orig_src, (fname, ext, _)) in enumerate(img_map.items()):
    src_path = os.path.join(OUT, 'novel_images', fname)
    shutil.copy2(src_path, f'{epub_dir}/OEBPS/images/{fname}')

# ZIP
epub_path = f'{OUT}/epub_output/{title_safe}.epub'
with zipfile.ZipFile(epub_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.write(f'{epub_dir}/mimetype', 'mimetype')
    for root, dirs, files in os.walk(epub_dir):
        for fname in files:
            if fname.endswith('.epub'): continue
            zf.write(os.path.join(root, fname), os.path.relpath(os.path.join(root, fname), epub_dir).replace('\\', '/'))

size_kb = os.path.getsize(epub_path) / 1024
total_img_kb = sum(sz for _, _, sz in img_map.values()) / 1024
chars = len(full['bodyText'])

print(f"\n  EPUB : {epub_path}")
print(f"  Size : {size_kb:.0f} KB (images: {total_img_kb:.0f} KB)")
print(f"  Text : {chars} chars")
print(f"  Images: {len(img_map)}")
print(f"  Replies: {len(full['replies'])} chapters in comment section")

print(f"\n{'=' * 50}")
print(f"COMPLETE")
print(f"{'=' * 50}")
print(f"  #{scored[0][1]['id']} - {full['title'][:40]}")
print(f"  {chars} 字 | {len(img_map)} 图 | epub {size_kb:.0f}KB")
