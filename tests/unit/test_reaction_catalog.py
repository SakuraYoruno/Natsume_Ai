from __future__ import annotations

import json

from host_brain.app.reaction_catalog import ReactionCatalog


def test_reaction_catalog_selects_matching_asset(tmp_path) -> None:
    catalog_path = tmp_path / "reaction_catalog.json"
    media_path = tmp_path / "cute.webp"
    media_path.write_bytes(b"fake")
    catalog_path.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "id": "cute-1",
                        "emotion": "cute",
                        "response_form": "sticker",
                        "media_ref": str(media_path),
                        "platforms": ["qq"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    asset = ReactionCatalog(catalog_path).select(platform="qq", emotion="cute", intensity=0.5, soul_id="")

    assert asset is not None
    assert asset.reaction_id == "cute-1"
    assert asset.response_form == "sticker"
    assert asset.media_ref == str(media_path)


def test_reaction_catalog_missing_asset_returns_none(tmp_path) -> None:
    catalog_path = tmp_path / "reaction_catalog.json"
    catalog_path.write_text(json.dumps({"assets": []}), encoding="utf-8")

    assert ReactionCatalog(catalog_path).select(platform="qq", emotion="sad", intensity=0.5, soul_id="") is None


def test_reaction_catalog_allows_structured_gif_asset(tmp_path) -> None:
    catalog_path = tmp_path / "reaction_catalog.json"
    gif_path = tmp_path / "wave.gif"
    gif_path.write_bytes(b"gif")
    catalog_path.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "id": "wave-1",
                        "emotion": "greeting",
                        "response_form": "gif",
                        "gif": str(gif_path),
                        "platforms": ["qq"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    asset = ReactionCatalog(catalog_path).select(platform="qq", emotion="greeting", intensity=0.5, soul_id="")

    assert asset is not None
    assert asset.response_form == "gif"
    assert asset.media_ref == str(gif_path)
