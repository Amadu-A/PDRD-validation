# services/analysis-service/src/pdrd_analysis_service/transport/http/schemas.py

"""HTTP schemas Analysis Service."""

from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from pdrd_analysis_service.domain.analysis import (
    ExperienceSource,
    FindingDraft,
    NormativeSource,
    PageFacts,
    UserPackageSource,
)


class PageFactsPayload(BaseModel):
    """HTTP representation PageFacts."""

    model_config = ConfigDict(
        extra="forbid",
    )

    discipline: str
    page_type: str
    summary: str

    objects: list[str]
    connections: list[str]
    labels: list[str]

    normative_queries: list[str]

    def to_domain(
        self,
    ) -> PageFacts:
        """Преобразует payload в Domain."""
        return PageFacts(
            discipline=self.discipline,
            page_type=self.page_type,
            summary=self.summary,
            objects=tuple(
                self.objects,
            ),
            connections=tuple(
                self.connections,
            ),
            labels=tuple(
                self.labels,
            ),
            normative_queries=tuple(
                self.normative_queries,
            ),
        )


class NormativeSourcePayload(BaseModel):
    """Нормативный source от Knowledge Service."""

    model_config = ConfigDict(
        extra="forbid",
    )

    source_id: str
    point_id: str = ""

    score: float

    document_id: str | None = None
    section_id: str | None = None
    category_id: str | None = None

    source_sha256: str | None = None

    source_file: str | None = None
    source_path: str | None = None

    page: int | str | None = None
    chunk_index: int | str | None = None

    text: str

    def to_domain(
        self,
    ) -> NormativeSource:
        """Преобразует source в Domain."""
        return NormativeSource(
            source_id=self.source_id,
            point_id=self.point_id,
            score=self.score,
            source_file=self.source_file,
            source_path=self.source_path,
            page=self.page,
            chunk_index=self.chunk_index,
            text=self.text,
            document_id=self.document_id,
            section_id=self.section_id,
            category_id=self.category_id,
            source_sha256=self.source_sha256,
        )


class UserPackageSourcePayload(BaseModel):
    """User-package source от Knowledge Service."""

    model_config = ConfigDict(
        extra="forbid",
    )

    source_id: str
    point_id: str = ""

    score: float

    document_id: str | None = None
    section_id: str | None = None
    category_id: str | None = None

    source_sha256: str | None = None

    source_file: str | None = None
    source_path: str | None = None

    page: int | str | None = None
    chunk_index: int | str | None = None

    text: str

    def to_domain(
        self,
    ) -> UserPackageSource:
        """Преобразует package source в Domain."""
        return UserPackageSource(
            source_id=self.source_id,
            point_id=self.point_id,
            score=self.score,
            source_file=self.source_file,
            source_path=self.source_path,
            page=self.page,
            chunk_index=self.chunk_index,
            text=self.text,
            document_id=self.document_id,
            section_id=self.section_id,
            category_id=self.category_id,
            source_sha256=self.source_sha256,
        )


class ExperienceSourcePayload(BaseModel):
    """Experience source от Knowledge Service."""

    model_config = ConfigDict(
        extra="forbid",
    )

    source_id: str
    point_id: str = ""
    score: float

    project_id: str | None = None
    issue_id: str | None = None
    issue_text: str | None = None

    status: str | None = None
    verified_fixed: bool = False

    before_page: int | str | None = None
    after_page: int | str | None = None

    before_context: str = ""
    after_context: str = ""

    def to_domain(
        self,
    ) -> ExperienceSource:
        """Преобразует source в Domain."""
        return ExperienceSource(
            source_id=self.source_id,
            point_id=self.point_id,
            score=self.score,
            project_id=self.project_id,
            issue_id=self.issue_id,
            issue_text=self.issue_text,
            status=self.status,
            verified_fixed=self.verified_fixed,
            before_page=self.before_page,
            after_page=self.after_page,
            before_context=self.before_context,
            after_context=self.after_context,
        )


class FindingDraftPayload(BaseModel):
    """Finding между requirement-check и experience stages."""

    model_config = ConfigDict(
        extra="forbid",
    )

    finding_id: str

    page: int
    page_type: str

    category: str
    severity: str
    status: str

    comment: str
    evidence: str
    recommendation_draft: str

    confidence: float

    normative_source_ids: list[str]

    basis: str

    basis_sources: list[NormativeSourcePayload]

    experience_query: str

    user_package_source_ids: list[str] = Field(
        default_factory=list,
    )

    user_package_basis_sources: list[UserPackageSourcePayload] = Field(
        default_factory=list,
    )

    def to_domain(
        self,
    ) -> FindingDraft:
        """Преобразует finding в Domain."""
        return FindingDraft(
            finding_id=self.finding_id,
            page=self.page,
            page_type=self.page_type,
            category=self.category,  # type: ignore[arg-type]
            severity=self.severity,  # type: ignore[arg-type]
            status=self.status,  # type: ignore[arg-type]
            comment=self.comment,
            evidence=self.evidence,
            recommendation_draft=self.recommendation_draft,
            confidence=self.confidence,
            normative_source_ids=tuple(
                self.normative_source_ids,
            ),
            basis=self.basis,
            basis_sources=tuple(source.to_domain() for source in self.basis_sources),
            experience_query=self.experience_query,
            user_package_source_ids=tuple(
                self.user_package_source_ids,
            ),
            user_package_basis_sources=tuple(
                source.to_domain() for source in self.user_package_basis_sources
            ),
        )


class UnderstandPageRequest(BaseModel):
    """Запрос понимания одного листа."""

    model_config = ConfigDict(
        extra="forbid",
    )

    page_number: int = Field(
        ge=1,
    )

    heuristic_page_type: str

    extracted_text: str

    image_base64: str = Field(
        min_length=1,
    )


class UnderstandPageResponse(BaseModel):
    """Ответ page understanding."""

    facts: PageFactsPayload

    metrics: dict[
        str,
        Any,
    ]


class NormativeQueriesRequest(BaseModel):
    """Запрос построения retrieval queries."""

    model_config = ConfigDict(
        extra="forbid",
    )

    page_facts: PageFactsPayload

    extracted_text: str

    project_context_texts: list[str] = Field(
        default_factory=list,
    )


class NormativeQueriesResponse(BaseModel):
    """Список normative/user retrieval queries."""

    queries: list[str]


class CheckNormsRequest(BaseModel):
    """Запрос проверки требований."""

    model_config = ConfigDict(
        extra="forbid",
    )

    page_number: int = Field(
        ge=1,
    )

    extracted_text: str

    page_facts: PageFactsPayload

    normative_sources: list[NormativeSourcePayload]

    user_package_sources: list[UserPackageSourcePayload] = Field(
        default_factory=list,
    )

    image_base64: str = Field(
        min_length=1,
    )

    normative_system_prompt: str | None = None


class CheckNormsResponse(BaseModel):
    """Ответ requirement check."""

    summary: str

    findings: list[FindingDraftPayload]

    metrics: dict[
        str,
        Any,
    ]


class FinalizeRequest(BaseModel):
    """Запрос финализации findings."""

    model_config = ConfigDict(
        extra="forbid",
    )

    findings: list[FindingDraftPayload]

    experience_by_finding: dict[
        str,
        list[ExperienceSourcePayload],
    ]


class FinalFindingPayload(BaseModel):
    """Итоговое замечание."""

    finding_id: str

    page: int
    page_type: str

    category: str
    severity: str
    status: str

    comment: str
    evidence: str
    recommendation: str

    confidence: float

    basis: str

    basis_sources: list[NormativeSourcePayload]

    experience_sources: list[ExperienceSourcePayload]

    user_package_basis_sources: list[UserPackageSourcePayload] = Field(
        default_factory=list,
    )


class FinalizeResponse(BaseModel):
    """Ответ финализации findings."""

    summary: str

    findings: list[FinalFindingPayload]

    metrics: dict[
        str,
        Any,
    ]


class LiveHealthResponse(BaseModel):
    """Liveness response."""

    status: str
    service: str
    version: str


class ReadyHealthResponse(BaseModel):
    """Readiness response."""

    status: str
    service: str
    version: str

    dependencies: dict[
        str,
        bool,
    ]
