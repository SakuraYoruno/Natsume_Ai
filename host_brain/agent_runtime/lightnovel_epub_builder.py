"""Native lightnovel.fun EPUB packaging helper for production WorkOrders."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
import http.client
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

import requests

from host_brain.agent_runtime.artifact_store import DEFAULT_CODEX_RUNS_ROOT
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.agent_runtime.work_order import AgentWorkOrder

logger = logging.getLogger(__name__)


LIGHTNOVEL_FUN_DOMAIN = "lightnovel.fun"
DEFAULT_CONTENT_SELECTOR = ".article-content"
DEFAULT_TITLE = "lightnovel"


def _load_bs4() -> tuple[Any, Any, Any]:
    try:
        from bs4 import BeautifulSoup, NavigableString, Tag  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        raise RuntimeError("beautifulsoup4 is required for lightnovel EPUB building") from exc
    return BeautifulSoup, NavigableString, Tag


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_url(value: Any) -> str:
    return html.unescape(str(value or "").strip()).replace("\\/", "/")


def _safe_name(value: str, *, default: str = DEFAULT_TITLE) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", "_", str(value or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name or default)[:140]


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _is_lightnovel_fun_url(url: str) -> bool:
    host = urlparse(str(url or "")).netloc.lower()
    return host.endswith(LIGHTNOVEL_FUN_DOMAIN)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _coerce_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        rows = payload.get("recommendations") or payload.get("items") or payload.get("value") or []
    else:
        rows = []
    return [dict(item) for item in rows if isinstance(item, Mapping)]


def _cn_index(value: str) -> int | None:
    normalized = str(value or "")
    for marker, index in (
        ("第一", 1),
        ("第1", 1),
        ("1", 1),
        ("第二", 2),
        ("第2", 2),
        ("2", 2),
        ("第三", 3),
        ("第3", 3),
        ("3", 3),
        ("第四", 4),
        ("第4", 4),
        ("4", 4),
        ("第五", 5),
        ("第5", 5),
        ("5", 5),
    ):
        if marker in normalized:
            return index
    return None


def _resolve_recommendation_index(input_pack: Mapping[str, Any]) -> int | None:
    for key in ("recommendation_index", "selected_index", "selected_recommendation_index"):
        raw = input_pack.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            index = int(raw)
            return index if index > 0 else None
        except (TypeError, ValueError):
            parsed = _cn_index(str(raw))
            if parsed:
                return parsed
    return _cn_index(str(input_pack.get("user_request") or ""))


def _candidate_recommendations_paths(input_pack: Mapping[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    raw_path = str(input_pack.get("recommendations_path") or "").strip()
    if raw_path:
        candidates.append(Path(raw_path))
    for env_name in (
        "HOST_LIGHTNOVEL_RECOMMENDER_LAST_RECOMMENDATIONS_PATH",
        "HOST_LIGHTNOVEL_RECOMMENDATIONS_PATH",
    ):
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            candidates.append(Path(value))
    if DEFAULT_CODEX_RUNS_ROOT.exists():
        candidates.extend(DEFAULT_CODEX_RUNS_ROOT.rglob("recommendations.json"))

    seen: set[str] = set()
    existing: list[Path] = []
    for path in candidates:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and path.is_file():
            existing.append(path.expanduser())
    existing.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return existing


def _resolve_recommendation_url(input_pack: Mapping[str, Any]) -> tuple[str, dict[str, Any], str]:
    selected = input_pack.get("selected_recommendation")
    if isinstance(selected, Mapping):
        item = dict(selected)
        url = str(item.get("detail_url") or item.get("url") or "").strip()
        if url:
            return url, item, "selected_recommendation"

    for path in _candidate_recommendations_paths(input_pack):
        payload = _read_json(path)
        items = _coerce_items(payload)
        index = _resolve_recommendation_index(input_pack) or 1
        if 1 <= index <= len(items):
            item = items[index - 1]
            url = str(item.get("detail_url") or item.get("url") or "").strip()
            if url:
                return url, item, f"recommendations_path:{index}:{path}"

    for url in input_pack.get("urls") or []:
        value = str(url or "").strip()
        if value:
            return value, {}, "urls"
    return "", {}, "missing"


def resolve_lightnovel_source(input_pack: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the selected lightnovel source from WorkOrder input_pack."""
    url, item, source = _resolve_recommendation_url(input_pack)
    html_path = ""
    for raw in input_pack.get("source_paths") or []:
        value = str(raw or "").strip()
        if value.lower().endswith((".html", ".htm")):
            html_path = value
            break
    if not html_path:
        html_path = str(input_pack.get("html_path") or "").strip()
    return {
        "url": url,
        "recommendation": item,
        "source": source,
        "html_path": html_path,
    }


def _fetch_html(url: str) -> str:
    response = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.content
    charset = response.encoding or response.apparent_encoding or "utf-8"
    return data.decode(charset, errors="replace")


def _fetch_html_playwright(url: str, *, timeout: float = 90) -> str:
    """Capture SPA-rendered HTML through the shared Playwright runtime."""
    if not _is_lightnovel_fun_url(url):
        return _fetch_html(url)

    try:
        from host_brain.agent_runtime.browser_automation_service import get_browser_automation_service  # noqa: PLC0415

        browser = get_browser_automation_service(session_id="natsume_lightnovel")
        html_val = browser.open_wait_eval(
            url,
            """async () => {
  for (const y of [2000, 5000, 10000, 20000, 40000, 80000]) {
    window.scrollTo(0, y);
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  window.scrollTo(0, 0);
  await new Promise(resolve => setTimeout(resolve, 500));
  return document.documentElement.outerHTML;
}""",
            wait_expression="document.body && document.body.innerText && document.body.innerText.length > 1000",
            open_timeout_ms=int(timeout * 1000),
            wait_timeout_ms=int(timeout * 1000),
            eval_timeout_ms=int(timeout * 1000),
        )
        if html_val and len(html_val) > 5000:
            return html_val
    except Exception as exc:
        logger.warning("Playwright fetch failed for %s: %s", url, exc)
    return _fetch_html(url)


def _read_source_html(*, url: str, html_path: str) -> tuple[str, str]:
    if html_path:
        path = Path(html_path)
        return path.read_text(encoding="utf-8-sig"), str(path)
    if not url:
        raise ValueError("No source URL or HTML path provided")
    if not _is_lightnovel_fun_url(url):
        raise ValueError(f"Unsupported EPUB source domain: {url}")
    # Try Playwright first for SPA pages (lightnovel.fun is Nuxt.js SPA)
    html_text = _fetch_html_playwright(url)
    return html_text, url


def _extract_title(soup: BeautifulSoup, content: Tag, recommendation: Mapping[str, Any]) -> str:
    for key in ("title", "name"):
        value = str(recommendation.get(key) or "").strip()
        if value:
            return value
    heading = soup.select_one("h1,h2,.article-title")
    if heading:
        value = heading.get_text(" ", strip=True)
        if value:
            return value
    first = content.get_text("\n", strip=True).splitlines()
    return str(first[0]).strip() if first else DEFAULT_TITLE


def _meta_from_text(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for label, key in (("作者", "author"), ("插画", "illustrator"), ("翻译", "translator"), ("校对", "proofreader"), ("图源", "image_source")):
        match = re.search(rf"{re.escape(label)}[：:]\s*([^\n\r]+)", text)
        if match:
            meta[key] = match.group(1).strip()
    return meta


def _guess_suffix(url: str, content_type: str = "") -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return suffix
    guessed = mimetypes.guess_extension(str(content_type or "").split(";", 1)[0].strip())
    return guessed if guessed in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"


def _download_bytes(url: str, *, referer: str) -> tuple[bytes, str]:
    last_error: Exception | None = None
    partial = b""
    content_type = ""
    for attempt in range(1, 5):
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": referer,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.content
            content_type = response.headers.get("Content-Type", "")
            return data, content_type
        except http.client.IncompleteRead as exc:
            last_error = exc
            partial = bytes(exc.partial or b"")
            time.sleep(0.5 * attempt)
        except Exception as exc:
            last_error = exc
            time.sleep(0.5 * attempt)
    if partial:
        return partial, content_type
    curl_exe = shutil.which("curl.exe") or shutil.which("curl")
    if curl_exe:
        try:
            completed = subprocess.run(
                [
                    curl_exe,
                    "--silent",
                    "--show-error",
                    "--location",
                    "--retry",
                    "3",
                    "--connect-timeout",
                    "20",
                    "--max-time",
                    "90",
                    "-A",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "-e",
                    referer,
                    url,
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
            if completed.stdout:
                return bytes(completed.stdout), content_type
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"image download failed: {url}: {last_error}") from last_error


def _load_image_bytes(src: str, *, base_url: str) -> tuple[bytes, str, str]:
    src = _clean_url(src)
    if src.startswith("data:"):
        header, raw_data = src.split(",", 1)
        content_type = header.split(";", 1)[0].removeprefix("data:") or "image/png"
        return base64.b64decode(raw_data), content_type, ".png"
    absolute = urljoin(base_url or "", src)
    parsed = urlparse(absolute)
    if parsed.scheme in {"http", "https"}:
        data, content_type = _download_bytes(absolute, referer=base_url or absolute)
        return data, content_type, _guess_suffix(absolute, content_type)
    path = Path(absolute if parsed.scheme != "file" else parsed.path)
    data = path.read_bytes()
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return data, content_type, _guess_suffix(str(path), content_type)


def _source_images(content: Tag, *, base_url: str) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    seen: set[str] = set()
    for img in content.select("img"):
        src = _clean_url(img.get("src") or img.get("data-src") or "")
        if not src or src in seen:
            continue
        seen.add(src)
        images.append({"src": src, "absolute_url": urljoin(base_url or "", src), "alt": str(img.get("alt") or "")})
    return images


def _extract_blocks(content: Tag, image_map: Mapping[str, str]) -> list[dict[str, str]]:
    _beautiful_soup, navigable_string, tag_type = _load_bs4()
    blocks: list[dict[str, str]] = []
    pending: list[str] = []

    def flush() -> None:
        text = "".join(pending).strip()
        pending.clear()
        if not text:
            return
        for line in re.split(r"\n+", text):
            normalized = re.sub(r"[ \t\u3000]+", " ", line).strip()
            if normalized:
                blocks.append({"type": "p", "text": normalized})

    def visit(child: object) -> None:
        if isinstance(child, navigable_string):
            pending.append(str(child))
            return
        if not isinstance(child, tag_type):
            return
        if child.name == "br":
            pending.append("\n")
            flush()
            return
        if child.name == "img":
            flush()
            src = _clean_url(child.get("src") or child.get("data-src") or "")
            if src in image_map:
                blocks.append({"type": "img", "src": image_map[src], "source_src": src, "alt": str(child.get("alt") or "")})
            for sub in child.children:
                visit(sub)
            return
        for sub in child.children:
            visit(sub)
        if child.name in {"p", "div"}:
            pending.append("\n")
            flush()

    for item in content.children:
        visit(item)
    flush()
    return blocks


def _chapter_heading_kind(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if not normalized or len(normalized) > 110:
        return None
    if re.match(
        r"^(序章|终章|終章|尾声|尾聲|後記|后记|あとがき|特典|プロローグ|エピローグ)(?:$|[\s　].{0,90}$)",
        normalized,
    ):
        return "named"
    if re.match(r"^第[一二三四五六七八九十百千万千\d]+[章話话節节回](?:$|[\s　].{0,90}$)", normalized):
        return "cjk_numbered"
    if re.match(r"^[1-9]\d?[\.．、]\s*\S.{0,80}$", normalized):
        return "arabic_numbered"
    return None


def _strip_toc_heading_run(blocks: list[dict[str, str]]) -> list[dict[str, str]]:
    """Remove page TOC entries while preserving the first chapter title.

    lightnovel.fun articles often contain a plain-text TOC:

        目录
        序章 ...
        1. ...
        2. ...

    Those entries are not body content. Keeping them creates a fake first chapter
    that contains the whole TOC before the actual prose. We replace the TOC run
    with a single synthetic first heading so prologue text still has a chapter
    title even when the source does not repeat it after the TOC.
    """
    stripped: list[dict[str, str]] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        text = str(block.get("text") or "").strip() if block.get("type") == "p" else ""
        if text in {"目录", "目錄", "目次", "CONTENTS", "Contents"}:
            j = i + 1
            toc_titles: list[str] = []
            while j < len(blocks):
                candidate = blocks[j]
                candidate_text = str(candidate.get("text") or "").strip() if candidate.get("type") == "p" else ""
                if not candidate_text or _chapter_heading_kind(candidate_text) is None:
                    break
                toc_titles.append(candidate_text)
                j += 1
            if len(toc_titles) >= 2:
                i = j
                continue
        stripped.append(block)
        i += 1
    return stripped


def _split_chapters(blocks: list[dict[str, str]]) -> list[dict[str, Any]]:
    blocks = _strip_toc_heading_run(blocks)
    chapters: list[dict[str, Any]] = []
    current: dict[str, Any] = {"id": "frontmatter", "title": "版权信息与插图", "blocks": []}
    chapter_index = 1
    for block in blocks:
        if block.get("type") == "p" and _chapter_heading_kind(block.get("text", "")) is not None:
            title = block["text"]
            if current["blocks"]:
                chapters.append(current)
            current = {"id": f"chapter_{chapter_index:02d}", "title": title, "blocks": [block]}
            chapter_index += 1
            continue
        current["blocks"].append(block)
    if current["blocks"]:
        chapters.append(current)
    if len(chapters) == 1 and chapters[0].get("id") == "frontmatter":
        blocks_only = list(chapters[0].get("blocks") or [])
        if blocks_only:
            text_chars = sum(len(str(b.get("text") or "")) for b in blocks_only if b.get("type") == "p")
            if text_chars >= 1200:
                chapters = [{"id": "chapter_01", "title": "正文", "blocks": blocks_only}]
    return chapters


def _xhtml_page(title: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN">
<head>
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" type="text/css" href="../styles/style.css" />
</head>
<body>
{body}
</body>
</html>
"""


def _write_chapters(chapters: list[dict[str, Any]], oebps: Path) -> list[dict[str, str]]:
    written: list[dict[str, str]] = []
    text_dir = oebps / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    for chapter in chapters:
        chapter_id = str(chapter["id"])
        title = str(chapter["title"])
        body_parts = [f'<section epub:type="chapter"><h1>{html.escape(title)}</h1>']
        for block in chapter["blocks"]:
            if block.get("type") == "img":
                body_parts.append(
                    f'<figure><img src="{html.escape("../" + block["src"])}" alt="{html.escape(block.get("alt") or "")}" /></figure>'
                )
            else:
                text = str(block.get("text") or "")
                if text == title:
                    continue
                body_parts.append(f"<p>{html.escape(text)}</p>")
        body_parts.append("</section>")
        rel = f"text/{chapter_id}.xhtml"
        (oebps / rel).write_text(_xhtml_page(title, "\n".join(body_parts)), encoding="utf-8")
        written.append({"id": chapter_id, "title": title, "href": rel})
    return written


def _write_epub_container(epub_dir: Path) -> None:
    (epub_dir / "META-INF").mkdir(parents=True, exist_ok=True)
    (epub_dir / "mimetype").write_text("application/epub+zip", encoding="ascii")
    (epub_dir / "META-INF" / "container.xml").write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
""",
        encoding="utf-8",
    )


def _write_package_files(
    *,
    oebps: Path,
    title: str,
    source_url: str,
    metadata: Mapping[str, str],
    chapters: list[dict[str, str]],
    images: list[dict[str, Any]],
    built_at: str,
) -> None:
    (oebps / "styles").mkdir(parents=True, exist_ok=True)
    (oebps / "styles" / "style.css").write_text(
        "\n".join(
            [
                "body { font-family: serif; line-height: 1.75; margin: 5%; }",
                "h1 { font-size: 1.35em; line-height: 1.4; margin: 1.2em 0 0.8em; }",
                "p { text-indent: 2em; margin: 0.35em 0; }",
                "figure { margin: 1.2em 0; text-align: center; }",
                "img { max-width: 100%; height: auto; }",
            ]
        ),
        encoding="utf-8",
    )
    nav_items = "\n".join(
        f'<li><a href="{html.escape(item["href"])}">{html.escape(item["title"])}</a></li>' for item in chapters
    )
    (oebps / "nav.xhtml").write_text(
        _xhtml_page(title, f'<nav epub:type="toc" id="toc"><h1>目录</h1><ol>{nav_items}</ol></nav>'),
        encoding="utf-8",
    )
    nav_points = "\n".join(
        f'<navPoint id="navPoint-{index}" playOrder="{index}"><navLabel><text>{html.escape(item["title"])}</text></navLabel><content src="{html.escape(item["href"])}"/></navPoint>'
        for index, item in enumerate(chapters, 1)
    )
    book_id = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, source_url or title))
    (oebps / "toc.ncx").write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{book_id}"/></head>
  <docTitle><text>{html.escape(title)}</text></docTitle>
  <navMap>{nav_points}</navMap>
</ncx>
""",
        encoding="utf-8",
    )

    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="style" href="styles/style.css" media-type="text/css"/>',
    ]
    spine = []
    for item in chapters:
        manifest.append(f'<item id="{item["id"]}" href="{item["href"]}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="{item["id"]}"/>')
    for index, image in enumerate(images, 1):
        media = str(image.get("content_type") or mimetypes.guess_type(str(image.get("file") or ""))[0] or "image/jpeg")
        props = ' properties="cover-image"' if index == 1 else ""
        manifest.append(f'<item id="img{index:02d}" href="{html.escape(str(image["file"]))}" media-type="{html.escape(media)}"{props}/>')
    creator = metadata.get("author") or ""
    publisher = "lightnovel.fun / 轻之国度"
    (oebps / "content.opf").write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{book_id}</dc:identifier>
    <dc:title>{html.escape(title)}</dc:title>
    <dc:creator>{html.escape(creator)}</dc:creator>
    <dc:language>zh-CN</dc:language>
    <dc:publisher>{html.escape(publisher)}</dc:publisher>
    <dc:source>{html.escape(source_url)}</dc:source>
    <meta property="dcterms:modified">{html.escape(built_at)}</meta>
    <meta name="cover" content="img01"/>
  </metadata>
  <manifest>
    {chr(10).join(manifest)}
  </manifest>
  <spine toc="ncx">
    {chr(10).join(spine)}
  </spine>
</package>
""",
        encoding="utf-8",
    )


def _zip_epub(epub_dir: Path, epub_path: Path) -> None:
    if epub_path.exists():
        epub_path.unlink()
    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.write(epub_dir / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(epub_dir.rglob("*")):
            if path.is_file() and path.name != "mimetype":
                zf.write(path, path.relative_to(epub_dir).as_posix(), compress_type=zipfile.ZIP_DEFLATED)


def _image_context(blocks: list[dict[str, str]], index: int) -> dict[str, str]:
    prev_text = ""
    next_text = ""
    for cursor in range(index - 1, -1, -1):
        if blocks[cursor].get("type") == "p":
            prev_text = blocks[cursor].get("text", "")[:120]
            break
    for cursor in range(index + 1, len(blocks)):
        if blocks[cursor].get("type") == "p":
            next_text = blocks[cursor].get("text", "")[:120]
            break
    return {"previous_text": prev_text, "next_text": next_text}


def _validate_epub(epub_path: Path, epub_dir: Path, chapter_count: int) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    with zipfile.ZipFile(epub_path) as zf:
        names = zf.namelist()
        checks.append({"name": "first_entry_mimetype", "ok": bool(names and names[0] == "mimetype")})
        checks.append({"name": "zip_test", "ok": zf.testzip() is None})
        checks.append({"name": "content_opf_exists", "ok": "OEBPS/content.opf" in names})
        checks.append({"name": "nav_exists", "ok": "OEBPS/nav.xhtml" in names})
        checks.append({"name": "toc_ncx_exists", "ok": "OEBPS/toc.ncx" in names})
        xhtml_count = len([name for name in names if name.startswith("OEBPS/text/") and name.endswith(".xhtml")])
        checks.append({"name": "chapter_xhtml_count", "value": xhtml_count, "ok": xhtml_count == chapter_count})
    image_files = list((epub_dir / "OEBPS" / "images").glob("*"))
    checks.append({"name": "image_file_count", "value": len(image_files), "ok": True})
    return {"status": "PASS" if all(bool(item.get("ok")) for item in checks) else "FAIL", "checks": checks}


def build_lightnovel_epub_package(
    *,
    input_pack: Mapping[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    """Build an EPUB package from a public/authorized lightnovel.fun article."""
    source = resolve_lightnovel_source(input_pack)
    source_url = str(source.get("url") or "").strip()
    html_path = str(source.get("html_path") or "").strip()
    html_text, captured_from = _read_source_html(url=source_url, html_path=html_path)
    base_url = source_url or captured_from
    package_dir = run_dir / "epub_package"
    out_dir = package_dir / "out"
    epub_dir = out_dir / "epub_src"
    oebps = epub_dir / "OEBPS"
    image_dir = oebps / "images"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    image_dir.mkdir(parents=True, exist_ok=True)
    _write_epub_container(epub_dir)

    BeautifulSoup, _navigable_string, _tag_type = _load_bs4()
    soup = BeautifulSoup(html_text, "html.parser")
    content = soup.select_one(DEFAULT_CONTENT_SELECTOR)
    if content is None:
        raise ValueError("lightnovel article content not found")
    raw_text = content.get_text("\n", strip=True)
    if len(raw_text) < 800:
        raise ValueError("lightnovel article content is too short or locked")

    recommendation = source.get("recommendation") if isinstance(source.get("recommendation"), Mapping) else {}
    title = str(input_pack.get("title") or "").strip() or _extract_title(soup, content, recommendation)
    metadata = _meta_from_text(raw_text)
    images = _source_images(content, base_url=base_url)
    image_map: dict[str, str] = {}
    image_manifest: list[dict[str, Any]] = []
    for index, image in enumerate(images, 1):
        data, content_type, suffix = _load_image_bytes(image["src"], base_url=base_url)
        rel = f"images/img{index:02d}{suffix}"
        path = oebps / rel
        path.write_bytes(data)
        image_map[image["src"]] = rel
        image_manifest.append(
            {
                "index": index,
                "source_url": image["absolute_url"],
                "file": rel,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "content_type": content_type,
            }
        )

    blocks = _extract_blocks(content, image_map)
    text_char_count = sum(len(block.get("text", "")) for block in blocks if block.get("type") == "p")
    if text_char_count < 800:
        raise ValueError("lightnovel article extracted text is too short or locked")
    source_image_positions = [
        {"index": len([b for b in blocks[:i + 1] if b.get("type") == "img"]), **_image_context(blocks, i)}
        for i, block in enumerate(blocks)
        if block.get("type") == "img"
    ]
    chapters = _split_chapters(blocks)
    chapter_items = _write_chapters(chapters, oebps)
    built_at = _utc_now()
    _write_package_files(
        oebps=oebps,
        title=title,
        source_url=source_url,
        metadata=metadata,
        chapters=chapter_items,
        images=image_manifest,
        built_at=built_at,
    )
    epub_path = out_dir / f"{_safe_name(title)}.epub"
    _zip_epub(epub_dir, epub_path)
    validation = _validate_epub(epub_path, epub_dir, len(chapter_items))
    result = {
        "status": "OK" if validation["status"] == "PASS" else "DEGRADED",
        "title": title,
        "source_url": source_url,
        "source_selector": DEFAULT_CONTENT_SELECTOR,
        "captured_from": captured_from,
        "epub_path": str(epub_path),
        "epub_size_bytes": epub_path.stat().st_size,
        "sha256": hashlib.sha256(epub_path.read_bytes()).hexdigest(),
        "chapter_count": len(chapter_items),
        "image_count": len(image_manifest),
        "text_char_count": text_char_count,
        "metadata": metadata,
        "built_at": built_at,
        "validation": validation,
        "source_resolution": source,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "content_manifest.json").write_text(
        json.dumps({"chapters": chapter_items, "text_char_count": text_char_count}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "image_manifest.json").write_text(
        json.dumps({"images": image_manifest, "position_contexts": source_image_positions}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "validation_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "sources.jsonl").write_text(
        json.dumps(
            {
                "url": source_url,
                "captured_from": captured_from,
                "content_selector": DEFAULT_CONTENT_SELECTOR,
                "rights_note": "Build only for public, user-owned, explicitly authorized, or account-purchased sources.",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "result_contract.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def execute_lightnovel_epub_work_order(order: AgentWorkOrder, *, run_dir: Path) -> dict[str, Any]:
    input_pack = dict(order.input_pack or {})
    try:
        package = build_lightnovel_epub_package(input_pack=input_pack, run_dir=run_dir)
        status = "EXECUTED" if package["status"] == "OK" else "DEGRADED"
        summary = "Lightnovel EPUB package built." if status == "EXECUTED" else "Lightnovel EPUB package built with warnings."
        result = AgentResult(
            status=status,
            work_order_id=order.work_order_id,
            summary=summary,
            result_text="",
            artifacts_dir=str(run_dir),
            verification=package.get("validation") or {},
            risk_flags=[] if status == "EXECUTED" else ["lightnovel_epub_validation_warning"],
            review_required=False,
            detail=f"epub_path={package.get('epub_path')}; images={package.get('image_count')}; chapters={package.get('chapter_count')}",
            reason_code=None if status == "EXECUTED" else "LIGHTNOVEL_EPUB_VALIDATION_WARNING",
            metadata={
                "workspace": str(Path(package["epub_path"]).parent),
                "epub_path": package.get("epub_path"),
                "epub_size_bytes": package.get("epub_size_bytes"),
                "sha256": package.get("sha256"),
                "title": package.get("title"),
                "source_url": package.get("source_url"),
                "chapter_count": package.get("chapter_count"),
                "image_count": package.get("image_count"),
                "text_char_count": package.get("text_char_count"),
                "native_lightnovel_epub_builder": True,
            },
        )
    except Exception as exc:
        result = AgentResult(
            status="DEGRADED",
            work_order_id=order.work_order_id,
            summary="Lightnovel EPUB package blocked.",
            result_text="",
            artifacts_dir=str(run_dir),
            verification={"status": "NOT_RUN", "reason": str(exc)},
            risk_flags=["lightnovel_epub_blocked"],
            review_required=True,
            detail=str(exc),
            reason_code="LIGHTNOVEL_EPUB_BLOCKED",
            metadata={"native_lightnovel_epub_builder": True},
        )
    payload = result.to_dict()
    (run_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


__all__ = [
    "build_lightnovel_epub_package",
    "execute_lightnovel_epub_work_order",
    "resolve_lightnovel_source",
]
