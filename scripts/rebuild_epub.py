#!/usr/bin/env python
"""Rebuild EPUB — uses existing text + images on disk, no dependency on stale data."""

import os, re, uuid, time, shutil, zipfile, glob, json
from html import escape as html_escape

OUT = 'D:/Natsume_Ai/runtime/browser'
IMG_DIR = os.path.join(OUT, 'novel_images')
TEXT_FILE = os.path.join(OUT, 'novel_full_text.txt')
EPUB_DIR = os.path.join(OUT, 'epub_output')

# Load text
with open(TEXT_FILE, encoding='utf-8') as f:
    full_text = f.read()

# Get title from CDP browser (page is already loaded with correct novel)
title = 'Light Novel'
author = 'Unknown'
try:
    import urllib.request, websocket
    tabs = json.loads(urllib.request.urlopen('http://127.0.0.1:9222/json', timeout=3).read())
    for t in tabs:
        if t.get('type') == 'page' and not t.get('url','').startswith('chrome-'):
            ws_url = t['webSocketDebuggerUrl']
            ws = websocket.create_connection(ws_url, timeout=5)
            js_expr = "JSON.stringify({title:document.title.split('-')[0].trim(),author:(function(){var b=document.body.innerText;var idx=b.indexOf('作者');if(idx<0)idx=b.indexOf('作者');if(idx<0)return'';var rest=b.slice(idx+3);var end=rest.search(/[\\n,、，]/);if(end<0)end=40;return rest.slice(0,end).trim();})()})"
            ws.send(json.dumps({'id':1,'method':'Runtime.evaluate','params':{'expression':js_expr,'returnByValue':True}}))
            for _ in range(8):
                msg = json.loads(ws.recv())
                if msg.get('id') == 1:
                    r = msg.get('result',{}).get('result',{})
                    if 'value' in r:
                        d = json.loads(r['value'])
                        title = d.get('title', title)[:100]
                        author = d.get('author', author) or author
            ws.close()
            break
except Exception as e:
    print(f"  CDP title lookup failed: {e}, using fallback")
    lines = full_text.split('\n')
    for line in lines[:30]:
        if line.strip().startswith('[') and ']' in line and len(line) > 10:
            title = line.strip()[:100]
            break
    for line in lines[:30]:
        m = re.match(r'作者[：:]\s*(.+)', line.strip())
        if m:
            author = m.group(1).split('、')[0].split('/')[0].strip()[:40]
            break

# Description
desc_text = ''
for l in lines:
    l = l.strip()
    if len(l) > 20 and '轻之国度' not in l and '仅供' not in l and '下载后' not in l:
        desc_text = l[:200]
        break

# Images
images = sorted(glob.glob(os.path.join(IMG_DIR, 'img_*.jpg')))
if not images:
    images = sorted(glob.glob(os.path.join(IMG_DIR, '*.jpg')))
cover_img = images[0] if images else None
body_imgs = images[1:] if len(images) > 1 else []

print(f"Title : {title[:60]}")
print(f"Author: {author}")
print(f"Cover : {os.path.basename(cover_img) if cover_img else 'NONE'} ({len(body_imgs)} body imgs)")
print(f"Text  : {len(full_text)} chars")

# ===== BUILD =====
title_safe = re.sub(r'[\\/:*?"<>|]', '-', title)[:50]
build_dir = os.path.join(OUT, 'epub_build')
if os.path.exists(build_dir):
    shutil.rmtree(build_dir, ignore_errors=True)
for d in [f'{build_dir}/OEBPS/images', f'{build_dir}/META-INF']:
    os.makedirs(d, exist_ok=True)
uid = str(uuid.uuid4())

# ---- style.css ----
with open(f'{build_dir}/OEBPS/style.css', 'w', encoding='utf-8') as f:
    f.write('''body { font-family: "Microsoft YaHei","PingFang SC","SimSun",serif; line-height:1.85; margin:0; padding:0; }
.cover { text-align:center; page-break-after:always; padding:2em 1em; }
.cover h1 { font-size:1.5em; margin:0.5em 0; }
.cover .author { font-size:1.1em; color:#555; }
.cover img { max-width:100%; height:auto; margin:1em auto; }
.content { padding:0 0.5em; }
.content h1 { text-align:center; font-size:1.4em; margin:1em 0 .3em 0; }
.content p { text-indent:2em; margin:0.3em 0; }
.content img { max-width:100%; height:auto; display:block; margin:1em auto; }
.content .illustration { text-indent:0; text-align:center; }
.meta { text-align:center; font-size:0.85em; color:#888; margin:0.5em 0 2em 0; }
.end { text-align:center; font-size:0.85em; color:#aaa; margin:3em 0 1em 0; border-top:1px solid #eee; padding-top:1em; }
''')

# ---- cover.xhtml ----
cover_ext = os.path.splitext(cover_img)[1] if cover_img else '.jpg'
cover_xhtml = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{html_escape(title)}</title><meta charset="utf-8"/><link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>
<div class="cover">
  <h1>{html_escape(title)}</h1>
  <p class="author">作者: {html_escape(author)}</p>
  <p class="meta">轻之国度 lightnovel.fun</p>
  <img src="images/cover{cover_ext}" alt="封面"/>
</div>
</body>
</html>'''
with open(f'{build_dir}/OEBPS/cover.xhtml', 'w', encoding='utf-8') as f:
    f.write(cover_xhtml)

# ---- content.xhtml ----
paras = []
for line in lines:
    s = line.strip()
    if not s: continue
    if any(kw in s for kw in ['仅供个人学习', '下载后请在', '禁作商业', '转载需经', '请尊重扫描',
                               '轻之国度 http', '未经允许严禁', '请尊重图源']):
        continue
    paras.append(s)

# Skip header metadata to find story body
story_i = 0
for i, p in enumerate(paras):
    if len(p) > 50 and '作者' not in p and '插图' not in p and '翻译' not in p \
       and '校对' not in p and '图源' not in p \
       and not re.match(r'^-{10,}', p):
        story_i = i
        break

content_lines = []
for p in paras[story_i:]:
    if p.startswith('第') and ('话' in p[:10] or '章' in p[:10]):
        content_lines.append(f'    <h2>{html_escape(p)}</h2>')
    else:
        content_lines.append(f'    <p>{html_escape(p)}</p>')

# Insert body images evenly
img_tags = []
for i, ip in enumerate(body_imgs):
    ext = os.path.splitext(ip)[1]
    img_tags.append((i, f'    <p class="illustration"><img src="images/img_{i:03d}{ext}" alt="插图 {i+1}"/></p>'))

interval = max(1, len(content_lines) // max(1, len(img_tags) + 1))
final_lines = []
tag_idx = 0
for i, cl in enumerate(content_lines):
    final_lines.append(cl)
    if (i + 1) % interval == 0 and tag_idx < len(img_tags):
        final_lines.append(img_tags[tag_idx][1])
        tag_idx += 1
while tag_idx < len(img_tags):
    final_lines.append(img_tags[tag_idx][1])
    tag_idx += 1

content_xhtml = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{html_escape(title)}</title><meta charset="utf-8"/><link rel="stylesheet" type="text/css" href="style.css"/></head>
<body class="content">
<h1>{html_escape(title)}</h1>
<p class="meta">作者: {html_escape(author)} | Natsume Agent | {time.strftime('%Y-%m-%d')}</p>
<hr/>
{chr(10).join(final_lines)}
<hr/>
<p class="end">— Natsume Agent 自动抓取封装 —</p>
</body>
</html>'''
with open(f'{build_dir}/OEBPS/content.xhtml', 'w', encoding='utf-8') as f:
    f.write(content_xhtml)

# ---- content.opf ----
cover_ext = os.path.splitext(cover_img)[1] if cover_img else '.jpg'
cover_item = f'    <item id="cover-image" href="images/cover{cover_ext}" media-type="image/jpeg" properties="cover-image"/>' if cover_img else ''

body_items = []
for i, ip in enumerate(body_imgs):
    ext = os.path.splitext(ip)[1]
    mt = {'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png','gif':'image/gif','webp':'image/webp'}.get(ext,'image/jpeg')
    body_items.append(f'    <item id="img{i}" href="images/img_{i:03d}{ext}" media-type="{mt}"/>')

opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="book-id" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">{uid}</dc:identifier>
    <dc:title>{html_escape(title)}</dc:title>
    <dc:creator>{html_escape(author)}</dc:creator>
    <dc:publisher>lightnovel.fun</dc:publisher>
    <dc:language>zh</dc:language>
    <dc:date>{time.strftime('%Y-%m-%d')}</dc:date>
    <dc:description>{html_escape(desc_text[:200])}</dc:description>
    <meta name="cover" content="cover-image"/>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="style" href="style.css" media-type="text/css"/>
    <item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>
    <item id="content-page" href="content.xhtml" media-type="application/xhtml+xml"/>
{cover_item}
{chr(10).join(body_items)}
  </manifest>
  <spine toc="ncx">
    <itemref idref="cover-page"/>
    <itemref idref="content-page"/>
  </spine>
  <guide>
    <reference type="cover" title="Cover" href="cover.xhtml"/>
  </guide>
</package>'''
with open(f'{build_dir}/OEBPS/content.opf', 'w', encoding='utf-8') as f:
    f.write(opf)

# ---- toc.ncx ----
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

# ---- mimetype + container ----
with open(f'{build_dir}/mimetype', 'w') as f:
    f.write('application/epub+zip')
with open(f'{build_dir}/META-INF/container.xml', 'w', encoding='utf-8') as f:
    f.write('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')

# ---- Copy assets ----
if cover_img:
    shutil.copy2(cover_img, f'{build_dir}/OEBPS/images/cover{cover_ext}')
for i, ip in enumerate(body_imgs):
    ext = os.path.splitext(ip)[1]
    shutil.copy2(ip, f'{build_dir}/OEBPS/images/img_{i:03d}{ext}')

# ---- ZIP ----
epub_path = f'{EPUB_DIR}/{title_safe}.epub'
with zipfile.ZipFile(epub_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.write(f'{build_dir}/mimetype', 'mimetype', compress_type=zipfile.ZIP_STORED)
    for root, dirs, files in os.walk(build_dir):
        for fname in files:
            if fname == 'mimetype': continue
            zf.write(os.path.join(root, fname), os.path.relpath(os.path.join(root, fname), build_dir).replace('\\', '/'))

size_kb = os.path.getsize(epub_path) / 1024
print(f"\nEPUB: {epub_path}")
print(f"Size: {size_kb:.0f} KB | Cover: YES | Body imgs: {len(body_imgs)} | Paras: {len(content_lines)}")
print("Done!")
