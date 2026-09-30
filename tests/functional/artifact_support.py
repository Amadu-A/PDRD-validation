# tests/functional/artifact_support.py

"""Тестовый порт реестра для HTTP-контура; настоящие блокировки проверяются PostgreSQL."""

from dataclasses import replace

from pdrd_experience_service.domain.artifact_quality import validate_quality_report
from pdrd_experience_service.domain.review import ReviewConflictError, ReviewError


class MemoryArtifacts:
    """Наблюдаемое состояние manifest и аренд в тесте маршрутов."""

    def __init__(self):
        """Начинает с пустого реестра и без аренд."""
        self.rows, self.owners = {}, {}
        self.assignments = {}

    async def create(self, version):
        """Сохраняет серверный manifest."""
        self.rows[version.id] = version

    async def list(self):
        """Исключает удалённые версии из селекторов."""
        return tuple(item for item in self.rows.values() if not item.deleted)

    async def applied(self):
        """Назначение независимо от списка и просмотра версий."""
        return tuple(self.assignments.values())

    async def get(self, version_id):
        """Возвращает фиксированный состав по идентификатору."""
        return self.rows.get(version_id)

    async def rename(self, *, version_id, revision, name, actor):
        """Проверяет ожидаемую редакцию перед изменением имени."""
        item = self.rows[version_id]
        if item.revision != revision:
            raise ReviewConflictError("Stale version.")
        self.rows[version_id] = replace(item, name=name, revision=revision + 1)
        return self.rows[version_id]

    async def delete(self, *, version_id, revision, actor):
        """Удалённый manifest остаётся в истории."""
        item = self.rows[version_id]
        if item.revision != revision or item.status == "building":
            raise ReviewConflictError("Stale version.")
        self.rows[version_id] = replace(item, deleted=True, revision=revision + 1)

    async def apply(self, *, version_id, revision, actor):
        """Применяет только точную проверенную редакцию."""
        item = self.rows[version_id]
        if item.revision != revision:
            raise ReviewConflictError("Stale version.")
        if (
            item.deleted
            or not item.quality_approved
            or not validate_quality_report(item.quality_report, item)
        ):
            raise ReviewError("Нужна оценка качества.")
        assignment = {
            "kind": item.kind,
            "section_id": item.section_id,
            "version_id": str(version_id),
            "actor": actor,
        }
        self.assignments[(item.kind, item.section_id)] = assignment
        self.rows[version_id] = replace(item, revision=revision + 1)
        return assignment

    async def record_quality(self, *, version_id, revision, report, actor):
        """CAS и допуск/отзыв соответствуют контракту production-порта."""
        item = self.rows[version_id]
        if item.deleted:
            raise LookupError("Version missing.")
        if item.revision != revision:
            raise ReviewConflictError("Stale version.")
        approved = (
            validate_quality_report(report, item) if report is not None else False
        )
        key = (item.kind, item.section_id)
        if self.assignments.get(key, {}).get("version_id") == str(version_id):
            del self.assignments[key]
        self.rows[version_id] = replace(
            item,
            quality_report=report,
            quality_approved=approved,
            revision=revision + 1,
        )
        return self.rows[version_id]

    async def claim(self, *, worker, model, identity, dimension):
        """Выдаёт только queued версию подходящей модели."""
        for item in self.rows.values():
            if (
                item.kind == "vector"
                and item.status == "queued"
                and item.model == model
                and not item.deleted
            ):
                current = replace(
                    item,
                    status="building",
                    revision=item.revision + 1,
                    collection=f"pdrd_e_{identity}_{item.id.hex}",
                    embedding_identity=identity,
                    dimension=dimension,
                )
                self.rows[item.id], self.owners[item.id] = current, worker
                return current
        return None

    async def finish(self, *, version_id, worker, result):
        """Владелец аренды завершает свою задачу."""
        if self.owners.get(version_id) != worker:
            raise ReviewConflictError("Wrong lease.")
        item = self.rows[version_id]
        self.rows[version_id] = replace(
            item,
            status=result["status"],
            error=result.get("error", ""),
            revision=item.revision + 1,
        )
        return self.rows[version_id]
