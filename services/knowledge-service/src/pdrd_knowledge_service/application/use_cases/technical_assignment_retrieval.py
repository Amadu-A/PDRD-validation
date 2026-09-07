# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/technical_assignment_retrieval.py

"""T-guided retrieval технического задания и нормативов."""

import asyncio
from dataclasses import (
    dataclass,
    replace,
)
from typing import Any
from uuid import UUID

from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingInput,
    MultimodalEmbeddingProvider,
)
from pdrd_knowledge_service.application.ports.persistence import (
    NormativeCatalogUnitOfWorkFactory,
)
from pdrd_knowledge_service.application.ports.technical_assignment_persistence import (
    TechnicalAssignmentUnitOfWorkFactory,
)
from pdrd_knowledge_service.application.ports.vector_store import (
    VectorStore,
)
from pdrd_knowledge_service.application.use_cases.normative import (
    SearchNormative,
)
from pdrd_knowledge_service.application.use_cases.technical_assignments import (
    TechnicalAssignmentNotFoundError,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    CatalogArea,
    IndexingStatus,
    NormativeDocument,
)
from pdrd_knowledge_service.domain.search import (
    NormativeSearchResult,
    NormativeSource,
    VectorPoint,
    VectorSearchCondition,
    VectorSearchFilter,
)
from pdrd_knowledge_service.domain.technical_assignment import (
    TechnicalAssignmentIndexStatus,
)
from pdrd_knowledge_service.domain.technical_assignment_retrieval import (
    NormativeReferenceResolution,
    NormativeReferenceResolutionStatus,
    RetrievalDiagnostic,
    TechnicalAssignmentConflictCandidate,
    TechnicalAssignmentGuidedSearchResult,
    TechnicalAssignmentSearchResult,
    TechnicalAssignmentSource,
)

TECHNICAL_ASSIGNMENT_QUERY_INSTRUCTION = (
    "Retrieve the technical-assignment page most directly applicable "
    "to this engineering drawing check. Match project requirements, "
    "equipment constraints, design conditions, diagrams and referenced "
    "standards."
)

_TARGETED_QUERY_TEXT_LIMIT = 1800


class TechnicalAssignmentSearchScopeError(
    ValueError,
):
    """T-search вызван с несовместимым analysis scope."""


class TechnicalAssignmentSearchConflictError(
    RuntimeError,
):
    """ТЗ существует, но ещё не готово к retrieval."""


@dataclass(frozen=True, slots=True)
class SearchTechnicalAssignment:
    """Ищет T отдельно от N/U/E retrieval budgets."""

    embedding_provider: MultimodalEmbeddingProvider

    vector_store: VectorStore

    unit_of_work_factory: TechnicalAssignmentUnitOfWorkFactory

    collection: str

    embedding_model: str

    top_k: int

    max_sources: int

    async def execute(
        self,
        queries: list[str],
        *,
        technical_assignment_id: UUID,
        section_id: UUID,
    ) -> TechnicalAssignmentSearchResult:
        """Ищет релевантные страницы одного READY ТЗ."""
        await self._validate_assignment(
            technical_assignment_id=technical_assignment_id,
            section_id=section_id,
        )

        normalized_queries = self._normalize_queries(
            queries,
        )

        if not normalized_queries:
            return TechnicalAssignmentSearchResult(
                queries=(),
                sources=(),
                embedding_model=self.embedding_model,
            )

        inputs = tuple(
            MultimodalEmbeddingInput(
                text=query,
                instruction=(TECHNICAL_ASSIGNMENT_QUERY_INSTRUCTION),
            )
            for query in normalized_queries
        )

        try:
            vectors = await self.embedding_provider.embed(
                inputs,
            )

            search_filter = VectorSearchFilter(
                must=(
                    VectorSearchCondition(
                        key="technical_assignment_id",
                        values=(
                            str(
                                technical_assignment_id,
                            ),
                        ),
                    ),
                    VectorSearchCondition(
                        key="section_id",
                        values=(
                            str(
                                section_id,
                            ),
                        ),
                    ),
                    VectorSearchCondition(
                        key="source_type",
                        values=("technical_assignment",),
                    ),
                    VectorSearchCondition(
                        key="representation",
                        values=("page_multimodal",),
                    ),
                )
            )

            groups = await asyncio.gather(
                *(
                    self.vector_store.search_filtered(
                        collection=self.collection,
                        vector=vector,
                        limit=self.top_k,
                        search_filter=search_filter,
                    )
                    for vector in vectors
                )
            )

        finally:
            await self.embedding_provider.release()

        merged = self._merge_points(
            groups,
        )

        sources = tuple(
            self._build_source(
                point,
                index=index,
            )
            for index, point in enumerate(
                merged,
                start=1,
            )
        )

        return TechnicalAssignmentSearchResult(
            queries=normalized_queries,
            sources=sources,
            embedding_model=self.embedding_model,
        )

    async def _validate_assignment(
        self,
        *,
        technical_assignment_id: UUID,
        section_id: UUID,
    ) -> None:
        """Проверяет immutable T scope перед GPU retrieval."""
        async with self.unit_of_work_factory() as unit_of_work:
            assignment = await unit_of_work.assignments.get(
                technical_assignment_id,
            )

        if assignment is None:
            raise TechnicalAssignmentNotFoundError(
                f"Техническое задание {technical_assignment_id} не найдено.",
            )

        if assignment.section_id != section_id:
            raise TechnicalAssignmentSearchScopeError(
                "ТЗ принадлежит другому нормативному разделу.",
            )

        if assignment.index_status is not TechnicalAssignmentIndexStatus.READY:
            raise TechnicalAssignmentSearchConflictError(
                "ТЗ ещё не готово к multimodal retrieval: "
                f"{assignment.index_status.value}.",
            )

    @staticmethod
    def _normalize_queries(
        queries: list[str],
    ) -> tuple[
        str,
        ...,
    ]:
        """Удаляет пустые и повторные T queries."""
        result: list[str] = []

        seen: set[str] = set()

        for query in queries:
            normalized = query.strip()

            if not normalized or normalized in seen:
                continue

            seen.add(
                normalized,
            )

            result.append(
                normalized,
            )

        return tuple(
            result,
        )

    def _merge_points(
        self,
        groups: tuple[
            list[VectorPoint],
            ...,
        ],
    ) -> list[VectorPoint]:
        """Дедуплицирует T pages по point_id."""
        by_id: dict[
            str,
            VectorPoint,
        ] = {}

        for points in groups:
            for point in points:
                if not point.point_id:
                    continue

                previous = by_id.get(
                    point.point_id,
                )

                if previous is None or point.score > previous.score:
                    by_id[point.point_id] = point

        return sorted(
            by_id.values(),
            key=lambda point: point.score,
            reverse=True,
        )[: self.max_sources]

    @staticmethod
    def _build_source(
        point: VectorPoint,
        *,
        index: int,
    ) -> TechnicalAssignmentSource:
        """Преобразует T Qdrant payload в domain source."""
        payload = point.payload

        return TechnicalAssignmentSource(
            source_id=f"T{index}",
            point_id=point.point_id,
            score=round(
                point.score,
                4,
            ),
            technical_assignment_id=(
                SearchTechnicalAssignment._optional_string(
                    payload.get(
                        "technical_assignment_id",
                    )
                )
            ),
            analysis_document_id=(
                SearchTechnicalAssignment._optional_string(
                    payload.get(
                        "analysis_document_id",
                    )
                )
            ),
            section_id=(
                SearchTechnicalAssignment._optional_string(
                    payload.get(
                        "section_id",
                    )
                )
            ),
            source_sha256=(
                SearchTechnicalAssignment._optional_string(
                    payload.get(
                        "source_sha256",
                    )
                )
            ),
            source_file=(
                SearchTechnicalAssignment._optional_string(
                    payload.get(
                        "source_file",
                    )
                )
            ),
            page=SearchTechnicalAssignment._page_value(
                payload.get(
                    "page",
                )
            ),
            text=str(
                payload.get(
                    "text",
                    "",
                )
                or ""
            ),
            normative_refs=(
                SearchTechnicalAssignment._normative_refs(
                    payload.get(
                        "normative_refs",
                    )
                )
            ),
        )

    @staticmethod
    def _normative_refs(
        value: Any,
    ) -> tuple[
        str,
        ...,
    ]:
        """Нормализует references из Qdrant payload."""
        if not isinstance(
            value,
            (
                list,
                tuple,
            ),
        ):
            return ()

        result: list[str] = []

        seen: set[str] = set()

        for item in value:
            if not isinstance(
                item,
                str,
            ):
                continue

            normalized = item.strip()

            if not normalized or normalized in seen:
                continue

            seen.add(
                normalized,
            )

            result.append(
                normalized,
            )

        return tuple(
            result,
        )

    @staticmethod
    def _optional_string(
        value: Any,
    ) -> str | None:
        """Преобразует optional payload value в string."""
        if value is None:
            return None

        return str(
            value,
        )

    @staticmethod
    def _page_value(
        value: Any,
    ) -> int | str | None:
        """Нормализует page payload."""
        if isinstance(
            value,
            (
                int,
                str,
            ),
        ):
            return value

        return None


@dataclass(frozen=True, slots=True)
class SearchTechnicalAssignmentGuidedNormative:
    """Оркестрирует separate-budget T-guided N retrieval."""

    search_technical_assignment: SearchTechnicalAssignment

    search_normative: SearchNormative

    normative_catalog_uow_factory: NormativeCatalogUnitOfWorkFactory

    combined_max_sources: int

    async def execute(
        self,
        queries: list[str],
        *,
        technical_assignment_id: UUID,
        section_id: UUID,
        normative_document_ids: list[UUID],
    ) -> TechnicalAssignmentGuidedSearchResult:
        """Ищет T, general N и T-targeted N без score multiplication."""
        technical_assignment_result = await self.search_technical_assignment.execute(
            queries,
            technical_assignment_id=technical_assignment_id,
            section_id=section_id,
        )

        general_normative_result = await self.search_normative.execute(
            queries,
            section_id=section_id,
            document_ids=normative_document_ids,
            expected_area=CatalogArea.NORMATIVE,
            source_prefix="GN",
            allow_unscoped=False,
        )

        references = self._collect_references(
            technical_assignment_result.sources,
        )

        resolutions = await self._resolve_references(
            section_id=section_id,
            normative_document_ids=(normative_document_ids),
            references=references,
        )

        targeted_document_ids = self._resolved_document_ids(
            resolutions,
        )

        targeted_queries = self._build_targeted_queries(
            technical_assignment_result.sources,
            resolutions,
        )

        if targeted_document_ids and targeted_queries:
            targeted_normative_result = await self.search_normative.execute(
                list(
                    targeted_queries,
                ),
                section_id=section_id,
                document_ids=list(
                    targeted_document_ids,
                ),
                expected_area=(CatalogArea.NORMATIVE),
                source_prefix="TN",
                allow_unscoped=False,
            )

        else:
            targeted_normative_result = NormativeSearchResult(
                queries=targeted_queries,
                sources=(),
                embedding_model=(general_normative_result.embedding_model),
            )

        normative_sources = self._merge_normative_sources(
            targeted=(targeted_normative_result.sources),
            general=(general_normative_result.sources),
        )

        diagnostics = self._build_diagnostics(
            technical_assignment_sources=(technical_assignment_result.sources),
            resolutions=resolutions,
            targeted_normative_sources=(targeted_normative_result.sources),
        )

        conflict_candidates = self._build_conflict_candidates(
            technical_assignment_sources=(technical_assignment_result.sources),
            resolutions=resolutions,
            normative_sources=normative_sources,
        )

        return TechnicalAssignmentGuidedSearchResult(
            queries=(technical_assignment_result.queries),
            technical_assignment_sources=(technical_assignment_result.sources),
            reference_resolutions=resolutions,
            targeted_normative_sources=(targeted_normative_result.sources),
            general_normative_sources=(general_normative_result.sources),
            normative_sources=normative_sources,
            diagnostics=diagnostics,
            conflict_candidates=(conflict_candidates),
            technical_assignment_embedding_model=(
                technical_assignment_result.embedding_model
            ),
            normative_embedding_model=(general_normative_result.embedding_model),
        )

    async def _resolve_references(
        self,
        *,
        section_id: UUID,
        normative_document_ids: list[UUID],
        references: tuple[
            str,
            ...,
        ],
    ) -> tuple[
        NormativeReferenceResolution,
        ...,
    ]:
        """Сопоставляет T references только с immutable N snapshot."""
        if not references:
            return ()

        normalized_ids = tuple(
            dict.fromkeys(
                normative_document_ids,
            )
        )

        async with self.normative_catalog_uow_factory() as unit_of_work:
            documents = await unit_of_work.documents.list_by_ids(
                normalized_ids,
            )

        usable_documents = tuple(
            document
            for document in documents
            if (
                document.section_id == section_id
                and document.area is CatalogArea.NORMATIVE
                and document.index_status is IndexingStatus.READY
            )
        )

        return tuple(
            self._resolve_reference(
                reference,
                usable_documents,
            )
            for reference in references
        )

    @staticmethod
    def _resolve_reference(
        reference: str,
        documents: tuple[
            NormativeDocument,
            ...,
        ],
    ) -> NormativeReferenceResolution:
        """Разрешает одну textual N reference в managed documents."""
        normalized_reference = (
            SearchTechnicalAssignmentGuidedNormative._normalize_reference(
                reference,
            )
        )

        matches = tuple(
            document
            for document in documents
            if (
                normalized_reference
                and normalized_reference
                in (
                    SearchTechnicalAssignmentGuidedNormative._normalize_reference(
                        document.original_name,
                    )
                )
            )
        )

        if (
            len(
                matches,
            )
            == 1
        ):
            status = NormativeReferenceResolutionStatus.RESOLVED

        elif not matches:
            status = NormativeReferenceResolutionStatus.MISSING

        else:
            status = NormativeReferenceResolutionStatus.AMBIGUOUS

        return NormativeReferenceResolution(
            reference=reference,
            normalized_reference=(normalized_reference),
            status=status,
            document_ids=tuple(
                str(
                    document.document_id,
                )
                for document in matches
            ),
            source_files=tuple(document.original_name for document in matches),
        )

    @staticmethod
    def _normalize_reference(
        value: str,
    ) -> str:
        """Приводит имя N/reference к comparison key."""
        normalized = value.upper().replace(
            "Ё",
            "Е",
        )

        return "".join(character for character in normalized if character.isalnum())

    @staticmethod
    def _collect_references(
        sources: tuple[
            TechnicalAssignmentSource,
            ...,
        ],
    ) -> tuple[
        str,
        ...,
    ]:
        """Собирает unique N references из найденных T pages."""
        result: list[str] = []

        seen: set[str] = set()

        for source in sources:
            for reference in source.normative_refs:
                normalized = (
                    SearchTechnicalAssignmentGuidedNormative._normalize_reference(
                        reference,
                    )
                )

                if not normalized or normalized in seen:
                    continue

                seen.add(
                    normalized,
                )

                result.append(
                    reference,
                )

        return tuple(
            result,
        )

    @staticmethod
    def _resolved_document_ids(
        resolutions: tuple[
            NormativeReferenceResolution,
            ...,
        ],
    ) -> tuple[
        UUID,
        ...,
    ]:
        """Возвращает exact document IDs resolved T references."""
        result: list[UUID] = []

        seen: set[UUID] = set()

        for resolution in resolutions:
            if resolution.status is not (NormativeReferenceResolutionStatus.RESOLVED):
                continue

            for document_id in resolution.document_ids:
                parsed = UUID(
                    document_id,
                )

                if parsed in seen:
                    continue

                seen.add(
                    parsed,
                )

                result.append(
                    parsed,
                )

        return tuple(
            result,
        )

    @classmethod
    def _build_targeted_queries(
        cls,
        sources: tuple[
            TechnicalAssignmentSource,
            ...,
        ],
        resolutions: tuple[
            NormativeReferenceResolution,
            ...,
        ],
    ) -> tuple[
        str,
        ...,
    ]:
        """Строит N queries непосредственно из требований T."""
        resolved_by_key = {
            resolution.normalized_reference: (resolution)
            for resolution in resolutions
            if (resolution.status is (NormativeReferenceResolutionStatus.RESOLVED))
        }

        result: list[str] = []

        seen: set[str] = set()

        for source in sources:
            resolved_references = [
                reference
                for reference in source.normative_refs
                if (
                    cls._normalize_reference(
                        reference,
                    )
                    in resolved_by_key
                )
            ]

            if not resolved_references:
                continue

            source_text = source.text.strip()[:_TARGETED_QUERY_TEXT_LIMIT]

            reference_text = ", ".join(
                resolved_references,
            )

            if source_text:
                query = (
                    "Требование технического задания:\n"
                    f"{source_text}\n"
                    "Ссылки ТЗ на нормативные документы: "
                    f"{reference_text}"
                )

            else:
                query = (
                    "Ссылки технического задания "
                    "на нормативные документы: "
                    f"{reference_text}"
                )

            if query in seen:
                continue

            seen.add(
                query,
            )

            result.append(
                query,
            )

        return tuple(
            result,
        )

    def _merge_normative_sources(
        self,
        *,
        targeted: tuple[
            NormativeSource,
            ...,
        ],
        general: tuple[
            NormativeSource,
            ...,
        ],
    ) -> tuple[
        NormativeSource,
        ...,
    ]:
        """Гарантирует targeted N перед general N и дедуплицирует."""
        ordered: list[NormativeSource] = []

        seen_points: set[str] = set()

        for source in (
            *targeted,
            *general,
        ):
            if source.point_id in seen_points:
                continue

            seen_points.add(
                source.point_id,
            )

            ordered.append(
                source,
            )

            if (
                len(
                    ordered,
                )
                >= self.combined_max_sources
            ):
                break

        return tuple(
            replace(
                source,
                source_id=f"N{index}",
            )
            for index, source in enumerate(
                ordered,
                start=1,
            )
        )

    @classmethod
    def _build_diagnostics(
        cls,
        *,
        technical_assignment_sources: tuple[
            TechnicalAssignmentSource,
            ...,
        ],
        resolutions: tuple[
            NormativeReferenceResolution,
            ...,
        ],
        targeted_normative_sources: tuple[
            NormativeSource,
            ...,
        ],
    ) -> tuple[
        RetrievalDiagnostic,
        ...,
    ]:
        """Формирует только проверяемые retrieval diagnostics."""
        diagnostics: list[RetrievalDiagnostic] = []

        if not technical_assignment_sources:
            diagnostics.append(
                RetrievalDiagnostic(
                    code=("technical_assignment_no_hits"),
                    message=("По запросам не найдено релевантных страниц ТЗ."),
                )
            )

        for resolution in resolutions:
            if resolution.status is (NormativeReferenceResolutionStatus.MISSING):
                diagnostics.append(
                    RetrievalDiagnostic(
                        code=("missing_referenced_normative"),
                        message=(
                            "ТЗ ссылается на нормативный "
                            "документ, которого нет "
                            "в immutable N snapshot."
                        ),
                        reference=(resolution.reference),
                    )
                )

            elif resolution.status is (NormativeReferenceResolutionStatus.AMBIGUOUS):
                diagnostics.append(
                    RetrievalDiagnostic(
                        code=("ambiguous_referenced_normative"),
                        message=(
                            "Ссылка ТЗ соответствует "
                            "нескольким нормативным "
                            "документам snapshot."
                        ),
                        reference=(resolution.reference),
                    )
                )

        targeted_document_ids = {
            source.document_id
            for source in targeted_normative_sources
            if source.document_id is not None
        }

        for resolution in resolutions:
            if resolution.status is not (NormativeReferenceResolutionStatus.RESOLVED):
                continue

            for document_id in resolution.document_ids:
                if document_id in targeted_document_ids:
                    continue

                diagnostics.append(
                    RetrievalDiagnostic(
                        code=("referenced_normative_no_hit"),
                        message=(
                            "Норматив из ссылки ТЗ "
                            "разрешён, но targeted "
                            "vector search не вернул "
                            "релевантный fragment."
                        ),
                        reference=(resolution.reference),
                    )
                )

        return tuple(
            diagnostics,
        )

    @classmethod
    def _build_conflict_candidates(
        cls,
        *,
        technical_assignment_sources: tuple[
            TechnicalAssignmentSource,
            ...,
        ],
        resolutions: tuple[
            NormativeReferenceResolution,
            ...,
        ],
        normative_sources: tuple[
            NormativeSource,
            ...,
        ],
    ) -> tuple[
        TechnicalAssignmentConflictCandidate,
        ...,
    ]:
        """Готовит T/N pairs для semantic validation на TZ-5."""
        resolved_by_key = {
            resolution.normalized_reference: (resolution)
            for resolution in resolutions
            if (resolution.status is (NormativeReferenceResolutionStatus.RESOLVED))
        }

        normative_by_document: dict[
            str,
            list[str],
        ] = {}

        for source in normative_sources:
            if source.document_id is None:
                continue

            normative_by_document.setdefault(
                source.document_id,
                [],
            ).append(
                source.source_id,
            )

        candidates: list[TechnicalAssignmentConflictCandidate] = []

        for source in technical_assignment_sources:
            resolved_document_ids: list[str] = []

            for reference in source.normative_refs:
                resolution = resolved_by_key.get(
                    cls._normalize_reference(
                        reference,
                    )
                )

                if resolution is None:
                    continue

                resolved_document_ids.extend(
                    resolution.document_ids,
                )

            normative_source_ids: list[str] = []

            seen_source_ids: set[str] = set()

            for document_id in resolved_document_ids:
                for normative_source_id in normative_by_document.get(
                    document_id,
                    [],
                ):
                    if normative_source_id in seen_source_ids:
                        continue

                    seen_source_ids.add(
                        normative_source_id,
                    )

                    normative_source_ids.append(
                        normative_source_id,
                    )

            if not normative_source_ids:
                continue

            candidates.append(
                TechnicalAssignmentConflictCandidate(
                    technical_assignment_source_id=(source.source_id),
                    normative_source_ids=tuple(
                        normative_source_ids,
                    ),
                    reason=(
                        "Требование ТЗ и связанный "
                        "норматив должны быть "
                        "семантически сопоставлены "
                        "до вывода о конфликте."
                    ),
                )
            )

        return tuple(
            candidates,
        )
