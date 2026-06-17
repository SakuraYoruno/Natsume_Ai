from __future__ import annotations

import json
import zipfile
from pathlib import Path

from host_brain.agent_runtime.lightnovel_epub_builder import (
    build_lightnovel_epub_package,
    resolve_lightnovel_source,
)


_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="


def _image(index: int) -> str:
    return f"data:image/png;name={index};base64,{_PNG}"


def _write_article_html(path: Path) -> None:
    long_text = "这是用于测试 EPUB 封装的授权正文段落，包含足够多的中文内容来模拟真实章节。"
    repeated = long_text * 35
    path.write_text(
        f"""<!doctype html>
<html>
<head><title>测试轻小说</title></head>
<body>
<h1>身为路人祭司的测试卷</h1>
<div class="article-content">
  <p>作者：测试作者</p>
  <p>插画：测试画师</p>
  <p>第一章 醒来</p>
  <p>图一前文。</p>
  <img src="{_image(1)}" alt="图一" />
  <p>图一后文。{repeated}</p>
  <p>第二章 出发</p>
  <p>图二前文。</p>
  <img src="{_image(2)}" alt="图二" />
  <p>图二后文。{repeated}</p>
  <p>终章 归来</p>
  <p>图三前文。</p>
  <img src="{_image(3)}" alt="图三" />
  <p>图三后文。{repeated}</p>
</div>
</body>
</html>
""",
        encoding="utf-8",
    )


def test_resolve_lightnovel_source_selects_recommendation_by_index(tmp_path: Path) -> None:
    recommendations = tmp_path / "recommendations.json"
    recommendations.write_text(
        json.dumps(
            [
                {"title": "第一本", "detail_url": "https://www.lightnovel.fun/detail/1"},
                {"title": "第二本", "detail_url": "https://www.lightnovel.fun/detail/2"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    source = resolve_lightnovel_source(
        {
            "recommendations_path": str(recommendations),
            "recommendation_index": "第二本",
        }
    )

    assert source["url"] == "https://www.lightnovel.fun/detail/2"
    assert source["recommendation"]["title"] == "第二本"
    assert str(source["source"]).startswith("recommendations_path:2:")


def test_resolve_lightnovel_source_falls_back_to_latest_recommendations_artifact(tmp_path: Path, monkeypatch) -> None:
    runs_root = tmp_path / "codex_runs"
    workspace = runs_root / "agent_lightnovel_recommender_latest" / "workspace"
    workspace.mkdir(parents=True)
    recommendations = workspace / "recommendations.json"
    recommendations.write_text(
        json.dumps(
            [
                {"title": "第一本", "detail_url": "https://www.lightnovel.fun/detail/1"},
                {"title": "第二本", "detail_url": "https://www.lightnovel.fun/detail/2"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("host_brain.agent_runtime.lightnovel_epub_builder.DEFAULT_CODEX_RUNS_ROOT", runs_root)

    source = resolve_lightnovel_source({"user_request": "推荐的第二本"})

    assert source["url"] == "https://www.lightnovel.fun/detail/2"
    assert source["recommendation"]["title"] == "第二本"


def test_build_lightnovel_epub_package_preserves_image_contexts(tmp_path: Path) -> None:
    html_path = tmp_path / "article.html"
    _write_article_html(html_path)

    result = build_lightnovel_epub_package(
        input_pack={
            "source_paths": [str(html_path)],
            "urls": ["https://www.lightnovel.fun/detail/1144163"],
        },
        run_dir=tmp_path / "run",
    )

    epub_path = Path(result["epub_path"])
    image_manifest = json.loads(epub_path.parent.joinpath("image_manifest.json").read_text(encoding="utf-8"))

    assert result["status"] == "OK"
    assert result["chapter_count"] == 4
    assert result["image_count"] == 3
    assert result["metadata"]["author"] == "测试作者"
    assert epub_path.exists()
    assert result["validation"]["status"] == "PASS"
    assert image_manifest["position_contexts"][0]["previous_text"] == "图一前文。"
    assert image_manifest["position_contexts"][0]["next_text"].startswith("图一后文。")
    assert image_manifest["position_contexts"][2]["previous_text"] == "图三前文。"
    assert image_manifest["position_contexts"][2]["next_text"].startswith("图三后文。")

    with zipfile.ZipFile(epub_path) as zf:
        assert zf.namelist()[0] == "mimetype"
        assert "OEBPS/content.opf" in zf.namelist()
        assert "OEBPS/nav.xhtml" in zf.namelist()
