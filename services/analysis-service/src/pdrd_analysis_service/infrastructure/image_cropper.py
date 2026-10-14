# services/analysis-service/src/pdrd_analysis_service/infrastructure/image_cropper.py

"""PIL-адаптер чтения неоднозначных фрагментов схем вне event loop."""

import asyncio
import math
from io import BytesIO

from PIL import Image

from pdrd_analysis_service.domain.image_crop import ImageCrop


class PilImageCropper:
    """Выполняет растровую обрезку; реальные округлённые границы входят в результат."""

    async def crop(self, image_bytes: bytes, region: dict) -> ImageCrop:
        """Выносит декодирование и PNG-кодирование в рабочий поток."""
        return await asyncio.to_thread(self._crop, image_bytes, region)

    @staticmethod
    def _crop(image_bytes: bytes, region: dict) -> ImageCrop:
        """Округляет наружу до пикселей, не подменяя матрицу координат запрошенной областью."""
        with Image.open(BytesIO(image_bytes)) as source:
            width, height = source.size
            x1 = max(0, math.floor(float(region["x_min"]) * width / 1000))
            y1 = max(0, math.floor(float(region["y_min"]) * height / 1000))
            x2 = min(width, math.ceil(float(region["x_max"]) * width / 1000))
            y2 = min(height, math.ceil(float(region["y_max"]) * height / 1000))
            if x1 >= x2 or y1 >= y2:
                raise ValueError(
                    "Фрагмент изображения должен иметь положительную площадь."
                )
            output = BytesIO()
            source.crop((x1, y1, x2, y2)).convert("RGB").save(output, format="PNG")
            return ImageCrop(
                output.getvalue(),
                x1 / width * 1000,
                y1 / height * 1000,
                x2 / width * 1000,
                y2 / height * 1000,
            )
