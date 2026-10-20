# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/database/normative_requirements.py

"""SQL-репозиторий атомарных нормативов без собственных commit и внешних вызовов."""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pdrd_knowledge_service.domain.normative_requirements import (
    NormativeRequirement,
    NormativeStatusEvidence,
)
from pdrd_knowledge_service.infrastructure.database.models import (
    NormativeRequirementGenerationModel as Generation,
)
from pdrd_knowledge_service.infrastructure.database.models import (
    NormativeRequirementModel as Requirement,
)
from pdrd_knowledge_service.infrastructure.database.models import (
    NormativeStatusEvidenceModel as Evidence,
)


class SqlAlchemyNormativeRequirementRepository:
    """Все изменения защищаются блокировкой документа в вызывающем use case."""

    def __init__(self, session: AsyncSession):
        """Использует session существующей UOW каталога."""
        self.session = session

    @staticmethod
    def _generation(row):
        """Читает active отдельно от исторического JSON snapshot."""
        return (
            {**row.payload, "active": row.active, "state": row.state} if row else None
        )

    async def get_generation(self, generation_id: str) -> dict | None:
        """Читает поколение по устойчивому UUID."""
        return self._generation(await self.session.get(Generation, UUID(generation_id)))

    async def active_generation(self, document_id: str) -> dict | None:
        """Возвращает лишь полностью опубликованное поколение."""
        row = await self.session.scalar(
            select(Generation).where(
                Generation.document_id == UUID(document_id), Generation.active
            )
        )
        return self._generation(row)

    async def save_generation(self, generation: dict) -> None:
        """Идемпотентно сохраняет lifecycle после блокировки документа."""
        values = {
            "id": UUID(generation["generation_id"]),
            "document_id": UUID(generation["document_id"]),
            "state": generation["state"],
            "active": generation["active"],
            "payload": generation,
        }
        statement = insert(Generation).values(**values)
        await self.session.execute(
            statement.on_conflict_do_update(index_elements=[Generation.id], set_=values)
        )

    async def write_batch(
        self,
        generation_id: str,
        offset: int,
        requirements: tuple[NormativeRequirement, ...],
    ) -> None:
        """Пишет только ограниченную порцию без изменения опубликованного поколения."""
        if not requirements:
            return
        values = [
            {
                "generation_id": UUID(generation_id),
                "requirement_id": item.requirement_id,
                "ordinal": offset + index,
                "payload": item.as_dict(),
            }
            for index, item in enumerate(requirements)
        ]
        statement = insert(Requirement).values(values)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[Requirement.generation_id, Requirement.requirement_id],
                set_={
                    "ordinal": statement.excluded.ordinal,
                    "payload": statement.excluded.payload,
                },
            )
        )

    async def publish(self, generation: dict) -> None:
        """Переключает active в одной транзакции; старые поколения остаются доступными."""
        await self.session.execute(
            update(Generation)
            .where(
                Generation.document_id == UUID(generation["document_id"]),
                Generation.active,
                Generation.id != UUID(generation["generation_id"]),
            )
            .values(active=False)
        )
        await self.save_generation({**generation, "state": "ready", "active": True})

    async def list_requirements(
        self, generation_id: str, offset: int, limit: int
    ) -> list[dict]:
        """Пагинация закрепляется за поколением, не смешивая редакции между запросами."""
        rows = await self.session.scalars(
            select(Requirement.payload)
            .where(Requirement.generation_id == UUID(generation_id))
            .order_by(Requirement.ordinal)
            .offset(offset)
            .limit(limit)
        )
        return list(rows)

    async def count_requirements(self, generation_id: str) -> int:
        """Проверяет число записанных атомов перед публикацией."""
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(Requirement)
                .where(Requirement.generation_id == UUID(generation_id))
            )
            or 0
        )

    async def status_evidence(
        self, document_id: str, source_sha256: str
    ) -> tuple[NormativeStatusEvidence, ...]:
        """Отделяет свидетельства разных исходных документов и их содержимого."""
        rows = await self.session.scalars(
            select(Evidence.payload)
            .where(
                Evidence.document_id == UUID(document_id),
                Evidence.source_sha256 == source_sha256,
            )
            .order_by(Evidence.id)
        )
        return tuple(
            NormativeStatusEvidence(
                **{**row, "clause_scope": tuple(row["clause_scope"])}
            )
            for row in rows
        )

    async def add_status_evidence(self, evidence: NormativeStatusEvidence) -> None:
        """Повтор записи одного подтверждения не создаёт дубликаты журнала."""
        await self.session.execute(
            insert(Evidence)
            .values(
                id=UUID(evidence.evidence_id),
                document_id=UUID(evidence.document_id),
                source_sha256=evidence.source_sha256,
                payload=evidence.as_dict(),
            )
            .on_conflict_do_nothing(index_elements=[Evidence.id])
        )
