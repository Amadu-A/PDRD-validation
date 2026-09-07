# services/knowledge-service/tests/unit/test_technical_assignment_guided_retrieval.py

"""Unit tests T-guided normative retrieval."""

from datetime import (
    UTC,
    datetime,
)
from uuid import (
    UUID,
    uuid4,
)

import pytest
from pdrd_knowledge_service.application.use_cases.technical_assignment_retrieval import (
    SearchTechnicalAssignment,
    SearchTechnicalAssignmentGuidedNormative,
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
)
from pdrd_knowledge_service.domain.technical_assignment import (
    PDF_MIME_TYPE,
    TechnicalAssignment,
    TechnicalAssignmentIndexStatus,
)
from pdrd_knowledge_service.domain.technical_assignment_retrieval import (
    NormativeReferenceResolutionStatus,
    TechnicalAssignmentSearchResult,
    TechnicalAssignmentSource,
)

NOW = datetime(
    2026,
    9,
    7,
    12,
    0,
    tzinfo=UTC,
)


def _normative_document(
    *,
    document_id: UUID,
    section_id: UUID,
    original_name: str,
) -> NormativeDocument:
    """Создаёт READY normative document."""
    return NormativeDocument(
        document_id=document_id,
        section_id=section_id,
        category_id=None,
        original_name=original_name,
        storage_key=f"{document_id}.pdf",
        mime_type=PDF_MIME_TYPE,
        size_bytes=1024,
        sha256="a" * 64,
        index_status=IndexingStatus.READY,
        index_error=None,
        indexed_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        area=CatalogArea.NORMATIVE,
    )


def _technical_assignment(
    *,
    technical_assignment_id: UUID,
    analysis_document_id: UUID,
    section_id: UUID,
) -> TechnicalAssignment:
    """Создаёт READY technical assignment."""
    return TechnicalAssignment(
        technical_assignment_id=(technical_assignment_id),
        analysis_document_id=(analysis_document_id),
        section_id=section_id,
        original_name="technical-assignment.pdf",
        mime_type=PDF_MIME_TYPE,
        size_bytes=2048,
        sha256="b" * 64,
        index_status=(TechnicalAssignmentIndexStatus.READY),
        index_error=None,
        indexed_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def _normative_source(
    *,
    source_id: str,
    point_id: str,
    document_id: UUID,
    section_id: UUID,
    score: float,
) -> NormativeSource:
    """Создаёт N source."""
    return NormativeSource(
        source_id=source_id,
        point_id=point_id,
        score=score,
        document_id=str(
            document_id,
        ),
        section_id=str(
            section_id,
        ),
        category_id=None,
        source_sha256="c" * 64,
        source_file="normative.pdf",
        source_path=None,
        page=1,
        chunk_index=0,
        text="Normative requirement.",
    )


class _FakeMultimodalProvider:
    """Fake multimodal provider."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует counters."""
        self.release_calls = 0

    async def embed(
        self,
        inputs: tuple[
            object,
            ...,
        ],
    ) -> list[list[float]]:
        """Возвращает deterministic vectors."""
        return [
            [
                float(
                    index + 1,
                ),
                0.5,
            ]
            for index, _ in enumerate(
                inputs,
            )
        ]

    async def release(
        self,
    ) -> None:
        """Фиксирует explicit release."""
        self.release_calls += 1

    async def is_ready(
        self,
    ) -> bool:
        """Всегда ready."""
        return True


class _FakeVectorStore:
    """Fake vector search storage."""

    def __init__(
        self,
        points: list[VectorPoint],
    ) -> None:
        """Сохраняет points."""
        self.points = points

        self.calls: list[
            dict[
                str,
                object,
            ]
        ] = []

    async def search_filtered(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
        search_filter: object,
    ) -> list[VectorPoint]:
        """Возвращает configured points."""
        self.calls.append(
            {
                "collection": collection,
                "vector": vector,
                "limit": limit,
                "search_filter": search_filter,
            }
        )

        return self.points


class _FakeTechnicalAssignmentRepository:
    """Fake T repository."""

    def __init__(
        self,
        assignment: TechnicalAssignment,
    ) -> None:
        """Сохраняет assignment."""
        self.assignment = assignment

    async def get(
        self,
        technical_assignment_id: UUID,
    ) -> TechnicalAssignment | None:
        """Возвращает assignment по ID."""
        if technical_assignment_id == self.assignment.technical_assignment_id:
            return self.assignment

        return None


class _FakeTechnicalAssignmentUnitOfWork:
    """Fake T UoW."""

    def __init__(
        self,
        assignment: TechnicalAssignment,
    ) -> None:
        """Создаёт repositories."""
        self.assignments = _FakeTechnicalAssignmentRepository(
            assignment,
        )

    async def __aenter__(
        self,
    ) -> "_FakeTechnicalAssignmentUnitOfWork":
        """Входит в context."""
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        """Выходит из context."""


class _FakeDocumentRepository:
    """Fake managed document repository."""

    def __init__(
        self,
        documents: tuple[
            NormativeDocument,
            ...,
        ],
    ) -> None:
        """Сохраняет documents."""
        self.documents = documents

    async def list_by_ids(
        self,
        document_ids: tuple[
            UUID,
            ...,
        ],
    ) -> list[NormativeDocument]:
        """Возвращает docs из snapshot."""
        requested = set(
            document_ids,
        )

        return [
            document for document in self.documents if document.document_id in requested
        ]


class _FakeNormativeUnitOfWork:
    """Fake normative UoW."""

    def __init__(
        self,
        documents: tuple[
            NormativeDocument,
            ...,
        ],
    ) -> None:
        """Создаёт repository."""
        self.documents = _FakeDocumentRepository(
            documents,
        )

    async def __aenter__(
        self,
    ) -> "_FakeNormativeUnitOfWork":
        """Входит в context."""
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        """Выходит из context."""


class _FakeTechnicalSearch:
    """Fake SearchTechnicalAssignment."""

    def __init__(
        self,
        result: TechnicalAssignmentSearchResult,
    ) -> None:
        """Сохраняет result."""
        self.result = result

    async def execute(
        self,
        queries: list[str],
        *,
        technical_assignment_id: UUID,
        section_id: UUID,
    ) -> TechnicalAssignmentSearchResult:
        """Возвращает configured result."""
        del queries
        del technical_assignment_id
        del section_id

        return self.result


class _FakeNormativeSearch:
    """Fake SearchNormative с отдельными channels."""

    def __init__(
        self,
        *,
        general: NormativeSearchResult,
        targeted: NormativeSearchResult,
    ) -> None:
        """Сохраняет channel results."""
        self.general = general

        self.targeted = targeted

        self.calls: list[
            dict[
                str,
                object,
            ]
        ] = []

    async def execute(
        self,
        queries: list[str],
        *,
        section_id: UUID | None = None,
        document_ids: list[UUID] | None = None,
        expected_area: CatalogArea = (CatalogArea.NORMATIVE),
        source_prefix: str = "N",
        query_instruction: str = "",
        allow_unscoped: bool = True,
    ) -> NormativeSearchResult:
        """Возвращает channel result по prefix."""
        del query_instruction

        self.calls.append(
            {
                "queries": queries,
                "section_id": section_id,
                "document_ids": document_ids,
                "expected_area": expected_area,
                "source_prefix": source_prefix,
                "allow_unscoped": allow_unscoped,
            }
        )

        if source_prefix == "TN":
            return self.targeted

        return self.general


@pytest.mark.asyncio
async def test_search_t_uses_separate_collection_and_releases_gpu() -> None:
    """T retrieval имеет свой Qdrant scope и explicit release."""
    section_id = uuid4()

    technical_assignment_id = uuid4()

    assignment = _technical_assignment(
        technical_assignment_id=(technical_assignment_id),
        analysis_document_id=uuid4(),
        section_id=section_id,
    )

    provider = _FakeMultimodalProvider()

    vector_store = _FakeVectorStore(
        points=[
            VectorPoint(
                point_id="t-point-1",
                score=0.91,
                payload={
                    "technical_assignment_id": str(
                        technical_assignment_id,
                    ),
                    "analysis_document_id": str(
                        assignment.analysis_document_id,
                    ),
                    "section_id": str(
                        section_id,
                    ),
                    "source_sha256": "b" * 64,
                    "source_file": ("technical-assignment.pdf"),
                    "page": 3,
                    "text": ("Шкаф управления IP54."),
                    "normative_refs": [
                        "СП 76.13330.2016",
                    ],
                },
            )
        ],
    )

    use_case = SearchTechnicalAssignment(
        embedding_provider=provider,
        vector_store=vector_store,
        unit_of_work_factory=lambda: _FakeTechnicalAssignmentUnitOfWork(
            assignment,
        ),
        collection="dva_multimodal_test",
        embedding_model=("Qwen/Qwen3-VL-Embedding-2B"),
        top_k=6,
        max_sources=12,
    )

    result = await use_case.execute(
        [
            "Проверить степень защиты шкафа.",
        ],
        technical_assignment_id=(technical_assignment_id),
        section_id=section_id,
    )

    assert provider.release_calls == 1

    assert (
        len(
            vector_store.calls,
        )
        == 1
    )

    call = vector_store.calls[0]

    assert call["collection"] == "dva_multimodal_test"

    search_filter = call["search_filter"]

    conditions = {condition.key: condition.values for condition in search_filter.must}

    assert conditions["technical_assignment_id"] == (
        str(
            technical_assignment_id,
        ),
    )

    assert conditions["section_id"] == (
        str(
            section_id,
        ),
    )

    assert conditions["source_type"] == ("technical_assignment",)

    assert conditions["representation"] == ("page_multimodal",)

    assert result.sources[0].source_id == "T1"

    assert result.sources[0].page == 3

    assert result.sources[0].normative_refs == ("СП 76.13330.2016",)


@pytest.mark.asyncio
async def test_guided_search_prioritizes_targeted_n_and_deduplicates() -> None:
    """Targeted N гарантированно идёт перед general N."""
    section_id = uuid4()

    technical_assignment_id = uuid4()

    referenced_document_id = uuid4()

    general_document_id = uuid4()

    referenced_document = _normative_document(
        document_id=(referenced_document_id),
        section_id=section_id,
        original_name=("СП 76.13330.2016.pdf"),
    )

    general_document = _normative_document(
        document_id=general_document_id,
        section_id=section_id,
        original_name=("ГОСТ Р 21.101-2020.pdf"),
    )

    technical_result = TechnicalAssignmentSearchResult(
        queries=("Проверить шкаф IP54.",),
        sources=(
            TechnicalAssignmentSource(
                source_id="T1",
                point_id="t-1",
                score=0.95,
                technical_assignment_id=str(
                    technical_assignment_id,
                ),
                analysis_document_id=str(
                    uuid4(),
                ),
                section_id=str(
                    section_id,
                ),
                source_sha256="b" * 64,
                source_file="tz.pdf",
                page=2,
                text=("Шкаф должен иметь IP54. СП 76.13330.2016."),
                normative_refs=("СП 76.13330.2016",),
            ),
        ),
        embedding_model=("Qwen/Qwen3-VL-Embedding-2B"),
    )

    targeted = NormativeSearchResult(
        queries=("targeted",),
        sources=(
            _normative_source(
                source_id="TN1",
                point_id="shared-point",
                document_id=(referenced_document_id),
                section_id=section_id,
                score=0.61,
            ),
            _normative_source(
                source_id="TN2",
                point_id="target-only",
                document_id=(referenced_document_id),
                section_id=section_id,
                score=0.60,
            ),
        ),
        embedding_model=("qwen3-embedding:4b"),
    )

    general = NormativeSearchResult(
        queries=("general",),
        sources=(
            _normative_source(
                source_id="GN1",
                point_id="shared-point",
                document_id=(referenced_document_id),
                section_id=section_id,
                score=0.99,
            ),
            _normative_source(
                source_id="GN2",
                point_id="general-only",
                document_id=(general_document_id),
                section_id=section_id,
                score=0.98,
            ),
        ),
        embedding_model=("qwen3-embedding:4b"),
    )

    normative_search = _FakeNormativeSearch(
        general=general,
        targeted=targeted,
    )

    use_case = SearchTechnicalAssignmentGuidedNormative(
        search_technical_assignment=(
            _FakeTechnicalSearch(
                technical_result,
            )
        ),
        search_normative=normative_search,
        normative_catalog_uow_factory=(
            lambda: _FakeNormativeUnitOfWork(
                (
                    referenced_document,
                    general_document,
                )
            )
        ),
        combined_max_sources=12,
    )

    result = await use_case.execute(
        [
            "Проверить шкаф IP54.",
        ],
        technical_assignment_id=(technical_assignment_id),
        section_id=section_id,
        normative_document_ids=[
            referenced_document_id,
            general_document_id,
        ],
    )

    assert result.reference_resolutions[0].status is (
        NormativeReferenceResolutionStatus.RESOLVED
    )

    assert (
        len(
            normative_search.calls,
        )
        == 2
    )

    assert normative_search.calls[0]["source_prefix"] == "GN"

    assert normative_search.calls[1]["source_prefix"] == "TN"

    assert normative_search.calls[1]["document_ids"] == [
        referenced_document_id,
    ]

    assert [source.point_id for source in result.normative_sources] == [
        "shared-point",
        "target-only",
        "general-only",
    ]

    assert [source.source_id for source in result.normative_sources] == [
        "N1",
        "N2",
        "N3",
    ]

    assert result.normative_sources[0].score == 0.61

    assert (
        len(
            result.conflict_candidates,
        )
        == 1
    )

    assert result.conflict_candidates[0].technical_assignment_source_id == "T1"

    assert result.conflict_candidates[0].normative_source_ids == (
        "N1",
        "N2",
    )


@pytest.mark.asyncio
async def test_missing_t_reference_is_diagnostic_not_hallucinated_target() -> None:
    """Missing N reference не превращается в выдуманный targeted N."""
    section_id = uuid4()

    technical_assignment_id = uuid4()

    general_document_id = uuid4()

    document = _normative_document(
        document_id=general_document_id,
        section_id=section_id,
        original_name=("ГОСТ Р 21.101-2020.pdf"),
    )

    technical_result = TechnicalAssignmentSearchResult(
        queries=("Проверить проект.",),
        sources=(
            TechnicalAssignmentSource(
                source_id="T1",
                point_id="t-1",
                score=0.9,
                technical_assignment_id=str(
                    technical_assignment_id,
                ),
                analysis_document_id=str(
                    uuid4(),
                ),
                section_id=str(
                    section_id,
                ),
                source_sha256="b" * 64,
                source_file="tz.pdf",
                page=1,
                text=("Выполнить по СП 999.99999.9999."),
                normative_refs=("СП 999.99999.9999",),
            ),
        ),
        embedding_model=("Qwen/Qwen3-VL-Embedding-2B"),
    )

    general = NormativeSearchResult(
        queries=("general",),
        sources=(
            _normative_source(
                source_id="GN1",
                point_id="general-1",
                document_id=(general_document_id),
                section_id=section_id,
                score=0.8,
            ),
        ),
        embedding_model=("qwen3-embedding:4b"),
    )

    normative_search = _FakeNormativeSearch(
        general=general,
        targeted=NormativeSearchResult(
            queries=(),
            sources=(),
            embedding_model=("qwen3-embedding:4b"),
        ),
    )

    use_case = SearchTechnicalAssignmentGuidedNormative(
        search_technical_assignment=(
            _FakeTechnicalSearch(
                technical_result,
            )
        ),
        search_normative=normative_search,
        normative_catalog_uow_factory=(lambda: _FakeNormativeUnitOfWork((document,))),
        combined_max_sources=12,
    )

    result = await use_case.execute(
        [
            "Проверить проект.",
        ],
        technical_assignment_id=(technical_assignment_id),
        section_id=section_id,
        normative_document_ids=[
            general_document_id,
        ],
    )

    assert (
        len(
            normative_search.calls,
        )
        == 1
    )

    assert result.reference_resolutions[0].status is (
        NormativeReferenceResolutionStatus.MISSING
    )

    assert result.targeted_normative_sources == ()

    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        "missing_referenced_normative",
    ]

    assert [source.point_id for source in result.normative_sources] == [
        "general-1",
    ]


@pytest.mark.asyncio
async def test_t_only_requirement_is_valid_without_normative_reference() -> None:
    """Project-specific T requirement не требует N evidence."""
    section_id = uuid4()

    technical_assignment_id = uuid4()

    technical_result = TechnicalAssignmentSearchResult(
        queries=("Проверить маркировку.",),
        sources=(
            TechnicalAssignmentSource(
                source_id="T1",
                point_id="t-only",
                score=0.88,
                technical_assignment_id=str(
                    technical_assignment_id,
                ),
                analysis_document_id=str(
                    uuid4(),
                ),
                section_id=str(
                    section_id,
                ),
                source_sha256="b" * 64,
                source_file="tz.pdf",
                page=7,
                text=("Заказчик требует маркировку кабелей формата PROJECT-X."),
                normative_refs=(),
            ),
        ),
        embedding_model=("Qwen/Qwen3-VL-Embedding-2B"),
    )

    empty_normative = NormativeSearchResult(
        queries=(),
        sources=(),
        embedding_model=("qwen3-embedding:4b"),
    )

    normative_search = _FakeNormativeSearch(
        general=empty_normative,
        targeted=empty_normative,
    )

    use_case = SearchTechnicalAssignmentGuidedNormative(
        search_technical_assignment=(
            _FakeTechnicalSearch(
                technical_result,
            )
        ),
        search_normative=normative_search,
        normative_catalog_uow_factory=(lambda: _FakeNormativeUnitOfWork(())),
        combined_max_sources=12,
    )

    result = await use_case.execute(
        [
            "Проверить маркировку.",
        ],
        technical_assignment_id=(technical_assignment_id),
        section_id=section_id,
        normative_document_ids=[],
    )

    assert (
        len(
            result.technical_assignment_sources,
        )
        == 1
    )

    assert result.technical_assignment_sources[0].source_id == "T1"

    assert result.reference_resolutions == ()

    assert result.targeted_normative_sources == ()

    assert result.normative_sources == ()

    assert result.conflict_candidates == ()
