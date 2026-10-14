# services/equipment-search-service/src/pdrd_equipment_search_service/infrastructure/postgres_jobs.py

"""Состояние поиска и монотонный журнал событий каждого задания."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


def _state(row) -> dict:
    """Добавляет признак фонового выполнения из сохранённой политики."""
    data = dict(row._mapping)
    data["background"] = bool(data.get("policy", {}).get("background"))
    return data


@dataclass(slots=True)
class PostgresJobRepository:
    """Хранит задание и события в одной транзакционной схеме."""

    engine: AsyncEngine

    async def is_ready(self) -> bool:
        """Проверяет соединение и наличие таблиц после миграции."""
        async with self.engine.connect() as connection:
            await connection.execute(
                text(
                    "SELECT policy, checkpoint, revision, resume_count "
                    "FROM equipment_search.equipment_jobs LIMIT 0"
                )
            )
            await connection.execute(
                text("SELECT payload FROM equipment_search.job_versions LIMIT 0")
            )
            await connection.execute(
                text("SELECT payload FROM equipment_search.job_models LIMIT 0")
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
                               cancel_requested,
                               CASE WHEN status IN ('completed', 'incomplete', 'cancelled') THEN results
                               ELSE COALESCE((SELECT jsonb_agg(m.payload ORDER BY m.model_index)
                                   FROM equipment_search.job_models AS m WHERE m.job_id = j.job_id), results)
                               END AS results, warning, event_sequence,
                               policy, checkpoint, revision, resume_count
                        FROM equipment_search.equipment_jobs AS j
                        WHERE job_id = :job_id
                    """),
                    {"job_id": job_id},
                )
            ).one()
            defaults = {
                "manufacturer_status": "explicit",
                "model_status": "confirmed",
                "variant_kind": "execution",
                "article": "",
            }
            saved = [{**defaults, **item} for item in row.identities]
            requested = [{**defaults, **item} for item in identities]
            if saved != requested or row.allow_unverified != allow_unverified:
                raise ValueError("Задание с этим ID уже создано с другими параметрами.")
        return _state(row)

    async def get(self, job_id: UUID) -> dict | None:
        """Читает состояние задания для API и восстановления."""
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text("""
                        SELECT job_id, status, identities, allow_unverified,
                               cancel_requested,
                               CASE WHEN status IN ('completed', 'incomplete', 'cancelled') THEN results
                               ELSE COALESCE((SELECT jsonb_agg(m.payload ORDER BY m.model_index)
                                   FROM equipment_search.job_models AS m WHERE m.job_id = j.job_id), results)
                               END AS results, warning, event_sequence,
                               policy, checkpoint, revision, resume_count
                        FROM equipment_search.equipment_jobs AS j
                        WHERE job_id = :job_id
                    """),
                    {"job_id": job_id},
                )
            ).first()
        return _state(row) if row else None

    async def configure(self, job_id: UUID, policy: dict) -> dict:
        """Однократно закрепляет ограниченную политику логической проверки."""
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE equipment_search.equipment_jobs SET policy = CAST(:policy AS JSONB), "
                    "revision = revision + 1 WHERE job_id = :id AND policy = '{}'::jsonb"
                ),
                {"id": job_id, "policy": json.dumps(policy)},
            )
            await connection.execute(
                text("""
                INSERT INTO equipment_search.job_models (job_id, model_index, payload)
                SELECT j.job_id, e.ordinality::integer - 1,
                    COALESCE(j.results -> (e.ordinality::integer - 1),
                        jsonb_build_object('identity', e.value, 'status', 'not_checked',
                            'reason_code', 'not_checked', 'metrics', '{}'::jsonb,
                            'attempts', '[]'::jsonb, 'retryable', true))
                FROM equipment_search.equipment_jobs AS j,
                    jsonb_array_elements(j.identities) WITH ORDINALITY AS e(value, ordinality)
                WHERE j.job_id = :id
                ON CONFLICT (job_id, model_index) DO NOTHING
            """),
                {"id": job_id},
            )
        return await self.get(job_id)

    async def checkpoint(
        self, job_id: UUID, results: list[dict], checkpoint: dict
    ) -> None:
        """Атомарно сохраняет результаты моделей, позицию и новую версию отчёта."""
        async with self.engine.begin() as connection:
            changed = await connection.scalar(
                text(
                    "UPDATE equipment_search.equipment_jobs SET checkpoint = CAST(:checkpoint AS JSONB), "
                    "revision = revision + 1, updated_at = :now "
                    "WHERE job_id = :id AND status = 'running' RETURNING job_id"
                ),
                {
                    "id": job_id,
                    "checkpoint": json.dumps(checkpoint),
                    "now": datetime.now(UTC),
                },
            )
            if changed is not None:
                index = int(
                    (checkpoint.get("inflight") or {}).get(
                        "index",
                        checkpoint.get(
                            "model_index",
                            max(0, int(checkpoint.get("cursor") or 0) - 1),
                        ),
                    )
                )
                if index < len(results):
                    await connection.execute(
                        text("""
                        INSERT INTO equipment_search.job_models (job_id, model_index, payload)
                        VALUES (:id, :index, CAST(:payload AS JSONB))
                        ON CONFLICT (job_id, model_index) DO UPDATE SET payload = EXCLUDED.payload
                    """),
                        {
                            "id": job_id,
                            "index": index,
                            "payload": json.dumps(results[index], ensure_ascii=False),
                        },
                    )

    async def queued_jobs(self) -> tuple[UUID, ...]:
        """Приоритетно выбирает короткие задания без внешней очереди и потери больших."""
        async with self.engine.connect() as connection:
            rows = await connection.scalars(
                text(
                    "SELECT job_id FROM equipment_search.equipment_jobs WHERE status = 'queued' "
                    "AND cancel_requested = false ORDER BY CASE WHEN job_id = ("
                    "SELECT job_id FROM equipment_search.equipment_jobs WHERE status = 'queued' "
                    "AND cancel_requested = false ORDER BY updated_at, created_at LIMIT 1) THEN 0 ELSE 1 END, "
                    "jsonb_array_length(identities), updated_at LIMIT 100"
                )
            )
            return tuple(rows)

    async def resume(self, job_id: UUID, revision: int, max_runs: int) -> bool:
        """Продолжает неполную проверку по ожидаемой версии, сохраняя готовые модели."""
        async with self.engine.begin() as connection:
            row = await connection.scalar(
                text(
                    "UPDATE equipment_search.equipment_jobs SET status = 'queued', cancel_requested = false, "
                    "checkpoint = checkpoint || CAST(:reset AS JSONB), "
                    "resume_count = resume_count + 1, revision = revision + 1, warning = '', updated_at = :now "
                    "WHERE job_id = :id AND revision = :revision AND status IN ('incomplete', 'cancelled') "
                    "AND resume_count < :max_runs RETURNING job_id"
                ),
                {
                    "id": job_id,
                    "revision": revision,
                    "max_runs": max_runs,
                    "now": datetime.now(UTC),
                    "reset": json.dumps({"round": 0, "cursor": 0}),
                },
            )
        return row is not None

    async def release(self, job_id: UUID) -> bool:
        """Освобождает локальный слот, сохраняя checkpoint для следующей порции."""
        async with self.engine.begin() as connection:
            row = await connection.scalar(
                text(
                    "UPDATE equipment_search.equipment_jobs SET status = 'queued', updated_at = :now, "
                    "revision = revision + 1 WHERE job_id = :id AND status = 'running' "
                    "AND cancel_requested = false RETURNING job_id"
                ),
                {"id": job_id, "now": datetime.now(UTC)},
            )
            return row is not None

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
        async with self.engine.connect() as connection:
            value = await connection.scalar(
                text(
                    "SELECT cancel_requested FROM equipment_search.equipment_jobs WHERE job_id = :id"
                ),
                {"id": job_id},
            )
        return value is None or bool(value)

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
                        warning = :warning, updated_at = :now, revision = revision + 1
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
            await connection.execute(
                text("""
                INSERT INTO equipment_search.job_models (job_id, model_index, payload)
                SELECT j.job_id, e.ordinality::integer - 1, e.value
                FROM equipment_search.equipment_jobs AS j,
                    jsonb_array_elements(j.results) WITH ORDINALITY AS e(value, ordinality)
                WHERE j.job_id = :id
                ON CONFLICT (job_id, model_index) DO UPDATE SET payload = EXCLUDED.payload
            """),
                {"id": job_id},
            )
            await connection.execute(
                text("""
                INSERT INTO equipment_search.job_versions (job_id, revision, payload, created_at)
                SELECT job_id, revision,
                    jsonb_build_object('job_id', job_id, 'revision', revision, 'status', status,
                        'results', results, 'identities', identities, 'warning', warning,
                        'policy', policy, 'resume_count', resume_count,
                        'background', COALESCE((policy->>'background')::boolean, false)), :now
                FROM equipment_search.equipment_jobs WHERE job_id = :id
                    AND status IN ('completed', 'incomplete', 'cancelled')
                ON CONFLICT (job_id, revision) DO NOTHING
            """),
                {"id": job_id, "now": datetime.now(UTC)},
            )

    async def version(self, job_id: UUID, revision: int) -> dict | None:
        """Читает неизменяемую завершённую редакцию, доступную после продолжения."""
        async with self.engine.connect() as connection:
            return await connection.scalar(
                text(
                    "SELECT payload FROM equipment_search.job_versions WHERE job_id = :id AND revision = :revision"
                ),
                {"id": job_id, "revision": revision},
            )

    async def recover_interrupted(self) -> None:
        """Восстанавливает checkpoint; ранее запрошенная отмена остаётся отменой."""
        async with self.engine.begin() as connection:
            await connection.execute(
                text("""
                    WITH interrupted AS (
                        UPDATE equipment_search.equipment_jobs AS j
                        SET status = CASE WHEN cancel_requested THEN 'cancelled' ELSE 'queued' END,
                            results = CASE WHEN cancel_requested THEN COALESCE(
                                (SELECT jsonb_agg(m.payload ORDER BY m.model_index)
                                 FROM equipment_search.job_models AS m WHERE m.job_id = j.job_id), results)
                                ELSE results END,
                            warning = '',
                            event_sequence = event_sequence + 1, updated_at = :now
                        WHERE status = 'running'
                        RETURNING job_id, event_sequence, cancel_requested
                    )
                    INSERT INTO equipment_search.search_events
                    (job_id, sequence, event_type, message, created_at)
                    SELECT job_id, event_sequence,
                           CASE WHEN cancel_requested THEN 'cancelled' ELSE 'recovered' END,
                           CASE WHEN cancel_requested THEN 'Поиск отменён пользователем.'
                           ELSE 'Поиск продолжен с сохранённого прогресса.' END, :now
                    FROM interrupted
                """),
                {"now": datetime.now(UTC)},
            )
            await connection.execute(
                text("""
                INSERT INTO equipment_search.job_versions (job_id, revision, payload, created_at)
                SELECT job_id, revision,
                    jsonb_build_object('job_id', job_id, 'revision', revision, 'status', status,
                        'results', results, 'identities', identities, 'warning', warning,
                        'policy', policy, 'resume_count', resume_count,
                        'background', COALESCE((policy->>'background')::boolean, false)), :now
                FROM equipment_search.equipment_jobs WHERE status = 'cancelled'
                ON CONFLICT (job_id, revision) DO NOTHING
            """),
                {"now": datetime.now(UTC)},
            )
