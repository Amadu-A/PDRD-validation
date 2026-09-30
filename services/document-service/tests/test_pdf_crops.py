# services/document-service/tests/test_pdf_crops.py

"""Проверка реального crop: повороты/cropbox, цвета и отсутствие PDF-аннотаций."""

import io

import fitz
import pytest
from pdrd_document_service.application.use_cases.crop_pdf import CropPdf
from pdrd_document_service.domain.pdf import PdfNormalizedBoundingBox
from pdrd_document_service.infrastructure.pdf.crop import PyMuPdfCropRenderer
from PIL import Image


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("cropped", [False, True])
def test_crop_contains_exact_visible_region_for_rotated_cropped_page(rotation, cropped):
    """В каждом повороте берём область красного объекта относительно видимого листа."""
    with fitz.open() as document:
        page = document.new_page(width=600, height=400)
        red = fitz.Rect(120, 100, 200, 160)
        page.draw_rect(red, color=(1, 0, 0), fill=(1, 0, 0))
        page.draw_rect(fitz.Rect(300, 200, 390, 260), color=(0, 0, 1), fill=(0, 0, 1))
        if cropped:
            page.set_cropbox(fitz.Rect(50, 50, 550, 350))
            red = fitz.Rect(70, 50, 150, 110)
        page.set_rotation(rotation)
        visible = red * page.rotation_matrix
        bounds = page.rect
        box = PdfNormalizedBoundingBox(
            visible.x0 / bounds.width * 1000,
            visible.y0 / bounds.height * 1000,
            visible.x1 / bounds.width * 1000,
            visible.y1 / bounds.height * 1000,
        )
        # Исходная PDF-аннотация не должна испортить вырезаемый учебный объект.
        annotation = page.add_rect_annot(red)
        annotation.set_colors(stroke=(0, 1, 0), fill=(0, 1, 0))
        annotation.update()
        content = document.tobytes()
    result = CropPdf(PyMuPdfCropRenderer(), 1_000_000).execute(
        content=content, page_number=1, regions=(box,)
    )[0]
    image = Image.open(io.BytesIO(result)).convert("RGB")
    assert image.width <= 2000 and image.height <= 2000
    red, green, blue = image.getpixel((image.width // 2, image.height // 2))
    assert red > 240 and green < 20 and blue < 20


def test_crop_output_is_bounded_for_huge_page_and_tiny_fractional_area():
    """Рендерится clip, а не огромный полный лист."""
    with fitz.open() as document:
        document.new_page(width=20000, height=10000)
        content = document.tobytes()
    crops = CropPdf(PyMuPdfCropRenderer(), 1_000_000).execute(
        content=content,
        page_number=1,
        regions=(
            PdfNormalizedBoundingBox(0, 0, 1000, 1000),
            PdfNormalizedBoundingBox(400.1, 400.2, 400.5, 400.6),
        ),
    )
    assert len(crops) == 2
    for crop in crops:
        image = Image.open(io.BytesIO(crop))
        assert 1 <= image.width <= 2000 and 1 <= image.height <= 2000


@pytest.mark.parametrize("content,page", [(b"html", 1), (b"%PDF-invalid", 1)])
def test_invalid_source_does_not_produce_crop(content, page):
    """Повреждённые исходники имеют управляемую ошибку."""
    with pytest.raises(ValueError):
        CropPdf(PyMuPdfCropRenderer(), 1_000_000).execute(
            content=content,
            page_number=page,
            regions=(PdfNormalizedBoundingBox(0, 0, 100, 100),),
        )
