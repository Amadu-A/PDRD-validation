# services/analysis-service/src/pdrd_analysis_service/application/understanding_metrics.py

"""Разделяет основной Understanding, уточнения, переполнения, кеш и физические попытки."""

import asyncio
import json
import logging
from dataclasses import dataclass, replace

from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelOutputBudgetError,
)
from pdrd_analysis_service.application.vision_execution import current_vision_execution
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
        self.logical_requests = 0
        self.main_requests = 0
        self.output_budgets = 0
        self.unknown_usage = False
        self.cache_hits = 0

    async def generate_json(self, **request):
        """Учитывает метрики исчерпанного ответа, не сохраняя его текст."""
        self.logical_requests += 1
        stage = request.get("stage", "")
        self.recoveries += ":recovery:" in stage
        self.main_requests += (
            stage.startswith("page_understanding:") and ":recovery:" not in stage
        )
        try:
            result = await self.delegate.generate_json(**request)
        except (Exception, asyncio.CancelledError) as error:
            self.output_budgets += isinstance(error, VisionModelOutputBudgetError)
            self._record(getattr(error, "metrics", None))
            raise
        self._record(result.metrics)
        if result.metrics is not None and result.metrics.done_reason == "length":
            self.output_budgets += 1
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
        execution = current_vision_execution.get()
        diagnostics["job"] = execution.job_key if execution is not None else "unscoped"

        def total(field):
            """Не интерпретирует неизвестную метрику как подтверждённый ноль."""
            values = [getattr(item, field) for item in self.metrics]
            if self.unknown_usage or (not values and self.calls):
                return None
            return None if any(value is None for value in values) else sum(values)

        result = UnderstandingMetrics(
            attempt=self.calls,
            done_reason=(
                diagnostics["result"]
                if diagnostics.get("result") in {"failed", "cancelled"}
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
            slot_wait_ms=total("slot_wait_ms"),
            diagnostics=diagnostics,
        )
        diagnostics.update(
            vlm_call_count=None if self.unknown_usage else self.calls,
            recovery_count=self.recoveries,
            main_request_count=self.main_requests,
            logical_request_count=self.logical_requests,
            output_budget_count=self.output_budgets,
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
