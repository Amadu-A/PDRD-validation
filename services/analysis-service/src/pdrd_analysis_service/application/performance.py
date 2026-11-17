# services/analysis-service/src/pdrd_analysis_service/application/performance.py

"""Измерения этапов и VLM без исходного текста; параллельные вызовы не суммируются в wall time."""

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import wraps

logger = logging.getLogger(__name__)
current_profile: ContextVar["StageProfile | None"] = ContextVar(
    "stage_profile", default=None
)


@dataclass(slots=True)
class StageProfile:
    """Измерения одного логического элемента этапа, включая все физические повторы."""

    document_id: str
    stage: str
    page: int
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started: float = field(default_factory=time.perf_counter)
    calls: list[dict] = field(default_factory=list)

    def result(self, status="ok") -> dict:
        """Оставляет неизвестный расход токенов неизвестным, а не нулевым."""

        def total(key):
            """Суммирует только полностью известную величину."""
            values = [call[key] for call in self.calls]
            return None if any(v is None for v in values) else sum(values)

        physical = total("physical_calls")
        misses = sum(not call["cache_hit"] for call in self.calls)
        return {
            "schema": "pdrd.stage-performance.v1",
            "job_id": None,
            "document_id": self.document_id,
            "stage": self.stage,
            "page": self.page,
            "started_at": self.started_at,
            "ended_at": datetime.now(UTC).isoformat(),
            "wall_ms": round((time.perf_counter() - self.started) * 1000, 2),
            "cpu_ms": None,
            "status": status,
            "logical_vlm_calls": len(self.calls),
            "physical_vlm_calls": physical,
            "retries": None if physical is None else max(0, physical - misses),
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "gpu_wait_ms": total("gpu_wait_ms"),
            "provider_work_ms": total("provider_ms"),
            "cache_hits": sum(c["cache_hit"] for c in self.calls),
            "cache_misses": sum(not c["cache_hit"] for c in self.calls),
            "context_bytes": total("context_bytes"),
            "image_bytes": total("image_bytes"),
            "calls": list(self.calls),
        }


@contextmanager
def stage_profile(*, document_id: str, stage: str, page: int):
    """Передаёт сборщик дочерним coroutine и сохраняет измерение даже при ошибке."""
    profile = StageProfile(document_id, stage, page)
    token = current_profile.set(profile)
    status = "error"
    try:
        yield profile
        status = "ok"
    finally:
        current_profile.reset(token)
        logger.info(
            "stage_performance %s",
            json.dumps(
                profile.result(status), ensure_ascii=False, separators=(",", ":")
            ),
        )


def observe_vision_call(function):
    """Измеряет логический запрос снаружи кэша, не меняя его ключ или контракт."""

    @wraps(function)
    async def measured(*args, **kwargs):
        """Записывает использование модели и безопасный тип ошибки без prompt и изображения."""
        profile = current_profile.get()
        if profile is None:
            return await function(*args, **kwargs)
        started, result, metrics, error_type = time.perf_counter(), None, None, None
        try:
            result = await function(*args, **kwargs)
            metrics = getattr(result, "metrics", None)
            return result
        except BaseException as error:
            error_type = type(error).__name__
            metrics = getattr(error, "metrics", None)
            raise
        finally:
            cache_hit = getattr(metrics, "done_reason", None) == "cache_hit"
            wall = round((time.perf_counter() - started) * 1000, 2)
            profile.calls.append(
                {
                    "stage": kwargs.get("stage"),
                    "wall_ms": wall,
                    "context_bytes": len(str(kwargs.get("prompt", "")).encode("utf-8")),
                    "schema_bytes": len(
                        json.dumps(kwargs.get("schema", {}), ensure_ascii=False).encode(
                            "utf-8"
                        )
                    ),
                    "image_bytes": len(kwargs.get("image_bytes") or b""),
                    "cache_hit": cache_hit,
                    "physical_calls": 0
                    if cache_hit
                    else getattr(metrics, "attempt", None),
                    "input_tokens": 0
                    if cache_hit
                    else getattr(metrics, "prompt_eval_count", None),
                    "output_tokens": 0
                    if cache_hit
                    else getattr(metrics, "eval_count", None),
                    "gpu_wait_ms": 0
                    if cache_hit
                    else getattr(metrics, "slot_wait_ms", None),
                    "provider_ms": 0
                    if cache_hit
                    else getattr(metrics, "total_duration_ms", None),
                    "error_type": error_type,
                }
            )

    return measured


def summarize_profiles(profiles: list[dict], *, job_id: str) -> dict:
    """Сводит реальные интервалы; затраты параллельных генераций остаются отдельной величиной."""

    def timestamp(value):
        """Читает ISO8601 с часовым поясом без зависимости от локальной зоны процесса."""
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("Профиль не содержит часового пояса.")
        return parsed.timestamp()

    stages = []
    for name in dict.fromkeys(p["stage"] for p in profiles):
        rows = [p for p in profiles if p["stage"] == name]
        sums = {}
        for key in (
            "physical_vlm_calls",
            "logical_vlm_calls",
            "retries",
            "provider_work_ms",
            "input_tokens",
            "output_tokens",
            "gpu_wait_ms",
            "cache_hits",
            "cache_misses",
            "context_bytes",
            "image_bytes",
        ):
            values = [row.get(key) for row in rows]
            sums[key] = None if any(v is None for v in values) else sum(values)
        stages.append(
            {
                "name": name,
                "pages": len({row["page"] for row in rows}),
                "wall_ms": round(
                    (
                        max(timestamp(r["ended_at"]) for r in rows)
                        - min(timestamp(r["started_at"]) for r in rows)
                    )
                    * 1000,
                    2,
                ),
                **sums,
            }
        )
    documents = {p["document_id"] for p in profiles}
    if len(documents) != 1 or any(
        p.get("job_id") not in {None, job_id} for p in profiles
    ):
        raise ValueError("Сводка должна относиться к одному заданию.")
    return {
        "schema": "pdrd.performance.v1",
        "job_id": job_id,
        "document_id": next(iter(documents)),
        "measured_span_ms": round(
            (
                max(timestamp(p["ended_at"]) for p in profiles)
                - min(timestamp(p["started_at"]) for p in profiles)
            )
            * 1000,
            2,
        ),
        "stages": stages,
        "note": "Интервал измеренных этапов; полное пользовательское время и внешние операции учитываются отдельно.",
    }
