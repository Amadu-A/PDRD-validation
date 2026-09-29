# tests/unit/test_crop_store.py

"""Устойчивый PNG-store: конкурентная запись, пересоздание, подмена и path traversal."""

import asyncio
import io
from dataclasses import replace

import pytest
from pdrd_experience_service.infrastructure.crops import LocalCropStore
from PIL import Image


@pytest.mark.asyncio
async def test_crops_survive_recreation_and_concurrent_duplicate_writes(tmp_path):
    """Контентный адрес одинаков, а частичного PNG и временных файлов не остаётся."""
    buffer = io.BytesIO()
    Image.new("RGB", (100, 80), "red").save(buffer, format="PNG")
    content = buffer.getvalue()
    store = LocalCropStore(tmp_path)
    crops = await asyncio.gather(*(store.put(content) for _ in range(4)))
    assert len(set(crops)) == 1
    assert await LocalCropStore(tmp_path).read(crops[0]) == content
    assert len(list(tmp_path.rglob("*.png"))) == 1
    assert not list(tmp_path.rglob(".crop-*"))
    with pytest.raises(RuntimeError):
        await store.read(replace(crops[0], sha256="../escape"))
    path = next(tmp_path.rglob("*.png"))
    path.write_bytes(content[:-1] + b"x")
    with pytest.raises(RuntimeError):
        await store.read(crops[0])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content", [b"%PDF-1.7", b"<html>404</html>", b"\x89PNG\r\n\x1a\n"]
)
async def test_non_png_is_not_saved(tmp_path, content):
    """Ошибки внутренних сервисов не сохраняются как миниатюры."""
    with pytest.raises(RuntimeError):
        await LocalCropStore(tmp_path).put(content)
    assert not list(tmp_path.iterdir())
