# services/document-service/src/pdrd_document_service/infrastructure/image_composer.py

"""Pillow adapter объединения PDF и CAD рендеров."""

import io
from dataclasses import dataclass

from PIL import Image

from pdrd_document_service.application.ports.image_composer import (
    ImageCompositionError,
)


@dataclass(frozen=True, slots=True)
class PillowCombinedImageComposer:
    """Объединяет PDF и CAD PNG по горизонтали."""

    max_side: int
    gap_px: int = 24

    def compose(
        self,
        *,
        pdf_png: bytes,
        cad_png: bytes,
    ) -> bytes:
        """Строит единый PNG и ограничивает максимальную сторону."""
        try:
            pdf_image = self._load_image(
                pdf_png,
            )
            cad_image = self._load_image(
                cad_png,
            )

            try:
                combined = Image.new(
                    "RGB",
                    (
                        pdf_image.width + self.gap_px + cad_image.width,
                        max(
                            pdf_image.height,
                            cad_image.height,
                        ),
                    ),
                    "white",
                )

                combined.paste(
                    pdf_image,
                    (
                        0,
                        (combined.height - pdf_image.height) // 2,
                    ),
                )

                combined.paste(
                    cad_image,
                    (
                        pdf_image.width + self.gap_px,
                        (combined.height - cad_image.height) // 2,
                    ),
                )

                prepared = self._limit_size(
                    combined,
                )

                try:
                    output = io.BytesIO()

                    prepared.save(
                        output,
                        format="PNG",
                        optimize=True,
                    )

                    return output.getvalue()
                finally:
                    prepared.close()
            finally:
                pdf_image.close()
                cad_image.close()

        except (
            OSError,
            ValueError,
        ) as error:
            raise ImageCompositionError(
                "Не удалось объединить PDF и CAD рендеры.",
            ) from error

    @staticmethod
    def _load_image(
        content: bytes,
    ) -> Image.Image:
        """Загружает PNG в независимый RGB image."""
        with Image.open(
            io.BytesIO(
                content,
            )
        ) as source:
            source.load()

            return source.convert(
                "RGB",
            )

    def _limit_size(
        self,
        image: Image.Image,
    ) -> Image.Image:
        """Ограничивает размер результата без изменения пропорций."""
        current_max_side = max(
            image.width,
            image.height,
        )

        if current_max_side <= self.max_side:
            return image

        scale = self.max_side / current_max_side

        target_size = (
            max(
                1,
                round(
                    image.width * scale,
                ),
            ),
            max(
                1,
                round(
                    image.height * scale,
                ),
            ),
        )

        resized = image.resize(
            target_size,
            Image.Resampling.LANCZOS,
        )

        image.close()

        return resized
