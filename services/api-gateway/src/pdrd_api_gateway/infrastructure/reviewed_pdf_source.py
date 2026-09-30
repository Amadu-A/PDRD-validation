# services/api-gateway/src/pdrd_api_gateway/infrastructure/reviewed_pdf_source.py

"""Строгая проверка межсервисной проекции Experience без импорта чужого домена."""

import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
)
from pdrd_api_gateway.application.ports.review import (
    ReviewContext,
    ReviewRequestError,
    ReviewService,
)
from pdrd_api_gateway.application.ports.reviewed_pdf import (
    ReviewedPdfFinding,
    ReviewedPdfManifest,
)


class _Contract(BaseModel):
    """Отсекает неизвестные поля и неявные преобразования типов."""

    model_config = ConfigDict(extra="forbid", strict=True)


class _Box(_Contract):
    """Проверяет конечные координаты и положительную площадь."""

    x_min: float = Field(ge=0, le=1000, allow_inf_nan=False)
    y_min: float = Field(ge=0, le=1000, allow_inf_nan=False)
    x_max: float = Field(ge=0, le=1000, allow_inf_nan=False)
    y_max: float = Field(ge=0, le=1000, allow_inf_nan=False)

    @model_validator(mode="after")
    def positive_area(self):
        """Пустые рамки не проходят внутренний контракт."""
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError("Пустая область.")
        return self

    def to_port(self):
        """Сохраняет дробную точность координат пользователя."""
        return AnalysisBoundingBox(**self.model_dump())


class _Finding(_Contract):
    """Контракт принятого замечания; решение браузер сюда не передаёт."""

    number: int = Field(ge=1)
    finding_id: str = Field(min_length=1, max_length=256)
    page_number: int = Field(ge=1)
    origin: Literal["vlm", "manual"]
    experience_tag: Literal["wise", "edited", "gold"]
    text: str = Field(min_length=1, max_length=10000)
    normative_basis: str = Field(max_length=2000)
    regions: list[_Box] = Field(max_length=4)
    callout_box: _Box | None

    @model_validator(mode="after")
    def check_origin(self):
        """Gold обязан иметь обе области; VLM никогда не получает тег Gold."""
        if self.origin == "manual":
            if (
                self.experience_tag != "gold"
                or len(self.regions) != 1
                or self.callout_box is None
            ):
                raise ValueError("Неполное ручное замечание.")
        elif self.experience_tag == "gold":
            raise ValueError("Неверный источник Gold.")
        return self


class _Manifest(_Contract):
    """Идентичность документа, утверждённая ревизия и последовательная нумерация."""

    job_id: UUID
    document_id: UUID
    source_filename: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(ge=0)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    findings: list[_Finding]

    @model_validator(mode="after")
    def check_findings(self):
        """Не допускает дублирования находок и рассинхронизации номеров."""
        if [row.number for row in self.findings] != list(
            range(1, len(self.findings) + 1)
        ):
            raise ValueError("Неверная нумерация.")
        if len({row.finding_id for row in self.findings}) != len(self.findings):
            raise ValueError("Повторяющееся замечание.")
        return self


@dataclass(frozen=True, slots=True)
class HttpReviewedPdfSource:
    """Использует существующий защищённый HTTP-клиент Review."""

    service: ReviewService

    async def load(self, *, context: ReviewContext) -> ReviewedPdfManifest:
        """Преобразует внешний JSON в типизированный application port."""
        payload = await self.service.execute(context=context)
        try:
            data = _Manifest.model_validate_json(json.dumps(payload))
            if data.job_id != context.job_id:
                raise ValueError("Другое задание.")
        except (ValidationError, ValueError, TypeError) as error:
            raise ReviewRequestError(
                503, "Некорректная проекция утверждённого Review."
            ) from error
        return ReviewedPdfManifest(
            job_id=data.job_id,
            document_id=data.document_id,
            source_filename=data.source_filename,
            source_sha256=data.source_sha256,
            revision=data.revision,
            digest=data.digest,
            findings=tuple(
                ReviewedPdfFinding(
                    number=row.number,
                    finding_id=row.finding_id,
                    page_number=row.page_number,
                    origin=row.origin,
                    experience_tag=row.experience_tag,
                    text=row.text,
                    normative_basis=row.normative_basis,
                    regions=tuple(box.to_port() for box in row.regions),
                    callout_box=row.callout_box.to_port() if row.callout_box else None,
                )
                for row in data.findings
            ),
        )
