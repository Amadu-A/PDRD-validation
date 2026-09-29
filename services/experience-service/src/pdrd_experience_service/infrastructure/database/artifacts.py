# services/experience-service/src/pdrd_experience_service/infrastructure/database/artifacts.py

"""Транзакционный реестр версий и долговечная очередь ручной индексации.

Аренда с heartbeat восстанавливается после остановки worker. Опубликованный состав
не перезаписывается; изменение каталога делает старые ссылки непригодными для E.
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pdrd_experience_service.application.use_cases.artifacts import version_view
from pdrd_experience_service.domain.artifact_projection import (
    artifact_member_projection,
)
from pdrd_experience_service.domain.artifact_quality import validate_quality_report
from pdrd_experience_service.domain.artifacts import ArtifactVersion, validate_name
from pdrd_experience_service.domain.index_projection import exclusion_reason
from pdrd_experience_service.domain.review import ReviewConflictError, ReviewError
from pdrd_experience_service.infrastructure.database.artifact_models import (
    AppliedArtifactModel,
    ArtifactEventModel,
    ArtifactMemberModel,
    ArtifactVersionModel,
)
from pdrd_experience_service.infrastructure.database.catalog import (
    SqlAlchemyCatalogRepository,
)
from pdrd_experience_service.infrastructure.database.catalog_models import (
    CatalogExampleModel,
)


def decode_version(value: dict) -> ArtifactVersion:
    """Восстанавливает только фиксированный доменный тип."""
    fields = dict(value)
    fields["id"] = UUID(fields["id"])
    fields["created_at"] = datetime.fromisoformat(fields["created_at"])
    fields["members"] = tuple(fields["members"])
    return ArtifactVersion(**fields)


class SqlAlchemyArtifactRepository:
    """Все таблицы принадлежат Experience; векторные точки принадлежат Knowledge."""

    def __init__(self, sessions: Callable[[], AsyncSession]) -> None:
        """Получает фабрику из composition root."""
        self._sessions = sessions

    @staticmethod
    def _save(
        database: AsyncSession,
        row: ArtifactVersionModel,
        version: ArtifactVersion,
        actor: str,
        operation: str,
    ) -> None:
        """Снимок и событие записываются в одной транзакции."""
        row.snapshot = version_view(version)
        row.name, row.status, row.deleted, row.revision = (
            version.name,
            version.status,
            version.deleted,
            version.revision,
        )
        database.add(
            ArtifactEventModel(
                id=uuid4(),
                version_id=version.id,
                operation=operation,
                actor=actor,
                occurred_at=datetime.now(UTC),
                snapshot=row.snapshot,
            )
        )

    async def list(self) -> tuple[ArtifactVersion, ...]:
        """Без удалённых версий; порядок стабилен для селекторов."""
        async with self._sessions() as database:
            rows = await database.scalars(
                select(ArtifactVersionModel)
                .where(ArtifactVersionModel.deleted.is_(False))
                .order_by(
                    ArtifactVersionModel.created_at.desc(), ArtifactVersionModel.id
                )
            )
            return tuple(decode_version(row.snapshot) for row in rows)

    async def get(self, version_id: UUID) -> ArtifactVersion | None:
        """Состав остаётся доступен аудиту после удаления из селектора."""
        async with self._sessions() as database:
            row = await database.get(ArtifactVersionModel, version_id)
            return decode_version(row.snapshot) if row else None

    async def create(self, version: ArtifactVersion) -> None:
        """CAS выбранных строк и запись всех members выполняются вместе."""
        async with self._sessions() as database, database.begin():
            ids = [UUID(item["example_id"]) for item in version.members]
            rows = (
                await database.execute(
                    SqlAlchemyCatalogRepository._query()
                    .where(CatalogExampleModel.id.in_(ids))
                    .order_by(CatalogExampleModel.id)
                    .with_for_update(of=CatalogExampleModel)
                )
            ).all()
            projections = {
                str(row.id): artifact_member_projection(
                    SqlAlchemyCatalogRepository._entry(row, current), kind=version.kind
                )
                for row, current in rows
            }
            if len(rows) != len(ids) or any(
                projections.get(item["example_id"]) != item for item in version.members
            ):
                raise ReviewConflictError(
                    "Выбранные примеры изменены перед записью версии."
                )
            row = ArtifactVersionModel(
                id=version.id,
                kind=version.kind,
                model=version.model,
                name=version.name,
                section_id=version.section_id,
                revision=0,
                status=version.status,
                deleted=False,
                snapshot=version_view(version),
                created_at=version.created_at,
                lease_owner="",
            )
            database.add(row)
            await database.flush()
            for item in version.members:
                database.add(
                    ArtifactMemberModel(
                        version_id=version.id,
                        example_id=UUID(item["example_id"]),
                        example_revision=item["example_revision"],
                        fingerprint=item["fingerprint"],
                        snapshot=item,
                    )
                )
            self._save(database, row, version, version.created_by, "create")

    @staticmethod
    async def _locked(
        database: AsyncSession, version_id: UUID, revision: int | None = None
    ) -> ArtifactVersionModel:
        """Один порядок блокировки для переименования, удаления и применения."""
        row = await database.get(ArtifactVersionModel, version_id, with_for_update=True)
        if row is None or row.deleted:
            raise LookupError("Версия не найдена.")
        if revision is not None and row.revision != revision:
            raise ReviewConflictError("Версия изменена; обновите список.")
        return row

    async def rename(
        self, *, version_id: UUID, revision: int, name: str, actor: str
    ) -> ArtifactVersion:
        """Имя не влияет на идентичность коллекции и manifest."""
        async with self._sessions() as database, database.begin():
            row = await self._locked(database, version_id, revision)
            version = replace(
                decode_version(row.snapshot),
                name=validate_name(name),
                revision=revision + 1,
            )
            self._save(database, row, version, actor, "rename")
        return version

    async def delete(self, *, version_id: UUID, revision: int, actor: str) -> None:
        """Применённую или строящуюся версию сначала надо заменить/дождаться завершения."""
        async with self._sessions() as database, database.begin():
            row = await self._locked(database, version_id, revision)
            active = await database.scalar(
                select(AppliedArtifactModel.version_id).where(
                    AppliedArtifactModel.version_id == version_id
                )
            )
            if active or row.status == "building":
                raise ReviewConflictError(
                    "Нельзя удалить применённую или строящуюся версию."
                )
            version = replace(
                decode_version(row.snapshot), deleted=True, revision=revision + 1
            )
            self._save(database, row, version, actor, "delete")

    async def claim(
        self, *, worker: str, model: str, identity: str, dimension: int
    ) -> ArtifactVersion | None:
        """Аренда на 10 минут продлевается worker до окончания работы."""
        now = datetime.now(UTC)
        async with self._sessions() as database, database.begin():
            row = await database.scalar(
                select(ArtifactVersionModel)
                .where(
                    ArtifactVersionModel.kind == "vector",
                    ArtifactVersionModel.deleted.is_(False),
                    ArtifactVersionModel.model == model,
                    or_(
                        ArtifactVersionModel.status == "queued",
                        and_(
                            ArtifactVersionModel.status == "building",
                            ArtifactVersionModel.lease_until < now,
                        ),
                    ),
                )
                .order_by(ArtifactVersionModel.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            version = replace(
                decode_version(row.snapshot),
                status="building",
                revision=row.revision + 1,
                collection=f"pdrd_e_{identity}_{row.id.hex}",
                embedding_identity=identity,
                dimension=dimension,
                error="",
            )
            row.lease_owner, row.lease_until = worker, now + timedelta(minutes=10)
            self._save(database, row, version, worker, "claim")
            return version

    async def finish(
        self, *, version_id: UUID, worker: str, result: dict
    ) -> ArtifactVersion:
        """Heartbeat и финальный статус относятся только к текущей действующей аренде."""
        now = datetime.now(UTC)
        async with self._sessions() as database, database.begin():
            row = await self._locked(database, version_id)
            if (
                row.status != "building"
                or row.lease_owner != worker
                or row.lease_until is None
                or row.lease_until < now
            ):
                raise ReviewConflictError(
                    "Аренда задачи истекла или принадлежит другому worker."
                )
            version = decode_version(row.snapshot)
            if result["status"] == "heartbeat":
                row.lease_until = now + timedelta(minutes=10)
                return version
            version = replace(
                version,
                status=result["status"],
                error=result.get("error", ""),
                revision=row.revision + 1,
            )
            self._save(database, row, version, worker, "finish")
            row.lease_owner, row.lease_until = "", None
        return version

    async def applied(self) -> tuple[dict, ...]:
        """Просмотр версии не меняет эти строки."""
        async with self._sessions() as database:
            rows = await database.scalars(
                select(AppliedArtifactModel).order_by(
                    AppliedArtifactModel.kind, AppliedArtifactModel.section_id
                )
            )
            return tuple(
                {
                    "kind": row.kind,
                    "section_id": row.section_id,
                    "version_id": str(row.version_id),
                    "actor": row.actor,
                    "applied_at": row.applied_at.isoformat(),
                }
                for row in rows
            )

    async def apply(self, *, version_id: UUID, revision: int, actor: str) -> dict:
        """Подготовленный датасет не выдаётся за веса; непроверенная база не включается в анализ."""
        async with self._sessions() as database, database.begin():
            row = await self._locked(database, version_id, revision)
            version = decode_version(row.snapshot)
            if (
                version.status != "ready"
                or not version.quality_approved
                or (version.kind == "fine_tune" and not version.weights_sha256)
            ):
                raise ReviewError(
                    "Для рабочего анализа нужна готовая версия с проверенным качеством. "
                    "Набор для дообучения ещё не является моделью."
                )
            if version.kind == "vector":
                if not validate_quality_report(version.quality_report, version):
                    raise ReviewError("Отчёт качества не разрешает применение версии.")
                await self._require_current_members(database, version)
            at = datetime.now(UTC)
            await database.execute(
                insert(AppliedArtifactModel)
                .values(
                    kind=version.kind,
                    section_id=version.section_id,
                    version_id=version_id,
                    actor=actor,
                    applied_at=at,
                )
                .on_conflict_do_update(
                    index_elements=[
                        AppliedArtifactModel.kind,
                        AppliedArtifactModel.section_id,
                    ],
                    set_={"version_id": version_id, "actor": actor, "applied_at": at},
                )
            )
            version = replace(version, revision=revision + 1)
            self._save(database, row, version, actor, "apply")
        return {
            "kind": version.kind,
            "section_id": version.section_id,
            "version_id": str(version_id),
            "applied_at": at.isoformat(),
        }

    @staticmethod
    async def _require_current_members(
        database: AsyncSession, version: ArtifactVersion
    ) -> None:
        """Блокирует выбранные строки и запрещает допуск изменившегося состава."""
        ids = [UUID(item["example_id"]) for item in version.members]
        rows = (
            await database.execute(
                SqlAlchemyCatalogRepository._query()
                .where(CatalogExampleModel.id.in_(ids))
                .order_by(CatalogExampleModel.id)
                .with_for_update(of=CatalogExampleModel)
            )
        ).all()
        entries = {
            str(row.id): SqlAlchemyCatalogRepository._entry(row, current)
            for row, current in rows
        }
        if len(entries) != len(version.members) or any(
            exclusion_reason(entries[item["example_id"]])
            or artifact_member_projection(
                entries[item["example_id"]], kind=version.kind
            )
            != item
            for item in version.members
        ):
            raise ReviewConflictError(
                "Состав версии изменился в каталоге; создайте и оцените новую версию."
            )

    async def record_quality(
        self, *, version_id: UUID, revision: int, report: dict | None, actor: str
    ) -> ArtifactVersion:
        """Допуск/отзыв и снятие рабочего назначения сохраняются одной транзакцией."""
        async with self._sessions() as database, database.begin():
            row = await self._locked(database, version_id, revision)
            version = decode_version(row.snapshot)
            approved = (
                validate_quality_report(report, version)
                if report is not None
                else False
            )
            if approved:
                await self._require_current_members(database, version)
            await database.execute(
                delete(AppliedArtifactModel).where(
                    AppliedArtifactModel.version_id == version_id
                )
            )
            version = replace(
                version,
                quality_approved=approved,
                quality_report=report,
                revision=revision + 1,
            )
            self._save(
                database,
                row,
                version,
                actor,
                "quality_report" if report is not None else "quality_revoke",
            )
        return version
