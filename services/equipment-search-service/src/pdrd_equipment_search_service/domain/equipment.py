# services/equipment-search-service/src/pdrd_equipment_search_service/domain/equipment.py

"""Идентичность оборудования, доверие к источнику и доказательство документа."""

import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit


class SourceStatus(StrEnum):
    """Состояние доверия к точному hostname производителя."""

    TRUSTED = "trusted"
    PENDING = "pending"
    BLOCKED = "blocked"


def normalized_token(value: str) -> str:
    """Нормализует ключ без предположений о модели или исполнении."""
    return re.sub(r"\s+", " ", value.strip()).casefold()


def safe_search_term(value: str) -> str:
    """Удаляет поисковые операторы из распознанной маркировки."""
    return re.sub(r"[^\w\-/+. ]+", " ", value, flags=re.UNICODE).strip()[:100]


def equipment_token(value: str) -> str:
    """Учитывает пробелы, типографские дефисы и обозначение исполнения."""
    value = value.casefold().translate(str.maketrans("–—−", "---"))
    value = re.sub(r"\bисп(?:олнение)?\.?\s*", "исп.", value)
    return re.sub(r"\s+", "", value)


@dataclass(frozen=True, slots=True)
class EquipmentIdentity:
    """Дословно установленное оборудование с физического листа."""

    manufacturer: str
    model: str
    variant: str = ""
    status: str = "resolved"
    confidence: float = 1.0
    properties: tuple[str, ...] = ()
    manufacturer_status: str = "explicit"
    model_status: str = "confirmed"
    variant_kind: str = "execution"
    article: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        """Возвращает ключ единственного поиска по модели."""
        return (
            normalized_token(self.manufacturer),
            equipment_token(self.model),
            equipment_token(self.variant)
            + ("/артикул" + equipment_token(self.article) if self.article else ""),
        )

    @property
    def searchable(self) -> bool:
        """Запрещает поиск по неполной или сомнительной идентификации."""
        return bool(
            self.key[1]
            and self.model_status == "confirmed"
            and (
                (self.status == "resolved" and self.key[0])
                or (
                    not self.key[0]
                    and self.manufacturer_status == "unknown"
                    and self.model_status == "confirmed"
                )
            )
            and self.confidence >= 0.75
        )


@dataclass(frozen=True, slots=True)
class SourceDomain:
    """Разрешение точного hostname для конкретного производителя."""

    manufacturer: str
    hostname: str
    status: SourceStatus
    enabled: bool = True
    allow_http: bool = False
    registry_official: bool = False

    def matches(self, manufacturer: str, hostname: str) -> bool:
        """Не распространяет доверие на произвольные поддомены."""
        return (
            self.enabled
            and normalized_token(self.manufacturer) == normalized_token(manufacturer)
            and self.hostname.casefold().rstrip(".") == hostname.casefold().rstrip(".")
        )


@dataclass(frozen=True, slots=True)
class SearchHit:
    """Поисковая ссылка без доказательной силы snippet."""

    url: str
    title: str = ""

    @property
    def hostname(self) -> str:
        """Возвращает hostname только HTTP(S)-ссылки без учётных данных."""
        try:
            parsed = urlsplit(self.url)
            hostname = parsed.hostname
            username = parsed.username
            password = parsed.password
        except ValueError:
            return ""
        if (
            parsed.scheme not in {"https", "http"}
            or not hostname
            or username
            or password
        ):
            return ""
        return hostname.casefold().rstrip(".")


@dataclass(frozen=True, slots=True)
class DownloadedDocument:
    """Ограниченный ответ безопасного загрузчика."""

    content: bytes
    final_url: str
    media_type: str

    @property
    def sha256(self) -> str:
        """Возвращает идентичность байтов snapshot."""
        return sha256(self.content).hexdigest()


@dataclass(frozen=True, slots=True)
class DocumentInspection:
    """Результат проверки применимости документа к модели."""

    applicable: bool
    revision: str = ""
    publication_date: str = ""
    reason: str = ""
    pages: tuple[tuple[int, str], ...] = ()
    requires_vision: bool = False
    vision_facts: tuple[dict[str, Any], ...] = ()
    reason_code: str = ""
    manufacturer: str = ""
    manufacturer_aliases: tuple[str, ...] = ()
    vision_pages: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentSnapshot:
    """Неизменяемая версия сохранённой документации производителя."""

    source_id: str
    manufacturer: str
    model: str
    variant: str
    source_url: str
    final_url: str
    sha256: str
    storage_reference: str
    revision: str
    trust_status: str
    revision_ambiguous: bool = False
    publication_date: str = ""
    fetched_at: str = ""
    article: str = ""


@dataclass(frozen=True, slots=True)
class ResolveResult:
    """Результат поиска одной уникальной модели оборудования."""

    identity: EquipmentIdentity
    status: str
    document: DocumentSnapshot | None = None
    warning: str = ""
    searched_urls: int = 0
    facts: tuple[dict[str, Any], ...] = ()
    facts_status: str = ""
    reason_code: str = ""
    attempts: tuple[dict[str, Any], ...] = ()
    resolved_manufacturer: str = ""
    tried_url_hashes: tuple[str, ...] = ()
    retryable: bool = False
