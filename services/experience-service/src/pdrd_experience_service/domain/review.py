# services/experience-service/src/pdrd_experience_service/domain/review.py

"""Неизменяемый Human Review: решения, исправления, геометрия и аудит.

Отображаемые области сохраняются отдельно от подтверждений для Experience.
Модель не зависит от HTTP, SQLAlchemy, VLM и генерации PDF.
"""

import math
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pdrd_experience_service.domain.rejection_feedback import validate_feedback


class ReviewError(ValueError):
    """Ошибка команды ревью, идентичности или геометрии."""


class ReviewConflictError(ReviewError):
    """Редакция ревью отличается от ожидаемой клиентом."""


class ReviewNotReadyError(ReviewError):
    """Ревью не завершено или ещё не утверждено."""


class Origin(StrEnum):
    """Источник первоначального замечания."""

    VLM = "vlm"
    MANUAL = "manual"


class Decision(StrEnum):
    """Явное решение проверяющего, независимое от происхождения замечания."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Action(StrEnum):
    """Операция ревью, сохраняемая в аудите."""

    OPENED = "opened"
    ADDED = "added"
    EDITED = "edited"
    DECIDED = "decided"
    APPROVED = "approved"
    GEOMETRY = "geometry"
    RESET = "reset"


def _text(value: str, *, limit: int = 10000, required: bool = True) -> str:
    """Проверяет пользовательский текст без скрытого усечения."""
    result = value.strip() if isinstance(value, str) else ""
    if (required and not result) or len(result) > limit:
        raise ReviewError(
            f"Expected text length 1..{limit}."
            if required
            else f"Text exceeds {limit}."
        )
    return result


def _actor(value: str) -> str:
    """Требует доверенного актёра из серверного адаптера идентичности."""
    return _text(value, limit=128)


def _time(value: datetime) -> datetime:
    """Сохраняет только время с часовым поясом в UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReviewError("Audit time must be timezone-aware.")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Rectangle:
    """Прямоугольник одной страницы PDF в координатах 0..1000 по обеим осям."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float

    def __post_init__(self) -> None:
        """Отсекает неверные и вырожденные пользовательские области."""
        values = (self.x_min, self.y_min, self.x_max, self.y_max)
        if (
            not all(math.isfinite(value) for value in values)
            or not (0 <= self.x_min < self.x_max <= 1000)
            or not (0 <= self.y_min < self.y_max <= 1000)
        ):
            raise ReviewError("Rectangle must lie inside one page (0..1000).")


@dataclass(frozen=True, slots=True)
class ProposedRegion:
    """Автоматическая область визуализации, ожидающая проверки инженером."""

    bbox: Rectangle
    source: str
    confidence: float
    method: str

    def __post_init__(self) -> None:
        """Не допускает пустую provenance или некорректную уверенность."""
        if (
            not isinstance(self.bbox, Rectangle)
            or not isinstance(self.source, str)
            or not self.source.strip()
            or not isinstance(self.method, str)
            or not self.method.strip()
            or not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
            or not math.isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise ReviewError("Некорректная предложенная область визуализации.")


@dataclass(frozen=True, slots=True)
class ReviewEvidenceLocation:
    """Страница доказательства одного логического замечания."""

    page: int
    source_id: str = ""
    text: str = ""
    proposed_regions: tuple[ProposedRegion, ...] = ()

    def __post_init__(self) -> None:
        """Проверяет страницу и типы областей до сохранения Review."""
        if self.page < 1 or any(
            not isinstance(item, ProposedRegion) for item in self.proposed_regions
        ):
            raise ReviewError("Неверная страница доказательства Review.")


@dataclass(frozen=True, slots=True)
class ReviewDocumentSource:
    """Снимок D-источника, доступный после удаления временного индекса."""

    source_id: str
    page: int
    evidence_text: str = ""
    text: str = ""
    fact_id: str | None = None
    chunk_index: int | None = None
    score: float = 0.0
    match_type: str = ""
    subject: str = ""
    property: str = ""
    scope: str = ""
    visual_regions: tuple[ProposedRegion, ...] = ()

    def __post_init__(self) -> None:
        """Не позволяет сохранить источник без стабильной физической идентичности."""
        if self.page < 1 or not self.source_id.startswith(f"D-p{self.page:04d}-"):
            raise ReviewError("Неверная идентичность источника D.")


@dataclass(frozen=True, slots=True)
class OriginalFinding:
    """Доверенное замечание завершённого анализа, исключающее гипотезы."""

    finding_id: str
    page_number: int
    text: str
    normative_basis: str = ""
    proposed_regions: tuple[ProposedRegion, ...] = ()
    evidence_locations: tuple[ReviewEvidenceLocation, ...] = ()
    document_context_basis_sources: tuple[ReviewDocumentSource, ...] = ()
    source_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Проверяет первоначальные данные перед открытием ревью."""
        _text(self.finding_id, limit=256)

        if self.finding_id != self.finding_id.strip():
            raise ReviewError("VLM finding IDs must be canonical.")

        if self.finding_id.startswith("manual:") or self.page_number < 1:
            raise ReviewError("Invalid VLM finding identity or physical page.")

        _text(self.text)
        _text(self.normative_basis, limit=2000, required=False)

        if not isinstance(self.proposed_regions, tuple) or any(
            not isinstance(region, ProposedRegion) for region in self.proposed_regions
        ):
            raise ReviewError("Области VLM должны быть валидными объектами.")


@dataclass(frozen=True, slots=True)
class ReviewedFinding:
    """Текущая редакция с неизменным оригиналом для обучения."""

    finding_id: str
    origin: Origin
    page_number: int
    original_text: str
    text: str
    original_basis: str
    normative_basis: str
    decision: Decision
    issue_box: Rectangle | None
    callout_box: Rectangle | None
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
    revision: int = 0
    proposed_regions: tuple[ProposedRegion, ...] = ()

    # Правки отображения инженером не повышают координаты до подтверждённых.
    display_regions: tuple[Rectangle, ...] | None = None
    reason_category: str | None = None
    comment: str = ""
    evidence_locations: tuple[ReviewEvidenceLocation, ...] = ()
    document_context_basis_sources: tuple[ReviewDocumentSource, ...] = ()
    source_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Проверяет разметку отклонения, допускает отсутствие причины у старых записей."""
        validate_feedback(
            rejected=self.decision is Decision.REJECTED,
            reason_category=self.reason_category,
            comment=self.comment,
        )

    @property
    def experience_tag(self) -> str | None:
        """Отражает источник и правку замечания независимо от решения."""
        if self.origin is Origin.MANUAL:
            return "gold"

        if (
            self.text != self.original_text
            or self.normative_basis != self.original_basis
        ):
            return "edited"

        if self.decision is Decision.ACCEPTED:
            return "wise"

        if self.decision is Decision.REJECTED:
            return "bad"

        return None


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    """Неизменная история с актёром и содержимым до и после действия."""

    action: Action
    actor: str
    occurred_at: datetime
    session_revision: int
    before: ReviewedFinding | None = None
    after: ReviewedFinding | None = None


@dataclass(frozen=True, slots=True)
class ApprovedReview:
    """Точная принятая редакция для адаптера формирования PDF."""

    job_id: UUID
    revision: int
    findings: tuple[ReviewedFinding, ...]


@dataclass(frozen=True, slots=True)
class ReviewSession:
    """Одно ревью завершённого задания с неизменяемыми состояниями для CAS-записи."""

    job_id: UUID
    document_id: UUID
    source_filename: str
    source_sha256: str
    opened_by: str
    opened_at: datetime
    allowed_pages: tuple[int, ...]
    findings: tuple[ReviewedFinding, ...]
    history: tuple[ReviewEvent, ...]
    revision: int = 0
    approved_revision: int | None = None

    @classmethod
    def open(
        cls,
        *,
        job_id: UUID,
        document_id: UUID,
        source_filename: str,
        source_sha256: str,
        originals: tuple[OriginalFinding, ...],
        rendered_pages: tuple[int, ...],
        actor: str,
        at: datetime,
    ) -> "ReviewSession":
        """Открывает каждое доверенное замечание VLM без решения, включая нелокализованные."""
        actor = _actor(actor)
        at = _time(at)
        source_filename = _text(source_filename, limit=512)

        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            raise ReviewError(
                "Source content SHA256 must be authoritative hexadecimal."
            )

        if len(set(rendered_pages)) != len(rendered_pages) or any(
            not isinstance(page, int) or isinstance(page, bool) or page < 1
            for page in rendered_pages
        ):
            raise ReviewError(
                "Rendered PDF page numbers must be distinct positive integers."
            )

        ids = [item.finding_id for item in originals]

        if len(ids) != len(set(ids)):
            raise ReviewError("Duplicate authoritative VLM finding IDs.")

        if any(
            item.proposed_regions and item.page_number not in rendered_pages
            for item in originals
        ):
            raise ReviewError("Область VLM привязана к неотрендеренному листу.")
        if any(
            location.proposed_regions and location.page not in rendered_pages
            for item in originals
            for location in item.evidence_locations
        ):
            raise ReviewError("Доказательство привязано к неотрендеренному листу.")

        findings = tuple(
            ReviewedFinding(
                finding_id=item.finding_id,
                origin=Origin.VLM,
                page_number=item.page_number,
                original_text=item.text,
                text=item.text,
                original_basis=item.normative_basis,
                normative_basis=item.normative_basis,
                decision=Decision.PENDING,
                issue_box=None,
                callout_box=None,
                created_by=actor,
                created_at=at,
                updated_by=actor,
                updated_at=at,
                proposed_regions=item.proposed_regions,
                evidence_locations=item.evidence_locations,
                document_context_basis_sources=item.document_context_basis_sources,
                source_kinds=item.source_kinds,
            )
            for item in originals
        )

        return cls(
            job_id=job_id,
            document_id=document_id,
            source_filename=source_filename,
            source_sha256=source_sha256,
            opened_by=actor,
            opened_at=at,
            allowed_pages=tuple(rendered_pages),
            findings=findings,
            history=(ReviewEvent(Action.OPENED, actor, at, 0),),
        )

    @property
    def pending_count(self) -> int:
        """Учитывает все доверенные замечания VLM и пользовательские Gold."""
        return sum(item.decision is Decision.PENDING for item in self.findings)

    def _expect(self, revision: int) -> None:
        """Отсекает устаревшую запись до изменения состояния."""
        if revision != self.revision:
            raise ReviewConflictError(
                f"Stale review revision {revision}; current revision {self.revision}."
            )

    def _item(self, finding_id: str) -> ReviewedFinding:
        """Находит запись ревью, не принимая придуманные клиентом идентификаторы VLM."""
        item = next(
            (item for item in self.findings if item.finding_id == finding_id),
            None,
        )

        if item is None:
            raise ReviewError(f"Unknown review finding: {finding_id}.")

        return item

    def _update(
        self,
        *,
        before: ReviewedFinding,
        after: ReviewedFinding,
        action: Action,
        actor: str,
        at: datetime,
    ) -> "ReviewSession":
        """Заменяет одну запись и добавляет неизменное событие аудита."""
        next_revision = self.revision + 1

        return replace(
            self,
            findings=tuple(
                after if item.finding_id == before.finding_id else item
                for item in self.findings
            ),
            history=(
                *self.history,
                ReviewEvent(
                    action,
                    actor,
                    at,
                    next_revision,
                    before,
                    after,
                ),
            ),
            revision=next_revision,
            approved_revision=None,
        )

    def add_manual(
        self,
        *,
        finding_id: str,
        page_number: int,
        text: str,
        normative_basis: str,
        issue_box: Rectangle,
        callout_box: Rectangle,
        actor: str,
        at: datetime,
        expected_revision: int,
    ) -> "ReviewSession":
        """Добавляет Gold только на проверенную отрисованную страницу PDF."""
        self._expect(expected_revision)
        actor = _actor(actor)
        at = _time(at)

        if not finding_id.startswith("manual:"):
            raise ReviewError("Manual ID requires the manual: prefix.")

        try:
            UUID(finding_id.removeprefix("manual:"))
        except ValueError as error:
            raise ReviewError("Manual finding ID must contain a UUID.") from error

        if any(item.finding_id == finding_id for item in self.findings):
            raise ReviewError("Duplicate review finding ID.")

        if (
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or page_number not in self.allowed_pages
        ):
            raise ReviewError("Manual finding must belong to a rendered source page.")

        if not isinstance(issue_box, Rectangle) or not isinstance(
            callout_box, Rectangle
        ):
            raise ReviewError("Manual annotation requires two validated rectangles.")

        text = _text(text)
        normative_basis = _text(
            normative_basis,
            limit=2000,
            required=False,
        )

        record = ReviewedFinding(
            finding_id=finding_id,
            origin=Origin.MANUAL,
            page_number=page_number,
            original_text=text,
            text=text,
            original_basis=normative_basis,
            normative_basis=normative_basis,
            decision=Decision.PENDING,
            issue_box=issue_box,
            callout_box=callout_box,
            created_by=actor,
            created_at=at,
            updated_by=actor,
            updated_at=at,
        )

        new_revision = self.revision + 1

        return replace(
            self,
            findings=(*self.findings, record),
            history=(
                *self.history,
                ReviewEvent(
                    Action.ADDED,
                    actor,
                    at,
                    new_revision,
                    after=record,
                ),
            ),
            revision=new_revision,
            approved_revision=None,
        )

    def edit(
        self,
        *,
        finding_id: str,
        text: str,
        normative_basis: str,
        actor: str,
        at: datetime,
        expected_revision: int,
    ) -> "ReviewSession":
        """Меняет текст или нормативное основание и отменяет прежнее утверждение."""
        self._expect(expected_revision)
        actor = _actor(actor)
        at = _time(at)
        record = self._item(finding_id)

        text = _text(text)
        normative_basis = _text(
            normative_basis,
            limit=2000,
            required=False,
        )

        if record.text == text and record.normative_basis == normative_basis:
            return self

        updated = replace(
            record,
            text=text,
            normative_basis=normative_basis,
            decision=Decision.PENDING,
            reason_category=None,
            comment="",
            updated_by=actor,
            updated_at=at,
            revision=record.revision + 1,
        )

        return self._update(
            before=record,
            after=updated,
            action=Action.EDITED,
            actor=actor,
            at=at,
        )

    def decide(
        self,
        *,
        finding_id: str,
        decision: Decision,
        actor: str,
        at: datetime,
        expected_revision: int,
        reason_category: str | None = None,
        comment: str = "",
    ) -> "ReviewSession":
        """Сохраняет решение и объяснение отказа, оставляя прежнюю версию в аудите."""
        self._expect(expected_revision)
        actor = _actor(actor)
        at = _time(at)

        if decision not in (
            Decision.ACCEPTED,
            Decision.REJECTED,
        ):
            raise ReviewError("A human decision must be accepted or rejected.")

        try:
            reason_category, comment = validate_feedback(
                rejected=decision is Decision.REJECTED,
                reason_category=reason_category,
                comment=comment,
                require_reason=True,
            )
        except ValueError as error:
            raise ReviewError(str(error)) from error
        record = self._item(finding_id)

        if (record.decision, record.reason_category, record.comment) == (
            decision,
            reason_category,
            comment,
        ):
            return self

        updated = replace(
            record,
            decision=decision,
            reason_category=reason_category,
            comment=comment,
            updated_by=actor,
            updated_at=at,
            revision=record.revision + 1,
        )

        return self._update(
            before=record,
            after=updated,
            action=Action.DECIDED,
            actor=actor,
            at=at,
        )

    def reset_decision(
        self,
        *,
        finding_id: str,
        actor: str,
        at: datetime,
        expected_revision: int,
    ) -> "ReviewSession":
        """Возвращает замечание в pending как отдельное аудируемое действие Undo."""
        self._expect(expected_revision)
        actor, at = _actor(actor), _time(at)
        record = self._item(finding_id)
        if record.decision is Decision.PENDING:
            return self
        updated = replace(
            record,
            decision=Decision.PENDING,
            reason_category=None,
            comment="",
            updated_by=actor,
            updated_at=at,
            revision=record.revision + 1,
        )
        return self._update(
            before=record,
            after=updated,
            action=Action.RESET,
            actor=actor,
            at=at,
        )

    def change_geometry(
        self,
        *,
        finding_id: str,
        regions: tuple[Rectangle, ...],
        callout_box: Rectangle | None,
        actor: str,
        at: datetime,
        expected_revision: int,
    ) -> "ReviewSession":
        """Сохраняет отображение одного листа, сбрасывая решение и утверждение.

        VLM сохраняет исходные proposed_regions. display_regions не являются
        подтверждением для Experience и не используются для обучения.
        Для Gold требуется область ошибки и область текста.
        """
        self._expect(expected_revision)
        actor, at = _actor(actor), _time(at)
        record = self._item(finding_id)
        if record.page_number not in self.allowed_pages:
            raise ReviewError("Геометрия допустима только на серверном листе.")
        if not isinstance(regions, tuple) or any(
            not isinstance(box, Rectangle) for box in regions
        ):
            raise ReviewError("Области должны быть проверенными прямоугольниками.")
        if callout_box is not None and not isinstance(callout_box, Rectangle):
            raise ReviewError("Некорректная область текста.")
        if record.origin is Origin.MANUAL:
            if len(regions) != 1 or callout_box is None:
                raise ReviewError("Gold требует две области на одном листе.")
            unchanged = (
                record.issue_box == regions[0] and record.callout_box == callout_box
            )
        else:
            if len(regions) != len(record.proposed_regions):
                raise ReviewError(
                    "Нельзя создавать новые области VLM через правку отображения."
                )
            existing = record.display_regions
            if existing is None:
                existing = tuple(item.bbox for item in record.proposed_regions)
            unchanged = existing == regions and record.callout_box == callout_box
        if unchanged:
            return self
        updated = replace(
            record,
            issue_box=regions[0]
            if record.origin is Origin.MANUAL
            else record.issue_box,
            display_regions=regions if record.origin is Origin.VLM else None,
            callout_box=callout_box,
            decision=Decision.PENDING,
            reason_category=None,
            comment="",
            updated_by=actor,
            updated_at=at,
            revision=record.revision + 1,
        )
        return self._update(
            before=record,
            after=updated,
            action=Action.GEOMETRY,
            actor=actor,
            at=at,
        )

    def approve(
        self,
        *,
        actor: str,
        at: datetime,
        expected_revision: int,
    ) -> "ReviewSession":
        """Утверждает редакцию после решения по каждому замечанию."""
        self._expect(expected_revision)
        actor = _actor(actor)
        at = _time(at)

        if self.pending_count:
            raise ReviewNotReadyError(
                f"{self.pending_count} findings still require review."
            )

        if self.approved_revision == self.revision:
            return self

        next_revision = self.revision + 1

        return replace(
            self,
            revision=next_revision,
            approved_revision=next_revision,
            history=(
                *self.history,
                ReviewEvent(
                    Action.APPROVED,
                    actor,
                    at,
                    next_revision,
                ),
            ),
        )

    def accepted_for_pdf(self) -> ApprovedReview:
        """Возвращает принятые записи; отклонённые остаются в аудите и Experience."""
        if self.approved_revision != self.revision or self.pending_count:
            raise ReviewNotReadyError(
                "Current review revision must be fully approved before PDF export."
            )

        return ApprovedReview(
            job_id=self.job_id,
            revision=self.revision,
            findings=tuple(
                item for item in self.findings if item.decision is Decision.ACCEPTED
            ),
        )
