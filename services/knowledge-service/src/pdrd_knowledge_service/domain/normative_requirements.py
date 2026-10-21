# services/knowledge-service/src/pdrd_knowledge_service/domain/normative_requirements.py

"""Атомарные требования существующего нормативного каталога и доказательства их статуса."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from urllib.parse import urlsplit
from uuid import UUID, uuid5

EXTRACTOR_VERSION = "normative-native-v2"
_NAMESPACE = UUID("668605a7-4a65-5b7f-8892-74ce88884b56")


def stable_requirement_id(*parts: object) -> str:
    """Включает документ, содержимое, версию извлечения и точный предмет атомизации."""
    return str(uuid5(_NAMESPACE, json.dumps(parts, ensure_ascii=False, sort_keys=True)))


def content_digest(value: object) -> str:
    """Даёт воспроизводимый отпечаток метаданных без зависимости от порядка ключей."""
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class NormativeSourceFragment:
    """Буквальный фрагмент страницы; координаты не выдумываются при наличии только текста."""

    page: int
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class NormativeRequirement:
    """Атомарный предмет с полным исходным пунктом, условиями и ограничениями извлечения."""

    requirement_id: str
    point_id: str
    document_id: str
    source_sha256: str
    source_file: str
    document_code: str
    edition: str
    clause: str
    subclause: str
    page: int
    source_text: str
    text: str
    object_type: str
    applicability_conditions: tuple[str, ...]
    exceptions: tuple[str, ...]
    required_parameters: tuple[str, ...]
    verification_type: str
    normative_dependencies: tuple[str, ...]
    extraction_status: str
    modality: str
    source_fragments: tuple[NormativeSourceFragment, ...]
    context: tuple[str, ...]
    limitations: tuple[str, ...]

    def as_dict(self) -> dict:
        """Сохраняет исходные названия полей N/T DTO: page, source_text и text."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class NormativeStatusEvidence:
    """Проверяемая запись каталога; готовность индекса не входит в доказательство статуса.

    Дата действия редакции и обязательность пунктов хранятся раздельно. Запись
    подтверждает только явно перечисленные пункты и заданный период.
    """

    evidence_id: str
    document_id: str
    source_sha256: str
    document_code: str
    edition: str
    edition_status: str
    requirement_binding: str
    clause_scope: tuple[str, ...]
    applicability_scope: str
    source_url: str
    source_quote: str
    confirmed_on: str
    valid_from: str
    valid_until: str
    verified_by: str
    recorded_at: str

    def __post_init__(self) -> None:
        """Запрещает непроверяемое подтверждение без редакции, источника, даты и автора."""
        object.__setattr__(self, "clause_scope", tuple(self.clause_scope))
        UUID(self.document_id)
        UUID(self.verified_by)
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise ValueError("Неверный SHA-256 нормативного документа.")
        if self.edition_status not in {"current", "withdrawn", "unknown", "disputed"}:
            raise ValueError("Неизвестный статус редакции.")
        if self.requirement_binding not in {
            "mandatory",
            "voluntary",
            "unknown",
            "disputed",
        }:
            raise ValueError("Неизвестная обязательность требования.")
        if not all(
            str(value).strip()
            for value in (
                self.document_code,
                self.edition,
                self.applicability_scope,
                self.source_quote,
                self.recorded_at,
            )
        ):
            raise ValueError(
                "Доказательство требует документа, редакции, области и цитаты."
            )
        parsed = urlsplit(self.source_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError(
                "Нужна HTTP(S)-ссылка на подтверждающий источник без учётных данных."
            )
        if not self.clause_scope or any(
            value != "*"
            and not re.fullmatch(r"(?:\d+|[А-ЯA-Z])(?:\.\d+)*(?:\.[а-яa-z])?", value)
            for value in self.clause_scope
        ):
            raise ValueError("Необходимо явно указать область пунктов доказательства.")
        start, end, confirmed = (
            date.fromisoformat(value)
            for value in (self.valid_from, self.valid_until, self.confirmed_on)
        )
        if start > end or confirmed > date.fromisoformat(self.recorded_at[:10]):
            raise ValueError("Недопустимый период или будущая дата подтверждения.")

    def as_dict(self) -> dict:
        """Возвращает неизменяемый журнал подтверждения для snapshot поколения."""
        return asdict(self)


def requirement_status(
    requirement: NormativeRequirement,
    evidence: tuple[NormativeStatusEvidence, ...],
    on: str,
) -> dict:
    """Не выводит правовой статус из имени, года, READY или рекомендательной модальности."""
    assessed = date.fromisoformat(on)
    target = requirement.clause + (
        "." + requirement.subclause if requirement.subclause else ""
    )
    applicable = tuple(
        item
        for item in evidence
        if item.document_id == requirement.document_id
        and item.source_sha256 == requirement.source_sha256
        and item.document_code == requirement.document_code
        and item.edition == requirement.edition
        and date.fromisoformat(item.valid_from)
        <= assessed
        <= date.fromisoformat(item.valid_until)
        and any(
            scope == "*"
            or target.casefold() == scope.casefold()
            or target.casefold().startswith(scope.casefold() + ".")
            for scope in item.clause_scope
        )
    )

    def consensus(field: str) -> str:
        """Противоречащие свидетельства не разрешаются выбором последней записи."""
        values = {getattr(item, field) for item in applicable} - {"unknown"}
        return (
            next(iter(values))
            if len(values) == 1
            else "disputed"
            if values
            else "unknown"
        )

    binding = consensus("requirement_binding")
    if requirement.modality != "mandatory" and binding == "mandatory":
        binding = "unknown"
    return {
        "assessed_on": on,
        "edition_status": consensus("edition_status"),
        "requirement_binding": binding,
        "evidence_ids": [item.evidence_id for item in applicable],
        "applicability_scopes": [item.applicability_scope for item in applicable],
        "project_applicability": "not_assessed",
    }
