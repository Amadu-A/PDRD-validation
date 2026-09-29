# services/experience-service/src/pdrd_experience_service/infrastructure/database/catalog.py

"""Каталог PostgreSQL: CAS-аудит, SQL-фильтры и атомарная проверка исходного Review."""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pdrd_experience_service.application.catalog_snapshot import (
    example_from_json,
    example_to_json,
)
from pdrd_experience_service.domain.catalog import CatalogEntry, CatalogFilter, Example
from pdrd_experience_service.domain.review import (
    Origin,
    ReviewConflictError,
    ReviewError,
    ReviewSession,
)
from pdrd_experience_service.infrastructure.database.catalog_models import (
    CatalogEventModel,
    CatalogExampleModel,
)
from pdrd_experience_service.infrastructure.database.codec import snapshot_to_json
from pdrd_experience_service.infrastructure.database.models import (
    ConfirmedAreaModel,
    ReviewSessionModel,
)


class SqlAlchemyCatalogRepository:
    """Все операции используют независимые сессии; выдача проверяет текущий источник."""

    def __init__(self, sessions: Callable[[], AsyncSession]) -> None:
        """Получает фабрику из Composition Root, не создаёт engine самостоятельно."""
        self._sessions = sessions

    @staticmethod
    def _current():
        """Правка Review или отзыв координат немедленно исключают старый пример."""
        row, review, area = CatalogExampleModel, ReviewSessionModel, ConfirmedAreaModel
        return case(
            (
                and_(
                    review.revision == row.approved_revision,
                    review.source_sha256
                    == row.snapshot["source"]["source_sha256"].astext,
                    review.approved_revision == review.revision,
                    or_(
                        row.origin == "manual",
                        and_(
                            area.active.is_(True),
                            area.source_sha256 == review.source_sha256,
                            area.revision == row.area_revision,
                            area.content_signature == row.area_signature,
                        ),
                    ),
                ),
                True,
            ),
            else_=False,
        )

    @classmethod
    def _query(cls):
        """Соединяет только собственные таблицы схемы Experience."""
        row = CatalogExampleModel
        return (
            select(row, cls._current())
            .outerjoin(ReviewSessionModel, ReviewSessionModel.job_id == row.job_id)
            .outerjoin(
                ConfirmedAreaModel,
                and_(
                    ConfirmedAreaModel.job_id == row.job_id,
                    ConfirmedAreaModel.finding_id == row.finding_id,
                ),
            )
        )

    @staticmethod
    def _entry(row, current: bool) -> CatalogEntry:
        """Не выдаёт повреждённый JSONB за корректную редакцию."""
        example = example_from_json(row.snapshot)
        if (
            example.id != row.id
            or example.revision != row.revision
            or example.source.job_id != row.job_id
            or example.source.approved_revision != row.approved_revision
            or example.source.finding_id != row.finding_id
            or example.tag != row.tag
            or example.learning_use != row.learning_use
            or example.text != row.text
            or example.active != row.active
        ):
            raise ReviewError("Снимок каталога не соответствует ревизии.")
        return CatalogEntry(example, bool(current))

    async def capture(
        self,
        *,
        review: ReviewSession,
        area_versions: tuple,
        examples: tuple[Example, ...],
        actor: str,
    ) -> dict:
        """Блокировка Review согласована с записью подтверждений и редактированием."""
        created = 0
        async with self._sessions() as database, database.begin():
            row = await database.get(
                ReviewSessionModel, review.job_id, with_for_update=True
            )
            if row is None or row.snapshot != snapshot_to_json(review):
                raise ReviewConflictError("Review изменён перед записью Experience.")
            review.accepted_for_pdf()
            areas = (
                await database.scalars(
                    select(ConfirmedAreaModel)
                    .where(ConfirmedAreaModel.job_id == review.job_id)
                    .order_by(ConfirmedAreaModel.finding_id)
                )
            ).all()
            if (
                tuple((area.finding_id, area.revision) for area in areas)
                != area_versions
            ):
                raise ReviewConflictError(
                    "Подтверждённые области изменены перед записью Experience."
                )
            by_finding = {area.finding_id: area for area in areas}
            for example in examples:
                source = example.source
                area = by_finding.get(source.finding_id)
                if (
                    source.job_id != review.job_id
                    or source.approved_revision != review.revision
                ):
                    raise ReviewError("Пример не принадлежит утверждённому Review.")
                if source.origin is Origin.VLM and (area is None or not area.active):
                    raise ReviewConflictError("Область примера больше не подтверждена.")
                values = {
                    "id": example.id,
                    "example_key": f"{source.example_key}:review:{source.approved_revision}",
                    "job_id": source.job_id,
                    "finding_id": source.finding_id,
                    "approved_revision": source.approved_revision,
                    "area_revision": area.revision if area else 0,
                    "area_signature": area.content_signature if area else "",
                    "revision": example.revision,
                    "origin": source.origin.value,
                    "tag": example.tag,
                    "decision": source.decision.value,
                    "learning_use": example.learning_use,
                    "active": example.active,
                    "document_title": example.document_title,
                    "text": example.text,
                    "normative_basis": example.normative_basis,
                    "source_filename": source.source_filename,
                    "snapshot": example_to_json(example),
                    "created_at": example.created_at,
                }
                inserted = await database.scalar(
                    insert(CatalogExampleModel)
                    .values(**values)
                    .on_conflict_do_nothing(index_elements=[CatalogExampleModel.id])
                    .returning(CatalogExampleModel.id)
                )
                if inserted is not None:
                    database.add(
                        CatalogEventModel(
                            example_id=example.id,
                            revision=0,
                            actor=actor,
                            occurred_at=example.created_at,
                            snapshot=values["snapshot"],
                        )
                    )
                    created += 1
        return {
            "created": created,
            "job_id": str(review.job_id),
            "revision": review.revision,
        }

    async def list(
        self, criteria: CatalogFilter
    ) -> tuple[tuple[CatalogEntry, ...], int]:
        """Поиск использует параметры SQLAlchemy и не загружает весь каталог в память."""
        row = CatalogExampleModel
        query = self._query()
        if criteria.query:
            query = query.where(
                or_(
                    *(
                        field.icontains(criteria.query, autoescape=True)
                        for field in (
                            row.text,
                            row.normative_basis,
                            row.document_title,
                            row.source_filename,
                        )
                    )
                )
            )
        tag, _, decision = criteria.tag.partition(":")
        if tag:
            query = query.where(row.tag == tag)
        if decision:
            query = query.where(row.decision == decision)
        if criteria.decision:
            query = query.where(row.decision == criteria.decision)
        if criteria.learning_use:
            query = query.where(row.learning_use == criteria.learning_use)
        if criteria.job_id:
            query = query.where(row.job_id == criteria.job_id)
        if criteria.active is not None:
            query = query.where(
                and_(row.active.is_(True), self._current()) == criteria.active
            )
        async with self._sessions() as database, database.begin():
            total = await database.scalar(
                select(func.count()).select_from(query.subquery())
            )
            records = await database.execute(
                query.order_by(row.created_at.desc(), row.id)
                .offset(criteria.offset)
                .limit(criteria.limit)
            )
            return tuple(
                self._entry(record, current) for record, current in records
            ), total

    async def get(self, example_id: UUID) -> CatalogEntry | None:
        """Получает одну запись вместе с актуальностью её источника."""
        async with self._sessions() as database:
            result = (
                await database.execute(
                    self._query().where(CatalogExampleModel.id == example_id)
                )
            ).first()
            return self._entry(*result) if result is not None else None

    async def update(self, *, example: Example, expected_revision: int) -> CatalogEntry:
        """Правка снимка и добавление события — одна транзакция с CAS."""
        async with self._sessions() as database, database.begin():
            row = await database.get(
                CatalogExampleModel, example.id, with_for_update=True
            )
            if row is None:
                raise LookupError("Пример Experience не найден.")
            if (
                row.revision != expected_revision
                or example.revision != expected_revision + 1
            ):
                raise ReviewConflictError("Пример изменён в другой вкладке.")
            original = example_from_json(row.snapshot)
            if example.source != original.source or example.crops != original.crops:
                raise ReviewError("Нельзя подменять исходные данные примера.")
            row.snapshot = example_to_json(example)
            row.revision, row.tag, row.learning_use = (
                example.revision,
                example.tag,
                example.learning_use,
            )
            row.active, row.document_title = example.active, example.document_title
            row.text, row.normative_basis = example.text, example.normative_basis
            database.add(
                CatalogEventModel(
                    example_id=example.id,
                    revision=example.revision,
                    actor=example.curated_by,
                    occurred_at=example.updated_at,
                    snapshot=row.snapshot,
                )
            )
        return await self.get(example.id)

    async def history(self, example_id: UUID) -> tuple[dict, ...]:
        """Возвращает сохранённые снимки без изменения прежних событий."""
        async with self._sessions() as database:
            events = (
                await database.scalars(
                    select(CatalogEventModel)
                    .where(CatalogEventModel.example_id == example_id)
                    .order_by(CatalogEventModel.revision)
                )
            ).all()
            return tuple(
                {
                    "revision": event.revision,
                    "actor": event.actor,
                    "occurred_at": event.occurred_at.isoformat(),
                    "snapshot": event.snapshot,
                }
                for event in events
            )
