# services/document-service/tests/unit/test_page_content.py

"""Графика и изображения не позволяют ошибочно признать смешанный лист чистой таблицей."""

import io

import fitz
import pytest
from pdrd_document_service.infrastructure.pdf.page_content import page_content_metadata
from PIL import Image


@pytest.mark.parametrize(
    "content", ["text", "graphic", "image_outside", "image_inside", "long_text"]
)
def test_route_requires_vision_for_unexplained_graphics_images_and_dense_text(content):
    """Чистая текстовая таблица обходится без VLM; даже изображение внутри неё требует проверки."""
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        table = fitz.Rect(30, 30, 400, 300)
        page.insert_text((40, 50), "Position Model Quantity")
        page.draw_rect(table)
        if content == "graphic":
            page.draw_circle((200, 500), 30)
        elif content.startswith("image"):
            stream = io.BytesIO()
            Image.new("RGB", (10, 10), "blue").save(stream, format="PNG")
            page.insert_image(
                fitz.Rect(60, 60, 80, 80)
                if content == "image_inside"
                else fitz.Rect(100, 400, 120, 420),
                stream=stream.getvalue(),
            )
        elif content == "long_text":
            page.insert_text((20, 450), "A" * 190, fontsize=2)
        result = page_content_metadata(page, [table], [])
        assert result["route"] == ("pdf_table" if content == "text" else "mixed")
        assert result["requires_vision"] is (content != "text")
        assert result["version"] == 1
        assert result["table_regions"][0]["x_min"] == 50
