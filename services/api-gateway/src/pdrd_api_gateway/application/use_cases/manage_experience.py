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
        actor: str | None = None,
    ) -> dict | bytes:
        """При объектной операции получает задание из неизменяемого источника записи."""
        context = self.contexts.resolve(
            operation=operation, job_id=job_id, example_id=example_id
        )
        if actor is not None:
            context = replace(context, actor=actor)
        if (
            context.operation != operation
            or context.job_id != job_id
            or context.example_id != example_id
        ):
            raise ReviewRequestError(
                403, "Серверный контекст не соответствует каталогу."
            )
        await self.access.require(context)
        if operation in {"delete_selection", "version_create"}:
            # Права проверяются на каждое серверное задание до единой записи набора.
            items = (command or {}).get("items", [])
            selection = await self.service.execute(
                context=replace(context, operation="selection_read"),
                query=None,
                command={"items": items},
                index=None,
            )
            try:
                rows = selection["items"]
                if {row["id"] for row in rows} != {item["id"] for item in items}:
                    raise ValueError("Несоответствующий выбор.")
                jobs = tuple(dict.fromkeys(UUID(row["job_id"]) for row in rows))
            except (KeyError, TypeError, ValueError) as error:
                raise ReviewRequestError(
                    503, "Каталог вернул некорректный выбор."
                ) from error
            for resource_job in jobs:
                await self.access.require(replace(context, job_id=resource_job))
        if example_id is not None and operation.startswith("version_"):
            record = await self.service.execute(
                context=replace(context, operation="version_read"),
                query=None,
                command=None,
                index=None,
            )
            if (
                not isinstance(record, dict)
                or record.get("id") != str(example_id)
                or not isinstance(record.get("members"), list)
            ):
                raise ReviewRequestError(
                    503, "Реестр вернул некорректный состав версии."
                )
            try:
                jobs = tuple(
                    dict.fromkeys(UUID(item["job_id"]) for item in record["members"])
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ReviewRequestError(
                    503, "Реестр вернул некорректный источник версии."
                ) from error
            for resource_job in jobs:
                await self.access.require(replace(context, job_id=resource_job))
            if operation == "version_read":
                return record
        elif example_id is not None:
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
