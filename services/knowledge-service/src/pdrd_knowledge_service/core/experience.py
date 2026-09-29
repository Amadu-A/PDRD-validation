# services/knowledge-service/src/pdrd_knowledge_service/core/experience.py

"""Composition root E: HTTP-владелец каталога, unified embedding и свой индекс Qdrant.

Используется API, индексатором и проверочным CLI. Рабочий поиск включается только
с одобренным отчётом для этой модели и этой коллекции, а не с результатами unit-тестов.
"""

import json
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

from pdrd_knowledge_service.application.ports.experience_versions import VersionIndexJob
from pdrd_knowledge_service.application.use_cases.index_experience import (
    SyncExperienceIndex,
)
from pdrd_knowledge_service.application.use_cases.index_experience_version import (
    RunExperienceVersion,
    SelectedExperienceFeed,
)
from pdrd_knowledge_service.application.use_cases.trusted_experience import (
    SearchTrustedExperience,
)
from pdrd_knowledge_service.core.settings import Settings
from pdrd_knowledge_service.infrastructure.embedding.multimodal_http import (
    HttpMultimodalEmbeddingProvider,
)
from pdrd_knowledge_service.infrastructure.embedding.text_http import (
    HttpTextEmbeddingProvider,
)
from pdrd_knowledge_service.infrastructure.experience_feed import HttpExperienceFeed
from pdrd_knowledge_service.infrastructure.experience_versions import (
    HttpExperienceVersionQueue,
)
from pdrd_knowledge_service.infrastructure.vector_store.qdrant import QdrantVectorStore


def require_quality_report(
    path: Path, *, identity: str, collection: str, top_k: int, min_score: float
) -> None:
    """Отсутствие/ошибка/несовместимость отчёта запрещает запуск рабочего E."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if (
            type(report["report_schema_version"]) is not int
            or report["report_schema_version"] != 1
            or report["approved"] is not True
            or report["embedding_identity"] != identity
            or report["collection"] != collection
            or type(report["top_k"]) is not int
            or report["top_k"] != top_k
            or report["min_score"] != min_score
        ):
            raise ValueError("Оценка E не одобрена для текущего индекса.")
        for count in ("retrieval_cases", "finding_documents", "forbidden_hits"):
            if type(report[count]) is not int or report[count] < 0:
                raise ValueError("Некорректный объём оценки E.")
        for value in (report["recall_at_k"], report["min_score"]):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError("Некорректная метрика E.")
        if (
            report["retrieval_cases"] < 20
            or report["finding_documents"] < 5
            or report["recall_at_k"] < 0.8
            or report["forbidden_hits"] != 0
        ):
            raise ValueError("Недостаточная оценка E.")
        baseline, augmented = report["baseline"], report["with_experience"]
        for metrics in (baseline, augmented):
            for name in ("precision", "recall"):
                value = metrics[name]
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not 0 <= value <= 1
                ):
                    raise ValueError("Некорректная метрика анализа.")
            if (
                type(metrics["false_positives"]) is not int
                or metrics["false_positives"] < 0
            ):
                raise ValueError("Некорректное число ложных замечаний.")
        if (
            augmented["precision"] < baseline["precision"]
            or augmented["recall"] < baseline["recall"]
            or augmented["false_positives"] > baseline["false_positives"]
        ):
            raise ValueError("Оценка E ухудшила качество.")
        if not (
            augmented["precision"] > baseline["precision"]
            or augmented["recall"] > baseline["recall"]
        ):
            raise ValueError("Оценка E не показала улучшения.")
        if not re.fullmatch(r"[a-f0-9]{64}", report["dataset_sha256"]):
            raise ValueError("Отчёт E не привязан к датасету.")
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ValueError(
            "Рабочий E требует успешную оценку на отложенных документах."
        ) from error


@dataclass(frozen=True, slots=True)
class ExperienceContainer:
    """Общие зависимости рабочего и проверочного поиска, без чужих SQL-репозиториев."""

    index: SyncExperienceIndex
    search: SearchTrustedExperience


def build_experience_container(
    settings: Settings, *, shadow: bool = False
) -> ExperienceContainer:
    """Создаёт HTTP-адаптеры; проверочный режим доступен CLI, не параметру браузера."""
    options = settings.experience
    identity = settings.embedding_identity.fingerprint
    collection = settings.embedding_index_plan.experience_target + "_human_review_v1"
    if settings.search.experience_enabled and not shadow:
        if len(options.key.get_secret_value()) < 32:
            raise ValueError("Рабочий E требует служебный ключ Experience.")
        require_quality_report(
            options.quality_report,
            identity=identity,
            collection=collection,
            top_k=settings.search.experience_top_k,
            min_score=options.min_score,
        )
    source = HttpExperienceFeed(options.base_url, options.key.get_secret_value())
    vectors = QdrantVectorStore(
        base_url=settings.qdrant.base_url,
        request_timeout_seconds=settings.qdrant.request_timeout_seconds,
        health_timeout_seconds=settings.qdrant.health_timeout_seconds,
    )
    common = {
        "base_url": settings.embedding.base_url,
        "request_timeout_seconds": settings.embedding.request_timeout_seconds,
        "connect_timeout_seconds": settings.embedding.connect_timeout_seconds,
        "health_timeout_seconds": settings.embedding.health_timeout_seconds,
        "output_dimension": settings.embedding_dimension,
    }
    return ExperienceContainer(
        index=SyncExperienceIndex(
            source,
            HttpMultimodalEmbeddingProvider(**common),
            vectors,
            collection,
            identity,
            settings.embedding_dimension,
            options.page_size,
        ),
        search=SearchTrustedExperience(
            HttpTextEmbeddingProvider(**common),
            vectors,
            source,
            collection,
            settings.embedding_model,
            identity,
            settings.search.experience_top_k,
            options.min_score,
            enabled=shadow or settings.search.experience_enabled,
            require_section=not shadow,
        ),
    )


def build_version_worker(
    settings: Settings, container: ExperienceContainer, *, worker: str
) -> RunExperienceVersion:
    """Очередь ручных версий использует ту же модель; альтернативы требуют своего совместимого worker."""
    source = HttpExperienceFeed(
        settings.experience.base_url, settings.experience.key.get_secret_value()
    )
    return RunExperienceVersion(
        HttpExperienceVersionQueue(source),
        container.index,
        worker,
        settings.embedding_model,
    )


async def prepare_version_search(
    settings: Settings, container: ExperienceContainer, version_id: UUID
) -> tuple[ExperienceContainer, VersionIndexJob]:
    """CLI выбирает готовую версию для эксперимента, не меняя applied или рабочий флаг."""
    source = HttpExperienceFeed(
        settings.experience.base_url, settings.experience.key.get_secret_value()
    )
    job = await HttpExperienceVersionQueue(source).read(
        version_id=version_id,
        model=settings.embedding_model,
        identity=container.index.identity,
        dimension=container.index.dimension,
    )
    return ExperienceContainer(
        replace(
            container.index,
            collection=job.collection,
            source=SelectedExperienceFeed(source, job.members),
        ),
        replace(
            container.search,
            collection=job.collection,
            require_section=True,
            source=SelectedExperienceFeed(source, job.members),
        ),
    ), job
