# services/document-service/src/pdrd_document_service/application/ports/specification_extraction.py

"""Контракт получения и долговечного сохранения результата общего извлечения спецификаций."""

from dataclasses import dataclass
from typing import Protocol


class SpecificationExtractionError(RuntimeError):
    """Безопасный отказ внешней интеграции, не подменяющий неполный результат готовым."""


class ExtractionArtifactStorageError(RuntimeError):
    """Собственное хранилище недоступно: полный результат нельзя надёжно сохранить."""


class ExtractionStorageProbe(Protocol):
    """Проверяет локальную готовность хранилища без обращения к внешним API."""

    async def check_ready(self) -> None:
        """Проверяет фактическую запись от имени рабочего пользователя сервиса."""
        ...


@dataclass(frozen=True, slots=True)
class SpecificationExtraction:
    """Исходные строки и диагностика; геометрия устанавливается отдельно в PDRD."""

    job_id: str
    result: dict
    warnings: tuple[dict, ...]
    status: str
    artifact_key: str


class SpecificationExtractor(Protocol):
    """Извлекает только указанные физические страницы, не обращаясь к чужой БД."""

    async def extract(
        self,
        *,
        content: bytes,
        file_name: str,
        pages: tuple[int, ...],
        project_code: str,
    ) -> SpecificationExtraction:
        """Возвращает сохранённый локально результат, независимый от TTL внешнего задания."""
        ...


class ExtractionArtifactStore(Protocol):
    """Хранит собственные неизменяемые результаты интеграции в PDRD."""

    async def load(self, key: str) -> dict | None:
        """Читает проверенный результат либо возвращает отсутствие кэша."""
        ...

    async def save(self, key: str, value: dict) -> None:
        """Атомарно сохраняет полный JSON до возврата вызывающему сценарию."""
        ...
