# services/knowledge-service/src/pdrd_knowledge_service/application/ports/normative_requirements.py

"""Транзакционное хранилище поколений атомарного индекса в существующем каталоге."""

from typing import Protocol

from pdrd_knowledge_service.domain.normative_requirements import (
    NormativeRequirement,
    NormativeStatusEvidence,
)


class NormativeRequirementRepository(Protocol):
    """Методы выполняются в той же UOW, что блокировка нормативного документа."""

    async def get_generation(self, generation_id: str) -> dict | None:
        """Читает конкретное неизменяемое поколение и его lifecycle."""
        ...

    async def active_generation(self, document_id: str) -> dict | None:
        """Читает опубликованное поколение, не строя индекс при чтении."""
        ...

    async def save_generation(self, generation: dict) -> None:
        """Сохраняет переход состояния после блокировки документа владельцем UOW."""
        ...

    async def write_batch(
        self,
        generation_id: str,
        offset: int,
        requirements: tuple[NormativeRequirement, ...],
    ) -> None:
        """Повторяемо пишет ограниченную порцию требований неактивного поколения."""
        ...

    async def publish(self, generation: dict) -> None:
        """Атомарно заменяет указатель поколения, сохраняя прежние строки для rollback."""
        ...

    async def list_requirements(
        self, generation_id: str, offset: int, limit: int
    ) -> list[dict]:
        """Читает полную воспроизводимую последовательность без similarity top-K."""
        ...

    async def count_requirements(self, generation_id: str) -> int:
        """Подтверждает полноту записанных порций перед публикацией."""
        ...

    async def status_evidence(
        self, document_id: str, source_sha256: str
    ) -> tuple[NormativeStatusEvidence, ...]:
        """Читает журнал доказательств только конкретного содержимого документа."""
        ...

    async def add_status_evidence(self, evidence: NormativeStatusEvidence) -> None:
        """Добавляет аудируемое свидетельство; существующие записи не перезаписываются."""
        ...
