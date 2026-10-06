# services/experience-service/src/pdrd_experience_service/infrastructure/database/catalog.py

"""Каталог PostgreSQL: CAS-аудит, SQL-фильтры и атомарная проверка исходного Review."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from pdrd_experience_service.application.catalog_snapshot import (
    example_from_json,
    example_to_json,
)
from pdrd_experience_service.domain.catalog import CatalogEntry, CatalogFilter, Example
from pdrd_experience_service.domain.catalog_identity import content_key
from pdrd_experience_service.domain.catalog_repair import repair_example
from pdrd_experience_service.domain.experience_selection import ExperienceCandidate
from pdrd_experience_service.domain.review import (
    Origin,
    ReviewConflictError,
    ReviewError,
    ReviewSession,
)
from pdrd_experience_service.infrastructure.database.catalog_models import (
    CatalogEventModel,
    CatalogExampleModel,
    CatalogOccurrenceModel,
)
from pdrd_experience_service.infrastructure.database.codec import snapshot_to_json
from pdrd_experience_service.infrastructure.database.models import (
    ConfirmedAreaModel,
    ReviewSessionModel,
)


class SqlAlchemyCatalogRepository:
    """Все операции используют независимые сессии; выдача проверяет текущий источник."""

    def __init__(self, sessions: Callable[[], AsyncSession]) -> None:
        """Получает фабрику при сборке приложения; соединение с БД отдельно не создаёт."""
        self._sessions = sessions

    @staticmethod
    def _current():
        """Правка Review или отзыв координат немедленно исключают старый пример."""
        row, review, area = CatalogExampleModel, ReviewSessionModel, ConfirmedAreaModel
        occurrence = CatalogOccurrenceModel
        later_review, later_area = (
            aliased(ReviewSessionModel),
            aliased(ConfirmedAreaModel),
        )
        repeated = (
            select(occurrence.example_id)
            .join(later_review, later_review.job_id == occurrence.job_id)
            .outerjoin(
                later_area,
                and_(
                    later_area.job_id == occurrence.job_id,
                    later_area.finding_id == occurrence.finding_id,
                ),
            )
            .where(
                occurrence.example_id == row.id,
                later_review.source_sha256 == occurrence.source_sha256,
                later_review.revision == occurrence.approved_revision,
                later_review.approved_revision == later_review.revision,
                or_(
                    row.origin == "manual",
                    occurrence.area_revision == 0,
                    and_(
                        later_area.active.is_(True),
                        later_area.revision == occurrence.area_revision,
                        later_area.content_signature == occurrence.area_signature,
                        later_area.source_sha256 == occurrence.source_sha256,
                    ),
                ),
            )
            .correlate(row)
            .exists()
        )
        return case(
            (
                or_(
                    repeated,
                    and_(
                        review.revision == row.approved_revision,
                        review.source_sha256
                        == row.snapshot["source"]["source_sha256"].astext,
                        review.approved_revision == review.revision,
                        or_(
                            row.origin == "manual",
                            row.area_revision == 0,
                            and_(
                                area.active.is_(True),
                                area.source_sha256 == review.source_sha256,
                                area.revision == row.area_revision,
                                area.content_signature == row.area_signature,
                            ),
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
        expected_revisions: tuple[tuple[UUID, int], ...] = (),
    ) -> dict:
        """Блокировка Review согласована с записью подтверждений и редактированием."""
        created = 0
        repaired = []
        async with self._sessions() as database, database.begin():
            row = await database.get(
                ReviewSessionModel, review.job_id, with_for_update=True
            )
            if row is None or row.snapshot != snapshot_to_json(review):
                raise ReviewConflictError("Review изменён перед записью Experience.")
            review.accepted_for_pdf()
            # Один SHA сериализует параллельные утверждения разных job. Блокировка
            # живёт только внутри транзакции; разные PDF сохраняются независимо.
            lock_key = int(review.source_sha256[:16], 16)
            if lock_key >= 2**63:
                lock_key -= 2**64
            await database.execute(select(func.pg_advisory_xact_lock(lock_key)))
            if expected_revisions:
                expected = dict(expected_revisions)
                selected = (
                    await database.scalars(
                        select(CatalogExampleModel)
                        .where(CatalogExampleModel.id.in_(expected))
                        .order_by(CatalogExampleModel.id)
                        .with_for_update()
                    )
                ).all()
                if len(selected) != len(expected) or any(
                    item.revision != expected[item.id] or item.deleted
                    for item in selected
                ):
                    raise ReviewConflictError(
                        "Замечания изменены перед восстановлением метаданных."
                    )
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
                finding = next(
                    (
                        item
                        for item in review.findings
                        if item.finding_id == source.finding_id
                    ),
                    None,
                )
                if (
                    source.job_id != review.job_id
                    or source.approved_revision != review.revision
                    or finding is None
                    or source.original_text != finding.original_text
                    or source.original_basis != finding.original_basis
                    or source.proposed_regions != finding.proposed_regions
                ):
                    raise ReviewError("Пример не принадлежит утверждённому Review.")
                if source.area_source == "vlm_original" and (
                    source.decision.value != "rejected"
                    or not source.proposed_regions
                    or source.issue_regions
                    != tuple(item.bbox for item in source.proposed_regions)
                ):
                    raise ReviewError(
                        "Область Bad не соответствует первоначальным координатам VLM."
                    )
                if (
                    source.origin is Origin.VLM
                    and source.issue_regions
                    and source.area_source == "engineer_confirmed"
                    and (area is None or not area.active)
                ):
                    raise ReviewConflictError("Область примера больше не подтверждена.")
                values = {
                    "id": example.id,
                    "example_key": f"{source.example_key}:review:{source.approved_revision}",
                    "job_id": source.job_id,
                    "finding_id": source.finding_id,
                    "approved_revision": source.approved_revision,
                    "area_revision": area.revision
                    if area
                    and source.issue_regions
                    and source.area_source == "engineer_confirmed"
                    else 0,
                    "area_signature": area.content_signature
                    if area
                    and source.issue_regions
                    and source.area_source == "engineer_confirmed"
                    else "",
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
                    "content_key": content_key(source),
                    "deleted": False,
                    "section_id": example.section_id,
                    "section_title": example.section_title,
                }
                prior_id = await database.scalar(
                    select(CatalogExampleModel.id)
                    .where(
                        or_(
                            CatalogExampleModel.content_key == values["content_key"],
                            CatalogExampleModel.id == example.id,
                        )
                    )
                    .order_by(CatalogExampleModel.created_at, CatalogExampleModel.id)
                    .limit(1)
                )
                if prior_id is not None:
                    prior = await database.get(
                        CatalogExampleModel, prior_id, with_for_update=True
                    )
                    original = example_from_json(prior.snapshot)
                    keep_confirmation = bool(original.source.issue_regions) and (
                        original.source.area_source == "engineer_confirmed"
                        and source.area_source == "unlocated"
                        and original.source.decision.value == "accepted"
                    )
                    updated = repair_example(original, example, actor)
                    if updated != original:
                        prior.snapshot = example_to_json(updated)
                        prior.revision = updated.revision
                        prior.section_id, prior.section_title = (
                            updated.section_id,
                            updated.section_title,
                        )
                        prior.content_key = content_key(updated.source)
                        if (
                            original.source.job_id == source.job_id
                            and not keep_confirmation
                        ):
                            prior.area_revision, prior.area_signature = (
                                values["area_revision"],
                                values["area_signature"],
                            )
                        if updated.source.area_source == "vlm_original":
                            prior.area_revision, prior.area_signature = 0, ""
                        database.add(
                            CatalogEventModel(
                                example_id=prior_id,
                                revision=updated.revision,
                                actor=actor,
                                occurred_at=updated.updated_at,
                                snapshot=prior.snapshot,
                            )
                        )
                        repaired.append(
                            {
                                "id": str(prior_id),
                                "previous_revision": original.revision,
                                "revision": updated.revision,
                            }
                        )
                    if not keep_confirmation:
                        await self._occurrence(
                            database, example, prior_id, values, actor
                        )
                    continue
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
                await self._occurrence(database, example, example.id, values, actor)
        return {
            "created": created,
            "job_id": str(review.job_id),
            "revision": review.revision,
            "repaired": repaired,
        }

    @staticmethod
    async def _occurrence(
        database: AsyncSession,
        example: Example,
        example_id: UUID,
        values: dict,
        actor: str,
    ) -> None:
        """Повторный прогон не перезаписывает инженерную редакцию и удалённый флаг."""
        source = example.source
        await database.execute(
            insert(CatalogOccurrenceModel)
            .values(
                example_id=example_id,
                job_id=source.job_id,
                finding_id=source.finding_id,
                approved_revision=source.approved_revision,
                area_revision=values["area_revision"],
                area_signature=values["area_signature"],
                source_sha256=source.source_sha256,
                actor=actor,
                snapshot=values["snapshot"]["source"],
            )
            .on_conflict_do_update(
                index_elements=[
                    CatalogOccurrenceModel.example_id,
                    CatalogOccurrenceModel.job_id,
                    CatalogOccurrenceModel.finding_id,
                    CatalogOccurrenceModel.approved_revision,
                ],
                set_={
                    "area_revision": values["area_revision"],
                    "area_signature": values["area_signature"],
                    "snapshot": values["snapshot"]["source"],
                },
            )
        )

    async def list(
        self, criteria: CatalogFilter
    ) -> tuple[tuple[CatalogEntry, ...], int]:
        """Поиск использует параметры SQLAlchemy и не загружает весь каталог в память."""
        row = CatalogExampleModel
        query = self._query().where(row.deleted.is_(False))
        if criteria.author:
            query = query.where(
                row.snapshot["source"]["created_by"].astext == criteria.author
            )
        if criteria.section_id:
            query = query.where(row.section_id == criteria.section_id)
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
            query = query.where(
                or_(
                    row.job_id == criteria.job_id,
                    select(CatalogOccurrenceModel.example_id)
                    .where(
                        CatalogOccurrenceModel.example_id == row.id,
                        CatalogOccurrenceModel.job_id == criteria.job_id,
                    )
                    .correlate(row)
                    .exists(),
                )
            )
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

    async def authors(self, *, offset: int, limit: int) -> tuple[tuple[str, ...], int]:
        """DISTINCT по неизменяемому source работает и для прежних строк без миграции."""
        row = CatalogExampleModel
        author = row.snapshot["source"]["created_by"].astext.label("author")
        query = select(author).where(row.deleted.is_(False)).distinct().subquery()
        async with self._sessions() as database:
            total = await database.scalar(select(func.count()).select_from(query))
            names = await database.scalars(
                select(query.c.author)
                .order_by(query.c.author)
                .offset(offset)
                .limit(limit)
            )
            return tuple(names), total

    async def get(self, example_id: UUID) -> CatalogEntry | None:
        """Получает одну запись вместе с актуальностью её источника."""
        async with self._sessions() as database:
            result = (
                await database.execute(
                    self._query().where(CatalogExampleModel.id == example_id)
                )
            ).first()
            return self._entry(*result) if result is not None else None

    async def find_source(self, source: ExperienceCandidate) -> CatalogEntry | None:
        """Старый снимок находится по происхождению даже после дополнения координат."""
        row, occurrence = CatalogExampleModel, CatalogOccurrenceModel
        known = (
            select(occurrence.example_id)
            .where(
                occurrence.example_id == row.id,
                occurrence.job_id == source.job_id,
                occurrence.finding_id == source.finding_id,
                occurrence.approved_revision == source.approved_revision,
            )
            .correlate(row)
            .exists()
        )
        async with self._sessions() as database:
            result = (
                await database.execute(
                    self._query()
                    .where(
                        or_(
                            row.content_key == content_key(source),
                            known,
                            and_(
                                row.job_id == source.job_id,
                                row.finding_id == source.finding_id,
                                row.approved_revision == source.approved_revision,
                            ),
                        )
                    )
                    .order_by(row.created_at, row.id)
                    .limit(1)
                )
            ).first()
            return self._entry(*result) if result is not None else None

    async def get_many(self, example_ids: tuple[UUID, ...]) -> tuple[CatalogEntry, ...]:
        """Проверяет все ссылки E одним SQL-снимком, включая серверный JOIN актуальности."""
        if not example_ids:
            return ()
        async with self._sessions() as database:
            records = await database.execute(
                self._query().where(CatalogExampleModel.id.in_(example_ids))
            )
            return tuple(self._entry(record, current) for record, current in records)

    async def scan(self, *, after: UUID | None, limit: int) -> tuple[CatalogEntry, ...]:
        """Keyset-обход всего каталога без смещения при появлении новых записей."""
        query = self._query()
        if after is not None:
            query = query.where(CatalogExampleModel.id > after)
        async with self._sessions() as database:
            records = await database.execute(
                query.order_by(CatalogExampleModel.id).limit(limit)
            )
            return tuple(self._entry(record, current) for record, current in records)

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
            row.section_id, row.section_title = (
                example.section_id,
                example.section_title,
            )
            if original.deleted:
                raise LookupError("Пример Experience удалён.")
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

    async def delete_many(
        self, *, references: tuple[tuple[UUID, int], ...], actor: str
    ) -> int:
        """Транзакция блокирует строки в одном порядке и откатывает весь набор при конфликте."""
        expected = dict(references)
        async with self._sessions() as database, database.begin():
            rows = (
                await database.scalars(
                    select(CatalogExampleModel)
                    .where(CatalogExampleModel.id.in_(expected))
                    .order_by(CatalogExampleModel.id)
                    .with_for_update()
                )
            ).all()
            if len(rows) != len(expected):
                raise LookupError("Часть выбранных замечаний не найдена.")
            if any(row.revision != expected[row.id] or row.deleted for row in rows):
                raise ReviewConflictError(
                    "Выбранные замечания изменены; обновите каталог."
                )
            at = datetime.now(UTC)
            for row in rows:
                example = replace(
                    example_from_json(row.snapshot),
                    deleted=True,
                    active=False,
                    revision=row.revision + 1,
                    updated_at=at,
                    curated_by=actor,
                )
                row.deleted, row.active, row.revision = True, False, example.revision
                row.snapshot = example_to_json(example)
                database.add(
                    CatalogEventModel(
                        example_id=row.id,
                        revision=row.revision,
                        actor=actor,
                        occurred_at=at,
                        snapshot=row.snapshot,
                    )
                )
        return len(expected)

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
