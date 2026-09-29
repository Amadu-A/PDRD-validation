# services/api-gateway/src/pdrd_api_gateway/application/use_cases/manage_experience.py

"""Проверяет контекст каталога и связывает операции записи с серверным заданием."""

from dataclasses import dataclass, replace
from uuid import UUID

from pdrd_api_gateway.application.ports.experience import (
    ExperienceAccessPolicy,
    ExperienceContextProvider,
    ExperienceOperation,
    ExperienceService,
)
from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.core.observability import log_execution_time


@dataclass(frozen=True, slots=True)
class ManageExperience:
    """Все маршруты используют один контракт прав; браузер не сообщает владельца."""

    contexts: ExperienceContextProvider
    access: ExperienceAccessPolicy
    service: ExperienceService

    @log_execution_time(operation="experience_request")
    async def execute(
        self,
        *,
        operation: ExperienceOperation,
        job_id: UUID | None = None,
        example_id: UUID | None = None,
        query: dict | None = None,
        command: dict | None = None,
        index: int | None = None,
    ) -> dict | bytes:
        """При объектной операции получает задание из неизменяемого источника записи."""
        context = self.contexts.resolve(
            operation=operation, job_id=job_id, example_id=example_id
        )
        if (
            context.operation != operation
            or context.job_id != job_id
            or context.example_id != example_id
        ):
            raise ReviewRequestError(
                403, "Серверный контекст не соответствует каталогу."
            )
        await self.access.require(context)
        if example_id is not None:
            record = await self.service.execute(
                context=replace(context, operation="read"),
                query=None,
                command=None,
                index=None,
            )
            try:
                if not isinstance(record, dict) or record["id"] != str(example_id):
                    raise ValueError("Несоответствующий пример.")
                resource_job = UUID(record["job_id"])
            except (ValueError, KeyError, TypeError) as error:
                raise ReviewRequestError(
                    503, "Каталог вернул некорректный источник."
                ) from error
            context = replace(context, job_id=resource_job)
            await self.access.require(context)
            if operation == "read":
                return record
        return await self.service.execute(
            context=context, query=query, command=command, index=index
        )
