# services/api-gateway/tests/unit/test_experience_selection_access.py

"""Будущая политика прав проверяет все задания выбранного набора до единой записи."""

from uuid import UUID

import pytest
from pdrd_api_gateway.application.ports.experience import ExperienceContext
from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.application.use_cases.manage_experience import ManageExperience


class Contexts:
    """Субъект задаётся сервером, независимо от команд браузера."""

    def resolve(self, **options):
        """Сохраняет операцию и ресурс для проверки сценария."""
        return ExperienceContext(actor="engineer:server", **options)


class Access:
    """Запрещает ровно одно задание, остальные разрешает."""

    def __init__(self, denied):
        """Хранит запрет и наблюдаемые контексты."""
        self.denied, self.checked = denied, []

    async def require(self, context):
        """Проверяет назначенный сервером job перед изменением набора."""
        self.checked.append(context)
        if context.job_id == self.denied:
            raise ReviewRequestError(403, "Нет прав на выбранное задание.")


class Service:
    """Выдаёт серверное происхождение выбранных ID; запись наблюдаема отдельно."""

    def __init__(self):
        """Два примера принадлежат разным заданиям."""
        self.items = [
            {"id": str(UUID(int=i)), "job_id": str(UUID(int=10 + i))} for i in (1, 2)
        ]
        self.operations = []

    async def execute(self, *, context, **options):
        """Один пакетный read заменяет отдельный HTTP-запрос для каждого примера."""
        self.operations.append(context.operation)
        return (
            {"items": self.items}
            if context.operation == "selection_read"
            else {"ok": True}
        )


@pytest.mark.parametrize("operation", ["delete_selection", "version_create"])
async def test_denied_second_job_prevents_entire_batch_mutation(operation):
    """Отказ в одном ресурсе не оставляет частично изменённый набор."""
    service, access = Service(), Access(UUID(int=12))
    with pytest.raises(ReviewRequestError) as result:
        await ManageExperience(Contexts(), access, service).execute(
            operation=operation,
            command={
                "items": [{"id": item["id"], "revision": 0} for item in service.items]
            },
        )
    assert result.value.status_code == 403
    assert service.operations == ["selection_read"]
    assert [item.job_id for item in access.checked] == [
        None,
        UUID(int=11),
        UUID(int=12),
    ]
    assert all(item.actor == "engineer:server" for item in access.checked)


async def test_allowed_batch_reads_sources_once_then_writes_once():
    """Серверные ресурсы проверены до одного запроса изменения."""
    service, access = Service(), Access(UUID(int=99))
    assert await ManageExperience(Contexts(), access, service).execute(
        operation="delete_selection",
        command={
            "items": [{"id": item["id"], "revision": 0} for item in service.items]
        },
    ) == {"ok": True}
    assert service.operations == ["selection_read", "delete_selection"]
