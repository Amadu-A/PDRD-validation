# services/api-gateway/tests/unit/test_artifact_dataset_access.py

"""Экспорт набора проверяет доступ ко всем зафиксированным заданиям до выдачи ZIP."""

from uuid import UUID

import pytest
from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.application.use_cases.manage_experience import ManageExperience

from .test_experience_selection_access import Access, Contexts


class Versions:
    """Источники читаются из версии, а не из текущего каталога или запроса браузера."""

    def __init__(self):
        """Два frozen примера относятся к разным заданиям."""
        self.operations = []

    async def execute(self, *, context, **options):
        """ZIP доступен только после проверки серверного record."""
        self.operations.append(context.operation)
        return (
            {
                "id": str(context.example_id),
                "members": [{"job_id": str(UUID(int=i))} for i in (11, 12)],
            }
            if context.operation == "version_read"
            else b"PK-test-archive"
        )


@pytest.mark.parametrize("denied,allowed", [(12, False), (99, True)])
async def test_dataset_export_checks_all_source_jobs_before_reading_images(
    denied, allowed
):
    """Отказ по последнему ресурсу предотвращает выдачу всего набора."""
    versions, access = Versions(), Access(UUID(int=denied))
    manager = ManageExperience(Contexts(), access, versions)
    if allowed:
        assert (
            await manager.execute(operation="version_export", example_id=UUID(int=1))
            == b"PK-test-archive"
        )
        assert versions.operations == ["version_read", "version_export"]
    else:
        with pytest.raises(ReviewRequestError) as result:
            await manager.execute(operation="version_export", example_id=UUID(int=1))
        assert result.value.status_code == 403 and versions.operations == [
            "version_read"
        ]
    assert [item.job_id for item in access.checked] == [
        None,
        UUID(int=11),
        UUID(int=12),
    ]
