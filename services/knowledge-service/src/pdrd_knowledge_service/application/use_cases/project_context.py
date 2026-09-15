# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/project_context.py

"""Use cases reusable Project Context cache."""

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from uuid import (
    NAMESPACE_URL,
    UUID,
    uuid4,
    uuid5,
)

from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProvider,
    EmbeddingProviderError,
)
from pdrd_knowledge_service.application.ports.vector_store import (
    VectorStore,
    VectorStoreError,
)
from pdrd_knowledge_service.domain.project_context import (
    ProjectContextCacheStatus,
    ProjectContextChunk,
    ProjectContextError,
    ProjectContextInfo,
    ProjectContextSearchResult,
    ProjectContextSource,
    ProjectContextTextPage,
    ProjectContextValidationItem,
    ProjectContextValidationSnapshot,
    VectorRecord,
    chunk_project_context_text,
    normalize_project_context_pages,
    normalize_project_context_text,
    project_context_cache_context_id,
    project_context_cache_key,
    project_context_collection_name,
    project_context_staging_collection_name,
)

logger = logging.getLogger(
    __name__,
)

PROJECT_CONTEXT_QUERY_INSTRUCTION = (
    "Given the content of an engineering drawing "
    "from the same project, retrieve the most "
    "relevant fragments of the project's "
    "explanatory note. Prefer fragments about "
    "the same equipment, tags, cables, functions, "
    "installation conditions, technical solutions "
    "and design assumptions."
)

PROJECT_CONTEXT_CACHE_SCHEMA_VERSION = 1

_CACHE_KEY_FIELD = "_pdrd_project_context_cache_key"
_CACHE_READY_FIELD = "_pdrd_project_context_cache_ready"
_CACHE_SCHEMA_FIELD = "_pdrd_project_context_cache_schema_version"
_CACHE_MODEL_FIELD = "_pdrd_project_context_embedding_model"
_CACHE_DIMENSION_FIELD = "_pdrd_project_context_embedding_dimension"
_CACHE_EMBEDDING_SCHEMA_FIELD = "_pdrd_project_context_embedding_schema_version"
_CACHE_PAGES_COUNT_FIELD = "_pdrd_project_context_pages_count"
_CACHE_CHUNKS_COUNT_FIELD = "_pdrd_project_context_chunks_count"
_CACHE_VECTOR_SIZE_FIELD = "_pdrd_project_context_vector_size"
_CACHE_VALIDATION_FIELD = "_pdrd_project_context_validation"


def _validation_to_payload(
    validation: ProjectContextValidationSnapshot | None,
) -> dict[str, Any] | None:
    """Сериализует validation snapshot в Qdrant payload."""
    if validation is None:
        return None

    return {
        "enabled": validation.enabled,
        "pages_count": validation.pages_count,
        "classifications": [
            {
                "page_number": item.page_number,
                "kind": item.kind,
                "confidence": item.confidence,
                "reason": item.reason,
            }
            for item in validation.classifications
        ],
        "warnings": [
            {
                "page_number": item.page_number,
                "kind": item.kind,
                "confidence": item.confidence,
                "reason": item.reason,
            }
            for item in validation.warnings
        ],
    }


def _validation_item_from_payload(
    payload: object,
) -> ProjectContextValidationItem | None:
    """Разбирает один cached validation item."""
    if not isinstance(
        payload,
        dict,
    ):
        return None

    try:
        page_number = int(
            payload.get(
                "page_number",
            )
        )

        confidence = float(
            payload.get(
                "confidence",
                0.0,
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        return None

    if page_number < 1:
        return None

    return ProjectContextValidationItem(
        page_number=page_number,
        kind=str(
            payload.get(
                "kind",
                "other",
            )
        ),
        confidence=min(
            max(
                confidence,
                0.0,
            ),
            1.0,
        ),
        reason=str(
            payload.get(
                "reason",
                "",
            )
        ),
    )


def _validation_from_payload(
    payload: object,
) -> ProjectContextValidationSnapshot | None:
    """Разбирает persisted validation snapshot."""
    if not isinstance(
        payload,
        dict,
    ):
        return None

    classifications_raw = payload.get(
        "classifications",
        [],
    )

    warnings_raw = payload.get(
        "warnings",
        [],
    )

    if not isinstance(
        classifications_raw,
        list,
    ) or not isinstance(
        warnings_raw,
        list,
    ):
        return None

    classifications: list[ProjectContextValidationItem] = []

    for item in classifications_raw:
        parsed = _validation_item_from_payload(
            item,
        )

        if parsed is None:
            return None

        classifications.append(
            parsed,
        )

    warnings: list[ProjectContextValidationItem] = []

    for item in warnings_raw:
        parsed = _validation_item_from_payload(
            item,
        )

        if parsed is None:
            return None

        warnings.append(
            parsed,
        )

    try:
        pages_count = int(
            payload.get(
                "pages_count",
                0,
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        return None

    return ProjectContextValidationSnapshot(
        enabled=bool(
            payload.get(
                "enabled",
                False,
            )
        ),
        pages_count=pages_count,
        classifications=tuple(
            classifications,
        ),
        warnings=tuple(
            warnings,
        ),
    )


@dataclass(frozen=True, slots=True)
class ResolveProjectContextCache:
    """Определяет reusable Project Context cache до VLM validation."""

    vector_store: VectorStore

    collection_prefix: str

    embedding_model: str
    embedding_dimension: int
    embedding_schema_version: int

    chunk_size: int
    chunk_overlap: int

    cache_schema_version: int = PROJECT_CONTEXT_CACHE_SCHEMA_VERSION

    async def execute(
        self,
        *,
        context_id: UUID,
        enabled: bool,
        pages: tuple[
            ProjectContextTextPage,
            ...,
        ],
    ) -> ProjectContextCacheStatus:
        """Возвращает deterministic cache identity и HIT/MISS."""
        if not enabled:
            return ProjectContextCacheStatus(
                context_id=context_id,
                enabled=False,
                cache_key=None,
                cache_hit=False,
                collection_name=None,
                pages_count=0,
                chunks_count=0,
                vector_size=0,
                validation=None,
            )

        normalized_pages = normalize_project_context_pages(
            pages,
        )

        if not normalized_pages:
            raise ProjectContextError(
                "Диапазон ПЗ не содержит страниц.",
            )

        cache_key = project_context_cache_key(
            pages=normalized_pages,
            embedding_model=self.embedding_model,
            embedding_dimension=(self.embedding_dimension),
            embedding_schema_version=(self.embedding_schema_version),
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            cache_schema_version=(self.cache_schema_version),
        )

        cache_context_id = project_context_cache_context_id(
            cache_key,
        )

        alias = project_context_collection_name(
            prefix=self.collection_prefix,
            context_id=cache_context_id,
        )

        target = await self.vector_store.get_alias_target(
            alias,
        )

        if target is None:
            result = self._miss(
                context_id=cache_context_id,
                cache_key=cache_key,
                collection_name=alias,
                pages_count=len(
                    normalized_pages,
                ),
            )

            self._log_result(
                result,
            )

            return result

        try:
            payloads = await self.vector_store.scroll_payloads(
                collection=target,
            )

        except VectorStoreError:
            result = self._miss(
                context_id=cache_context_id,
                cache_key=cache_key,
                collection_name=alias,
                pages_count=len(
                    normalized_pages,
                ),
            )

            self._log_result(
                result,
            )

            return result

        if not payloads:
            result = self._miss(
                context_id=cache_context_id,
                cache_key=cache_key,
                collection_name=alias,
                pages_count=len(
                    normalized_pages,
                ),
            )

            self._log_result(
                result,
            )

            return result

        metadata = payloads[0].payload

        if not self._metadata_matches(
            payloads=tuple(item.payload for item in payloads),
            cache_key=cache_key,
        ):
            result = self._miss(
                context_id=cache_context_id,
                cache_key=cache_key,
                collection_name=alias,
                pages_count=len(
                    normalized_pages,
                ),
            )

            self._log_result(
                result,
            )

            return result

        chunks_count = self._positive_int(
            metadata.get(
                _CACHE_CHUNKS_COUNT_FIELD,
            )
        )

        pages_count = self._positive_int(
            metadata.get(
                _CACHE_PAGES_COUNT_FIELD,
            )
        )

        vector_size = self._positive_int(
            metadata.get(
                _CACHE_VECTOR_SIZE_FIELD,
            )
        )

        validation = _validation_from_payload(
            metadata.get(
                _CACHE_VALIDATION_FIELD,
            )
        )

        if (
            chunks_count is None
            or pages_count is None
            or vector_size is None
            or chunks_count
            != len(
                payloads,
            )
            or pages_count
            != len(
                normalized_pages,
            )
            or validation is None
            or not validation.enabled
            or validation.pages_count != pages_count
        ):
            result = self._miss(
                context_id=cache_context_id,
                cache_key=cache_key,
                collection_name=alias,
                pages_count=len(
                    normalized_pages,
                ),
            )

            self._log_result(
                result,
            )

            return result

        result = ProjectContextCacheStatus(
            context_id=cache_context_id,
            enabled=True,
            cache_key=cache_key,
            cache_hit=True,
            collection_name=alias,
            pages_count=pages_count,
            chunks_count=chunks_count,
            vector_size=vector_size,
            validation=validation,
        )

        self._log_result(
            result,
        )

        return result

    def _metadata_matches(
        self,
        *,
        payloads: tuple[
            dict[str, Any],
            ...,
        ],
        cache_key: str,
    ) -> bool:
        """Проверяет cache metadata всех persisted chunks."""
        for payload in payloads:
            if (
                payload.get(
                    _CACHE_KEY_FIELD,
                )
                != cache_key
            ):
                return False

            if (
                payload.get(
                    _CACHE_READY_FIELD,
                )
                is not True
            ):
                return False

            if (
                self._positive_int(
                    payload.get(
                        _CACHE_SCHEMA_FIELD,
                    )
                )
                != self.cache_schema_version
            ):
                return False

            if (
                str(
                    payload.get(
                        _CACHE_MODEL_FIELD,
                        "",
                    )
                )
                != self.embedding_model
            ):
                return False

            if (
                self._positive_int(
                    payload.get(
                        _CACHE_DIMENSION_FIELD,
                    )
                )
                != self.embedding_dimension
            ):
                return False

            if (
                self._positive_int(
                    payload.get(
                        _CACHE_EMBEDDING_SCHEMA_FIELD,
                    )
                )
                != self.embedding_schema_version
            ):
                return False

        return True

    @staticmethod
    def _positive_int(
        value: object,
    ) -> int | None:
        """Возвращает положительный int либо None."""
        try:
            result = int(
                value,
            )

        except (
            TypeError,
            ValueError,
        ):
            return None

        if result < 1:
            return None

        return result

    @staticmethod
    def _miss(
        *,
        context_id: UUID,
        cache_key: str,
        collection_name: str,
        pages_count: int,
    ) -> ProjectContextCacheStatus:
        """Формирует cache miss result."""
        return ProjectContextCacheStatus(
            context_id=context_id,
            enabled=True,
            cache_key=cache_key,
            cache_hit=False,
            collection_name=collection_name,
            pages_count=pages_count,
            chunks_count=0,
            vector_size=0,
            validation=None,
        )

    @staticmethod
    def _log_result(
        result: ProjectContextCacheStatus,
    ) -> None:
        """Пишет дешёвую cache metric."""
        logger.info(
            (
                "project_context_cache "
                "hit=%s "
                "context_id=%s "
                "cache_key=%s "
                "pages=%s "
                "chunks=%s"
            ),
            result.cache_hit,
            result.context_id,
            (result.cache_key[:12] if result.cache_key else "-"),
            result.pages_count,
            result.chunks_count,
        )


@dataclass(frozen=True, slots=True)
class CreateProjectContext:
    """Создаёт либо переиспользует reusable PZ index."""

    embedding_provider: EmbeddingProvider
    vector_store: VectorStore

    collection_prefix: str
    embedding_model: str
    embedding_dimension: int
    embedding_schema_version: int

    chunk_size: int
    chunk_overlap: int

    embed_batch_size: int
    upsert_batch_size: int

    cache_schema_version: int = PROJECT_CONTEXT_CACHE_SCHEMA_VERSION

    async def execute(
        self,
        *,
        context_id: UUID,
        enabled: bool,
        pages: tuple[
            ProjectContextTextPage,
            ...,
        ],
        cache_key: str | None = None,
        validation: ProjectContextValidationSnapshot | None = None,
    ) -> ProjectContextInfo:
        """Создаёт cache atomically либо возвращает существующий."""
        if not enabled:
            return ProjectContextInfo(
                context_id=context_id,
                enabled=False,
                collection_name=None,
                pages_count=0,
                chunks_count=0,
                vector_size=0,
                cache_key=None,
                cache_hit=False,
                validation=None,
            )

        resolver = ResolveProjectContextCache(
            vector_store=self.vector_store,
            collection_prefix=self.collection_prefix,
            embedding_model=self.embedding_model,
            embedding_dimension=(self.embedding_dimension),
            embedding_schema_version=(self.embedding_schema_version),
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            cache_schema_version=(self.cache_schema_version),
        )

        resolved = await resolver.execute(
            context_id=context_id,
            enabled=True,
            pages=pages,
        )

        if resolved.cache_key is None:
            raise ProjectContextError(
                "Не удалось определить Project Context cache key.",
            )

        if cache_key is not None and cache_key.strip().lower() != resolved.cache_key:
            raise ProjectContextError(
                "Переданный Project Context cache key не соответствует содержимому ПЗ.",
            )

        if resolved.cache_hit:
            return ProjectContextInfo(
                context_id=resolved.context_id,
                enabled=True,
                collection_name=(resolved.collection_name),
                pages_count=resolved.pages_count,
                chunks_count=resolved.chunks_count,
                vector_size=resolved.vector_size,
                cache_key=resolved.cache_key,
                cache_hit=True,
                validation=resolved.validation,
            )

        normalized_pages = normalize_project_context_pages(
            pages,
        )

        chunks = self._build_chunks(
            normalized_pages,
        )

        if not chunks:
            raise ProjectContextError(
                "В диапазоне ПЗ нет текста для индексации.",
            )

        if validation is not None:
            self._validate_snapshot(
                validation=validation,
                pages=normalized_pages,
            )

        started_at = asyncio.get_running_loop().time()

        embedding_started_at = asyncio.get_running_loop().time()

        vectors: list[list[float]] = []

        for start in range(
            0,
            len(
                chunks,
            ),
            self.embed_batch_size,
        ):
            batch = chunks[start : start + self.embed_batch_size]

            batch_vectors = await self.embedding_provider.embed(
                tuple(chunk.text for chunk in batch),
                instruction=None,
            )

            vectors.extend(
                batch_vectors,
            )

        embedding_finished_at = asyncio.get_running_loop().time()

        if len(
            vectors,
        ) != len(
            chunks,
        ):
            raise ProjectContextError(
                "Количество Project Context embeddings "
                "не совпадает с количеством chunks.",
            )

        if not vectors or not vectors[0]:
            raise ProjectContextError(
                "Project Context embedding пуст.",
            )

        vector_size = len(
            vectors[0],
        )

        if any(
            len(
                vector,
            )
            != vector_size
            for vector in vectors
        ):
            raise ProjectContextError(
                "Project Context embeddings имеют разную размерность.",
            )

        if vector_size != self.embedding_dimension:
            raise ProjectContextError(
                "Размерность Project Context embedding "
                "не совпадает с configured embedding identity.",
            )

        cache_context_id = resolved.context_id

        alias = project_context_collection_name(
            prefix=self.collection_prefix,
            context_id=cache_context_id,
        )

        build_id = uuid4()

        staging = project_context_staging_collection_name(
            prefix=self.collection_prefix,
            context_id=cache_context_id,
            build_id=build_id,
        )

        validation_payload = _validation_to_payload(
            validation,
        )

        cache_ready = validation_payload is not None

        records = tuple(
            VectorRecord(
                point_id=str(
                    uuid5(
                        NAMESPACE_URL,
                        (
                            "pdrd-project-context:"
                            f"{cache_context_id}:"
                            f"{chunk.page_number}:"
                            f"{chunk.chunk_index}"
                        ),
                    )
                ),
                vector=vector,
                payload={
                    "page": chunk.page_number,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                    _CACHE_KEY_FIELD: (resolved.cache_key),
                    _CACHE_READY_FIELD: (cache_ready),
                    _CACHE_SCHEMA_FIELD: (self.cache_schema_version),
                    _CACHE_MODEL_FIELD: (self.embedding_model),
                    _CACHE_DIMENSION_FIELD: (self.embedding_dimension),
                    _CACHE_EMBEDDING_SCHEMA_FIELD: (self.embedding_schema_version),
                    _CACHE_PAGES_COUNT_FIELD: len(
                        normalized_pages,
                    ),
                    _CACHE_CHUNKS_COUNT_FIELD: len(
                        chunks,
                    ),
                    _CACHE_VECTOR_SIZE_FIELD: (vector_size),
                    _CACHE_VALIDATION_FIELD: (validation_payload),
                },
            )
            for (
                chunk,
                vector,
            ) in zip(
                chunks,
                vectors,
                strict=True,
            )
        )

        previous_target = await self.vector_store.get_alias_target(
            alias,
        )

        created = False

        try:
            await self.vector_store.create_collection(
                collection=staging,
                vector_size=vector_size,
            )

            created = True

            for start in range(
                0,
                len(
                    records,
                ),
                self.upsert_batch_size,
            ):
                await self.vector_store.upsert(
                    collection=staging,
                    records=records[start : start + self.upsert_batch_size],
                )

            await self.vector_store.replace_aliases(
                {
                    alias: staging,
                }
            )

        except (
            EmbeddingProviderError,
            VectorStoreError,
        ):
            if created:
                with suppress(
                    VectorStoreError,
                ):
                    await self.vector_store.delete_collection(
                        collection=staging,
                    )

            raise

        if previous_target is not None and previous_target != staging:
            with suppress(
                VectorStoreError,
            ):
                await self.vector_store.delete_collection(
                    collection=previous_target,
                )

        finished_at = asyncio.get_running_loop().time()

        logger.info(
            (
                "project_context_cache_build "
                "hit=false "
                "context_id=%s "
                "cache_key=%s "
                "pages=%s "
                "chunks=%s "
                "embedding_ms=%.2f "
                "total_ms=%.2f "
                "ready=%s"
            ),
            cache_context_id,
            resolved.cache_key[:12],
            len(
                normalized_pages,
            ),
            len(
                chunks,
            ),
            (embedding_finished_at - embedding_started_at) * 1000,
            (finished_at - started_at) * 1000,
            cache_ready,
        )

        return ProjectContextInfo(
            context_id=cache_context_id,
            enabled=True,
            collection_name=alias,
            pages_count=len(
                normalized_pages,
            ),
            chunks_count=len(
                chunks,
            ),
            vector_size=vector_size,
            cache_key=resolved.cache_key,
            cache_hit=False,
            validation=validation,
        )

    def _build_chunks(
        self,
        pages: tuple[
            ProjectContextTextPage,
            ...,
        ],
    ) -> tuple[
        ProjectContextChunk,
        ...,
    ]:
        """Разбивает все страницы ПЗ на chunks."""
        result: list[ProjectContextChunk] = []

        for page in pages:
            page_chunks = chunk_project_context_text(
                page.text,
                chunk_size=self.chunk_size,
                overlap=self.chunk_overlap,
            )

            result.extend(
                ProjectContextChunk(
                    page_number=page.page_number,
                    chunk_index=index,
                    text=text,
                )
                for (
                    index,
                    text,
                ) in enumerate(
                    page_chunks,
                    start=1,
                )
            )

        return tuple(
            result,
        )

    @staticmethod
    def _validate_snapshot(
        *,
        validation: ProjectContextValidationSnapshot,
        pages: tuple[
            ProjectContextTextPage,
            ...,
        ],
    ) -> None:
        """Проверяет согласованность VLM validation и cache input."""
        if not validation.enabled:
            raise ProjectContextError(
                "Enabled Project Context получил disabled validation snapshot.",
            )

        if validation.pages_count != len(
            pages,
        ):
            raise ProjectContextError(
                "Количество страниц validation snapshot не совпадает с диапазоном ПЗ.",
            )

        expected_pages = tuple(page.page_number for page in pages)

        actual_pages = tuple(item.page_number for item in validation.classifications)

        if actual_pages != expected_pages:
            raise ProjectContextError(
                "Validation snapshot не соответствует ordered диапазону страниц ПЗ.",
            )


@dataclass(frozen=True, slots=True)
class SearchProjectContext:
    """Ищет релевантные chunks в ПЗ текущего проекта."""

    embedding_provider: EmbeddingProvider
    vector_store: VectorStore

    collection_prefix: str
    embedding_model: str

    top_k: int

    async def execute(
        self,
        *,
        context_id: UUID,
        enabled: bool,
        query: str,
    ) -> ProjectContextSearchResult:
        """Выполняет instruction-aware semantic retrieval."""
        normalized = normalize_project_context_text(
            query,
        )

        if not enabled or not normalized:
            return ProjectContextSearchResult(
                context_id=context_id,
                query=normalized,
                sources=(),
                embedding_model=(self.embedding_model),
            )

        collection = project_context_collection_name(
            prefix=self.collection_prefix,
            context_id=context_id,
        )

        vectors = await self.embedding_provider.embed(
            (normalized,),
            instruction=(PROJECT_CONTEXT_QUERY_INSTRUCTION),
        )

        if (
            len(
                vectors,
            )
            != 1
            or not vectors[0]
        ):
            raise ProjectContextError(
                "Не удалось построить Project Context query embedding.",
            )

        points = await self.vector_store.search(
            collection=collection,
            vector=vectors[0],
            limit=self.top_k,
        )

        sources = tuple(
            ProjectContextSource(
                source_id=f"PZ{index}",
                point_id=point.point_id,
                score=round(
                    point.score,
                    4,
                ),
                page=self._optional_int(
                    point.payload.get(
                        "page",
                    )
                ),
                chunk_index=(
                    self._optional_int(
                        point.payload.get(
                            "chunk_index",
                        )
                    )
                ),
                text=str(
                    point.payload.get(
                        "text",
                        "",
                    )
                    or ""
                ),
            )
            for (
                index,
                point,
            ) in enumerate(
                points,
                start=1,
            )
        )

        return ProjectContextSearchResult(
            context_id=context_id,
            query=normalized,
            sources=sources,
            embedding_model=(self.embedding_model),
        )

    @staticmethod
    def _optional_int(
        value: object,
    ) -> int | None:
        """Возвращает int metadata либо None."""
        try:
            if value is None:
                return None

            return int(
                value,
            )

        except (
            TypeError,
            ValueError,
        ):
            return None


@dataclass(frozen=True, slots=True)
class DeleteProjectContext:
    """Явно удаляет Project Context cache по context_id."""

    vector_store: VectorStore

    collection_prefix: str

    async def execute(
        self,
        *,
        context_id: UUID,
    ) -> bool:
        """Удаляет published cache target либо legacy collection."""
        collection = project_context_collection_name(
            prefix=self.collection_prefix,
            context_id=context_id,
        )

        target = await self.vector_store.get_alias_target(
            collection,
        )

        if target is not None:
            return await self.vector_store.delete_collection(
                collection=target,
            )

        if not await self.vector_store.collection_exists(
            collection,
        ):
            return False

        return await self.vector_store.delete_collection(
            collection=collection,
        )
