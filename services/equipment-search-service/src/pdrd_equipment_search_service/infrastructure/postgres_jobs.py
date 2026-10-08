# services/equipment-search-service/src/pdrd_equipment_search_service/infrastructure/postgres_jobs.py

"""Состояние поиска и монотонный журнал событий каждого задания."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(slots=True)
class PostgresJobRepository:
    """Хранит задание и события в одной транзакционной схеме."""

    engine: AsyncEngine

    async def is_ready(self) -> bool:
        """Проверяет соединение и наличие таблиц после миграции."""
        async with self.engine.connect() as connection:
            await connection.execute(
                text("SELECT job_id FROM equipment_search.equipment_jobs LIMIT 0")
            )
        return True

    async def create_or_get(
        self,
        job_id: UUID,
        identities: list[dict],
        allow_unverified: bool,
    ) -> dict:
        """Идемпотентно создаёт задание с проверкой неизменности запроса."""
        now = datetime.now(UTC)
        async with self.engine.begin() as connection:
            await connection.execute(
                text("""
                    INSERT INTO equipment_search.equipment_jobs
                    (job_id, status, identities, allow_unverified,
                     created_at, updated_at)
                    VALUES (:job_id, 'queued', CAST(:identities AS JSONB),
                            :allow_unverified, :now, :now)
                    ON CONFLICT (job_id) DO NOTHING
                """),
                {
                    "job_id": job_id,
                    "identities": json.dumps(identities),
                    "allow_unverified": allow_unverified,
                    "now": now,
                },
            )
            row = (
                await connection.execute(
                    text("""
                        SELECT job_id, status, identities, allow_unverified,
                               cancel_requested, results, warning, event_sequence
                        FROM equipment_search.equipment_jobs
                        WHERE job_id = :job_id
                    """),
                    {"job_id": job_id},
                )
            ).one()
            if row.identities != identities or row.allow_unverified != allow_unverified:
                raise ValueError("Задание с этим ID уже создано с другими параметрами.")
        return dict(row._mapping)

    async def get(self, job_id: UUID) -> dict | None:
        """Читает состояние задания для API и восстановления."""
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text("""
                        SELECT job_id, status, identities, allow_unverified,
                               cancel_requested, results, warning, event_sequence
                        FROM equipment_search.equipment_jobs
                        WHERE job_id = :job_id
                    """),
                    {"job_id": job_id},
                )
            ).first()
        return dict(row._mapping) if row else None

    async def claim(self, job_id: UUID) -> bool:
        """Даёт выполнение только одному запущенному обработчику."""
        async with self.engine.begin() as connection:
            row = (
                await connection.execute(
                    text("""
                        UPDATE equipment_search.equipment_jobs
                        SET status = 'running', updated_at = :now
                        WHERE job_id = :job_id AND status = 'queued'
                          AND cancel_requested = false
                        RETURNING job_id
                    """),
                    {"job_id": job_id, "now": datetime.now(UTC)},
                )
            ).first()
        return row is not None

    async def cancel(self, job_id: UUID) -> bool:
        """Ставит долговечный флаг остановки новых операций."""
        async with self.engine.begin() as connection:
            row = (
                await connection.execute(
                    text("""
                        UPDATE equipment_search.equipment_jobs
                        SET cancel_requested = true, updated_at = :now
                        WHERE job_id = :job_id
                          AND status IN ('queued', 'running')
                        RETURNING job_id
                    """),
                    {"job_id": job_id, "now": datetime.now(UTC)},
                )
            ).first()
        if row:
            await self.append_event(
                job_id, "cancellation_requested", "Запрошена отмена поиска."
            )
        return row is not None

    async def is_cancelled(self, job_id: UUID) -> bool:
        """Проверяет флаг перед сетевыми и модельными операциями."""
        state = await self.get(job_id)
        return state is None or bool(state["cancel_requested"])

    async def append_event(
        self,
        job_id: UUID,
        event_type: str,
        message: str,
    ) -> int:
        """Атомарно выдаёт следующий номер события всего задания."""
        async with self.engine.begin() as connection:
            row = (
                await connection.execute(
                    text("""
                        UPDATE equipment_search.equipment_jobs
                        SET event_sequence = event_sequence + 1,
                            updated_at = :now
                        WHERE job_id = :job_id
                        RETURNING event_sequence
                    """),
                    {"job_id": job_id, "now": datetime.now(UTC)},
                )
            ).one()
            sequence = int(row.event_sequence)
            await connection.execute(
                text("""
                    INSERT INTO equipment_search.search_events
                    (job_id, sequence, event_type, message, created_at)
                    VALUES (:job_id, :sequence, :event_type, :message, :now)
                """),
                {
                    "job_id": job_id,
                    "sequence": sequence,
                    "event_type": event_type,
                    "message": message[:500],
                    "now": datetime.now(UTC),
                },
            )
        return sequence

    async def events_after(self, job_id: UUID, sequence: int) -> list[dict]:
        """Отдаёт компактную историю для SSE Last-Event-ID."""
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    text("""
                        SELECT sequence, event_type, message, created_at
                        FROM equipment_search.search_events
                        WHERE job_id = :job_id AND sequence > :sequence
                        ORDER BY sequence LIMIT 100
                    """),
                    {"job_id": job_id, "sequence": sequence},
                )
            ).all()
        return [dict(row._mapping) for row in rows]

    async def finish(
        self,
        job_id: UUID,
        status: str,
        results: list[dict],
        warning: str = "",
    ) -> None:
        """Фиксирует итог даже при частичной ошибке EQ-ветви."""
        if status not in {"completed", "incomplete", "cancelled"}:
            raise ValueError("Недопустимое итоговое состояние.")
        async with self.engine.begin() as connection:
            await connection.execute(
                text("""
                    UPDATE equipment_search.equipment_jobs
                    SET status = :status, results = CAST(:results AS JSONB),
                        warning = :warning, updated_at = :now
                    WHERE job_id = :job_id
                      AND status IN ('queued', 'running')
                """),
                {
                    "job_id": job_id,
                    "status": status,
                    "results": json.dumps(results, ensure_ascii=False),
                    "warning": warning[:500],
                    "now": datetime.now(UTC),
                },
            )

    async def recover_interrupted(self) -> None:
        """Помечает прерванные рестартом задания как частичные."""
        async with self.engine.begin() as connection:
            await connection.execute(
                text("""
                    WITH interrupted AS (
                        UPDATE equipment_search.equipment_jobs
                        SET status = 'incomplete',
                            warning = 'Обработка прервана перезапуском сервиса.',
                            event_sequence = event_sequence + 1, updated_at = :now
                        WHERE status IN ('queued', 'running')
                        RETURNING job_id, event_sequence
                    )
                    INSERT INTO equipment_search.search_events
                    (job_id, sequence, event_type, message, created_at)
                    SELECT job_id, event_sequence, 'incomplete',
                           'Обработка прервана перезапуском сервиса.', :now
                    FROM interrupted
                """),
                {"now": datetime.now(UTC)},
            )
