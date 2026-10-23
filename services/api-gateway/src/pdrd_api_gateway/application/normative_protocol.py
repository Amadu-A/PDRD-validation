# services/api-gateway/src/pdrd_api_gateway/application/normative_protocol.py

"""Привязка неизменяемого аудита к настоящему job и его нормативному snapshot."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_api_gateway.application.ports.normative_protocol import (
    NormativeProtocolError,
    NormativeProtocolNotFound,
    NormativeProtocolStore,
)
from pdrd_api_gateway.application.ports.persistence import UnitOfWorkFactory
from pdrd_api_gateway.domain.analysis_job import AnalysisJobStatus


@dataclass(frozen=True, slots=True)
class ManageNormativeProtocol:
    """Публикация под существующей блокировкой job; никакого отдельного сервиса или БД."""

    unit_of_work_factory: UnitOfWorkFactory
    store: NormativeProtocolStore

    async def validate_result(self, job_id, document_id, result):
        """Не завершает job со ссылкой на чужой, частичный или отсутствующий архив."""
        for protocol_id, ref in protocol_references(result).items():
            if ref.get("document_id") != str(document_id):
                raise NormativeProtocolError(
                    "Протокол другого документа в результате анализа."
                )
            manifest = await self.read(job_id, protocol_id, "manifest", 0, 1)
            for key in (
                "protocol_id",
                "document_id",
                "job_id",
                "counts",
                "bytes",
                "section_id",
                "document_ids",
                "state",
                "diagnostics_digest",
                "stage_stream",
            ):
                if manifest.get(key) != ref.get(key):
                    raise NormativeProtocolError(
                        "Ссылка результата не совпадает с опубликованным протоколом."
                    )
            await self.store.verify(document_id, protocol_id)

    async def publish(self, document_id, protocol_id, manifest):
        """Проверяет snapshot и отмену до появления долговечной ссылки."""
        async with self.unit_of_work_factory() as unit:
            job = await unit.analysis_jobs.get_by_document_id_for_update(document_id)
            if job is None:
                raise NormativeProtocolNotFound("Задание документа не найдено.")
            if job.status == AnalysisJobStatus.CANCELLED:
                raise NormativeProtocolError("analysis_cancelled")
            snapshot = job.normative_snapshot
            if (
                snapshot is None
                or str(snapshot.section_id) != manifest["section_id"]
                or sorted(map(str, snapshot.document_ids)) != manifest["document_ids"]
            ):
                raise NormativeProtocolError(
                    "Нормативный scope не совпадает с snapshot задания."
                )
            result = await self.store.publish(
                document_id, protocol_id, {**manifest, "job_id": str(job.id)}
            )
            return result

    async def read(self, job_id, protocol_id, collection, offset, limit):
        """Принимает job_id только снаружи; document_id получает из репозитория."""
        async with self.unit_of_work_factory() as unit:
            job = await unit.analysis_jobs.get(job_id)
            if job is None or job.document_id is None:
                raise NormativeProtocolNotFound("Задание не найдено.")
        manifest = await self.store.read(job.document_id, protocol_id, "manifest", 0, 1)
        if manifest["job_id"] != str(job_id):
            raise NormativeProtocolNotFound("Протокол другого задания.")
        return await self.store.read(
            job.document_id, protocol_id, collection, offset, limit
        )

    async def hydrate_metrics(self, job_id, result):
        """Восстанавливает планы только для диагностики, не добавляет их в публичный result."""
        references = protocol_references(result)
        restored = []
        for protocol_id in references:
            manifest = await self.read(job_id, protocol_id, "manifest", 0, 1)
            plan = await self.store.diagnostic_plan(
                UUID(manifest["document_id"]), protocol_id
            )
            restored.append({"protocol_id": protocol_id, "requirement_plan": plan})
        return {**result, "protocol_diagnostics": restored}


def protocol_references(result):
    """Извлекает только доверенные позиции контракта; одинаковый ID не допускает разных scope."""
    references = {}

    def collect(value):
        """Не исследует свободный текст и исходные proof records."""
        if isinstance(value, dict):
            plan = value.get("requirement_plan")
            if isinstance(plan, dict) and plan.get("protocol"):
                ref = plan["protocol"]
                key = ref["protocol_id"]
                if key in references and references[key] != ref:
                    raise NormativeProtocolError(
                        "Разные ссылки для одного ID протокола."
                    )
                references[key] = ref
            for key in (
                "pages",
                "page",
                "metrics",
                "normative_check",
                "independent_normative",
                "finalization",
            ):
                if key in value:
                    collect(value[key])
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(result)
    return references
