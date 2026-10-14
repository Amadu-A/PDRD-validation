# services/analysis-service/src/pdrd_analysis_service/application/ports/image_cropper.py

"""Порт выборочного чтения растровых фрагментов без зависимости application от PIL."""

from typing import Protocol

from pdrd_analysis_service.domain.image_crop import ImageCrop


class ImageCropper(Protocol):
    """Возвращает фрагмент и преобразование в координаты исходного PDF."""

    async def crop(self, image_bytes: bytes, region: dict) -> ImageCrop:
        """Обрезает только явно выбранную область, сохраняя фактические границы."""
        ...
