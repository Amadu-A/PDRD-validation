# services/analysis-service/src/pdrd_analysis_service/application/understanding_metrics.py

"""Считает обращения Understanding, включая уточнения, восстановление и неудачные попытки."""

import json
import logging
from dataclasses import dataclass, replace

from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelError,
    VisionModelOutputBudgetError,
)
from pdrd_analysis_service.domain.analysis import GenerationMetrics

logger = logging.getLogger(__name__)


class UnderstandingMeasurements:
    """Прозрачный декоратор существующего VLM-порта, не меняющий кеш и limiter."""

    def __init__(self, delegate):
        """Готовит измерения только одного физического листа."""
        self.delegate = delegate
        self.metrics = []
        self.calls = 0
        self.recoveries = 0
        self.unknown_usage = False
        self.cache_hits = 0

    async def generate_json(self, **request):
        """Учитывает метрики исчерпанного ответа, не сохраняя его текст."""
        try:
            result = await self.delegate.generate_json(**request)
        except VisionModelError as error:
            self.recoveries += isinstance(error, VisionModelOutputBudgetError)
            self._record(error.metrics)
            raise
        self._record(result.metrics)
        if result.metrics is not None and result.metrics.done_reason == "length":
            self.recoveries += 1
        return result

    def _record(self, metrics):
        """Cache hit не считается GPU-вызовом; отсутствие usage не выдаётся за ноль."""
        if metrics is not None and metrics.done_reason == "cache_hit":
            self.cache_hits += 1
            metrics = replace(
                metrics, attempt=0, requested_num_predict=0, content_length=0
            )
        self.calls += metrics.attempt if metrics is not None else 1
        if metrics is not None:
            self.metrics.append(metrics)
        else:
            self.unknown_usage = True

    async def is_ready(self):
        """Оставляет readiness существующему адаптеру."""
        return await self.delegate.is_ready()

    def result(self, diagnostics: dict) -> GenerationMetrics:
        """Возвращает суммарные известные затраты и безопасную диагностику листа."""

        def total(field):
            """Не интерпретирует неизвестную метрику как подтверждённый ноль."""
            values = [getattr(item, field) for item in self.metrics]
            if self.unknown_usage or (not values and self.calls):
                return None
            return None if any(value is None for value in values) else sum(values)

        result = UnderstandingMetrics(
            attempt=self.calls,
            done_reason=(
                "failed"
                if diagnostics.get("result") == "failed"
                else self.metrics[-1].done_reason
                if self.metrics
                else "unknown"
                if self.calls
                else "pdf_table"
            ),
            requested_num_predict=total("requested_num_predict"),
            total_duration_ms=total("total_duration_ms"),
            load_duration_ms=total("load_duration_ms"),
            prompt_eval_count=total("prompt_eval_count"),
            eval_count=total("eval_count"),
            content_length=total("content_length"),
            thinking_length=total("thinking_length"),
            diagnostics=diagnostics,
        )
        diagnostics.update(
            vlm_call_count=self.calls,
            recovery_count=self.recoveries,
            cache_hits=self.cache_hits,
            input_tokens=result.prompt_eval_count,
            output_tokens=result.eval_count,
        )
        logger.info(
            "understanding_page %s",
            json.dumps(diagnostics, ensure_ascii=False, sort_keys=True),
        )
        return result


@dataclass(frozen=True, slots=True)
class UnderstandingMetrics(GenerationMetrics):
    """Расширяет словарь метрик без изменения PageFacts или внешнего HTTP DTO."""

    diagnostics: dict

    def as_dict(self):
        """Сохраняет прежние поля и добавляет структурированную диагностику."""
        return {**GenerationMetrics.as_dict(self), "understanding": self.diagnostics}
