# services/experience-service/src/pdrd_experience_service/application/use_cases/artifacts.py

"""Ручной запуск выбранных примеров и отдельная конфигурация рабочего анализа."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pdrd_experience_service.application.catalog_snapshot import encode
from pdrd_experience_service.application.ports.artifacts import ArtifactRepository
from pdrd_experience_service.application.ports.catalog import CatalogRepository
from pdrd_experience_service.application.use_cases.refresh_catalog_sources import (
    RefreshCatalogSources,
)
from pdrd_experience_service.core.observability import log_execution_time
from pdrd_experience_service.domain.artifact_projection import (
    artifact_member_projection,
)
from pdrd_experience_service.domain.artifact_quality import validate_quality_report
from pdrd_experience_service.domain.artifacts import (
    ArtifactVersion,
    manifest_hash,
    validate_name,
)
from pdrd_experience_service.domain.index_projection import (
    exclusion_reason,
    index_projection,
)
from pdrd_experience_service.domain.review import ReviewConflictError, ReviewError


def version_view(version: ArtifactVersion) -> dict:
    """Состав содержит ссылки и текстовые снимки; PDF и веса не лежат в SQL."""
    return encode(asdict(version))


@dataclass(frozen=True, slots=True)
class ManageArtifacts:
    """Не запускает GPU из HTTP-запроса: создаёт долговечное задание для worker."""

    repository: ArtifactRepository
    catalog: CatalogRepository
    refresh: RefreshCatalogSources | None = None

    async def list(self) -> dict:
        """Модели перечисляются из известных версий; новая модель задаётся при создании."""
        summaries = []
        for item in await self.repository.list():
            summary = version_view(item)
            summary["member_count"] = len(summary.pop("members"))
            summaries.append(summary)
        return {"items": summaries, "applied": await self.repository.applied()}

    @log_execution_time(operation="experience_version_create")
    async def create(
        self,
        *,
        kind: str,
        name: str,
        model: str,
        references: tuple[tuple[UUID, int], ...],
        actor: str,
    ) -> dict:
        """Все примеры должны относиться к одному явному разделу нормативки."""
        if kind not in {"vector", "fine_tune"} or not actor.strip():
            raise ReviewError("Неизвестный вид версии или отсутствует автор.")
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ReviewError("Укажите название базовой модели.")
        if (
            not references
            or len(references) > 1000
            or len(dict(references)) != len(references)
        ):
            raise ReviewError("Выберите от 1 до 1000 различных замечаний.")
        entries = await self.catalog.get_many(tuple(dict(references)))
        if len(entries) != len(references):
            raise LookupError("Часть выбранных замечаний не найдена.")
        expected, sections, members = dict(references), set(), []
        for entry in entries:
            if entry.example.revision != expected[entry.example.id]:
                raise ReviewConflictError("Замечания изменены; обновите выбор.")
        repaired = (
            await self.refresh.execute(entries=entries, actor=actor)
            if self.refresh
            else ()
        )
        for item in repaired:
            example_id = UUID(item["id"])
            if expected.get(example_id) == item["previous_revision"]:
                expected[example_id] = item["revision"]
        if repaired:
            entries = await self.catalog.get_many(tuple(expected))
        excluded = []
        for entry in entries:
            example = entry.example
            if example.revision != expected[example.id]:
                raise ReviewConflictError("Замечания изменены; обновите выбор.")
            projection = artifact_member_projection(entry, kind=kind)
            reason = exclusion_reason(entry)
            if not example.section_id or not example.section_title:
                reason = (
                    reason or "В исходном задании не определён раздел нормативной базы."
                )
            if reason:
                excluded.append({"id": str(example.id), "reason": reason})
                continue
            sections.add((example.section_id, example.section_title))
            members.append(projection)
        if not members:
            raise ReviewError(
                "Нет пригодных выбранных замечаний. "
                + "; ".join(f"{item['id']}: {item['reason']}" for item in excluded[:20])
            )
        if len(sections) != 1:
            raise ReviewError(
                "Создайте отдельную версию для каждого раздела нормативного документа."
            )
        section_id, section_title = sections.pop()
        frozen = tuple(sorted(members, key=lambda item: item["example_id"]))
        version = ArtifactVersion(
            id=uuid4(),
            kind=kind,
            name=validate_name(name),
            model=model.strip(),
            section_id=section_id,
            section_title=section_title,
            members=frozen,
            manifest_sha256=manifest_hash(
                kind=kind, model=model.strip(), section_id=section_id, members=frozen
            ),
            created_at=datetime.now(UTC),
            created_by=actor,
            status="queued" if kind == "vector" else "prepared",
        )
        await self.repository.create(version)
        return {**version_view(version), "excluded": excluded, "repaired": repaired}

    async def get(self, version_id: UUID) -> dict:
        """Просмотр и подсветка используют зафиксированный состав этой версии."""
        version = await self.repository.get(version_id)
        if version is None or version.deleted:
            raise LookupError("Версия не найдена.")
        return version_view(version)

    async def rename(
        self, *, version_id: UUID, revision: int, name: str, actor: str
    ) -> dict:
        """Меняет только отображаемое имя с проверкой текущей редакции."""
        return version_view(
            await self.repository.rename(
                version_id=version_id,
                revision=revision,
                name=validate_name(name),
                actor=actor,
            )
        )

    async def delete(self, *, version_id: UUID, revision: int, actor: str) -> dict:
        """Удаляет версию из доступного реестра, сохраняя историю и состав."""
        await self.repository.delete(
            version_id=version_id, revision=revision, actor=actor
        )
        return {"id": str(version_id), "deleted": True}

    async def apply(self, *, version_id: UUID, revision: int, actor: str) -> dict:
        """Записывает назначение только после серверного допуска качества."""
        return await self.repository.apply(
            version_id=version_id, revision=revision, actor=actor
        )

    async def claim(
        self, *, worker: str, model: str, identity: str, dimension: int
    ) -> dict:
        """Служебный worker получает только одну совместимую ручную задачу."""
        item = await self.repository.claim(
            worker=worker,
            model=model,
            identity=identity,
            dimension=dimension,
        )
        return {"item": version_view(item) if item else None}

    async def record_quality(
        self, *, version_id: UUID, revision: int, report: dict | None, actor: str
    ) -> dict:
        """Сохраняет отчёт или отзывает допуск; загрузка сама не применяет версию."""
        return version_view(
            await self.repository.record_quality(
                version_id=version_id, revision=revision, report=report, actor=actor
            )
        )

    async def read_applied(self, section_id: str) -> dict:
        """Выдаёт Knowledge только проверенный неизменившийся состав своего раздела."""
        references = await self.repository.applied()
        reference = next(
            (
                item
                for item in references
                if item["kind"] == "vector" and item["section_id"] == section_id
            ),
            None,
        )
        if reference is None:
            return {"item": None}
        version = await self.repository.get(UUID(reference["version_id"]))
        if version is None or version.deleted or not version.quality_approved:
            return {"item": None}
        try:
            if version.section_id != section_id or not validate_quality_report(
                version.quality_report, version
            ):
                return {"item": None}
        except ReviewError:
            return {"item": None}
        entries = await self.catalog.get_many(
            tuple(UUID(item["example_id"]) for item in version.members)
        )
        current = {str(entry.example.id): entry for entry in entries}
        if len(current) != len(version.members) or any(
            exclusion_reason(current[item["example_id"]])
            or index_projection(current[item["example_id"]]) != item
            for item in version.members
        ):
            return {"item": None}
        return {"item": version_view(version)}

    async def finish(self, *, version_id: UUID, worker: str, result: dict) -> dict:
        """Аренда и результат не дают права назначать качество версии."""
        return version_view(
            await self.repository.finish(
                version_id=version_id,
                worker=worker,
                result=result,
            )
        )
