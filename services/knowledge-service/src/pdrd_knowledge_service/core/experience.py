# services/knowledge-service/src/pdrd_knowledge_service/core/experience.py

"""Composition root E: HTTP-владелец каталога, unified embedding и свой индекс Qdrant.

Используется API, индексатором и проверочным CLI. Рабочий поиск включается только
с одобренным отчётом для этой модели и этой коллекции, а не с результатами unit-тестов.
"""

import json
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

from pdrd_knowledge_service.application.ports.experience_versions import VersionIndexJob
from pdrd_knowledge_service.application.use_cases.applied_experience import (
    SearchAppliedExperience,
)
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
from pdrd_knowledge_service.domain.experience_admission import require_quality_metrics
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
        require_quality_metrics(
            report,
            identity=identity,
            collection=collection,
            top_k=top_k,
            min_score=min_score,
        )
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ValueError(
            "Рабочий E требует успешную оценку на отложенных документах."
        ) from error


@dataclass(frozen=True, slots=True)
class ExperienceContainer:
    """Общие зависимости рабочего и проверочного поиска, без чужих SQL-репозиториев."""

    index: SyncExperienceIndex
    search: SearchTrustedExperience | SearchAppliedExperience


def build_experience_container(
    settings: Settings, *, shadow: bool = False
) -> ExperienceContainer:
    """Создаёт HTTP-адаптеры; проверочный режим доступен CLI, не параметру браузера."""
    options = settings.experience
    identity = settings.embedding_identity.fingerprint
    collection = settings.embedding_index_plan.experience_target + "_human_review_v1"
    if (
        settings.search.experience_enabled
        and not shadow
        and len(options.key.get_secret_value()) < 32
    ):
        raise ValueError("Рабочий E требует служебный ключ Experience.")
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
    trusted_search = SearchTrustedExperience(
        HttpTextEmbeddingProvider(**common),
        vectors,
        source,
        collection if shadow else "",
        settings.embedding_model,
        identity,
        settings.search.experience_top_k,
        options.min_score,
        enabled=shadow,
        require_section=not shadow,
    )
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
        search=(
            trusted_search
            if shadow
            else SearchAppliedExperience(
                trusted_search,
                HttpExperienceVersionQueue(source),
                settings.embedding_dimension,
                enabled=settings.search.experience_enabled,
            )
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
    if not isinstance(container.search, SearchTrustedExperience):
        raise ValueError("Выбор версии доступен только операторскому проверочному CLI.")
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
