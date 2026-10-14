# services/analysis-service/src/pdrd_analysis_service/application/limited_vision.py

"""Общий лимит VLM для всех этапов и одновременно активных заданий."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from pdrd_analysis_service.application.ports.vision_model import StructuredVisionModel
from pdrd_analysis_service.domain.analysis import GenerationResult


@dataclass(slots=True)
class LimitedVisionModel:
    """Ограничивает обращения к resident VLM одним общим semaphore."""

    delegate: StructuredVisionModel
    max_concurrent: int = 4
    _semaphore: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        """Создаёт лимит на процесс Analysis Service."""
        if self.max_concurrent < 1:
            raise ValueError("Лимит VLM должен быть положительным.")
        self._semaphore = asyncio.Semaphore(self.max_concurrent)

    async def generate_json(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        num_predict: int,
        seed: int,
        stage: str,
        image_bytes: bytes | None = None,
    ) -> GenerationResult:
        """Ожидает общий слот; отмена ожидания не запускает GPU-запрос."""
        async with self._semaphore:
            return await self.delegate.generate_json(
                prompt=prompt,
                schema=schema,
                num_predict=num_predict,
                seed=seed,
                stage=stage,
                image_bytes=image_bytes,
            )

    async def is_ready(self) -> bool:
        """Проверяет логический runtime без занятия inference слота."""
        return await self.delegate.is_ready()
