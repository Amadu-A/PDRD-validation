# services/analysis-service/src/pdrd_analysis_service/domain/image_crop.py

"""Преобразование нормализованных координат фрагмента в исходную страницу PDF."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ImageCrop:
    """Растр фрагмента с фактическими границами после округления до пикселей."""

    image_bytes: bytes
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    def project_region(self, region: dict) -> dict | None:
        """Переносит локальную область 0..1000 в нормализованную исходную страницу."""
        try:
            x1, y1, x2, y2 = (
                float(region[key]) for key in ("x_min", "y_min", "x_max", "y_max")
            )
            confidence = float(region.get("confidence", 0))
        except (KeyError, ValueError, TypeError):
            return None
        if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000 and 0 <= confidence <= 1):
            return None
        box = {
            "x_min": round(self.x_min + x1 / 1000 * (self.x_max - self.x_min)),
            "y_min": round(self.y_min + y1 / 1000 * (self.y_max - self.y_min)),
            "x_max": round(self.x_min + x2 / 1000 * (self.x_max - self.x_min)),
            "y_max": round(self.y_min + y2 / 1000 * (self.y_max - self.y_min)),
            "confidence": confidence,
        }
        return (
            box if box["x_min"] < box["x_max"] and box["y_min"] < box["y_max"] else None
        )
