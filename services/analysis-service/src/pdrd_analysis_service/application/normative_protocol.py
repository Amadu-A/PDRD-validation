# services/analysis-service/src/pdrd_analysis_service/application/normative_protocol.py

"""Публикация полного аудита перед формированием компактного ответа страницы/стадии."""

import asyncio

from pdrd_analysis_service.application.ports.normative_protocol import (
    NormativeProtocolUnavailable,
)
from pdrd_analysis_service.domain.normative_protocol import (
    protocol_archive,
    transport_summary,
)


async def publish_independent(
    *, writer, result, document_id, scope, check_cancellation, page_metrics=()
):
    """Не выдаёт успешную сводку без проверенной ссылки на долговечный полный протокол."""
    if writer is None:
        raise NormativeProtocolUnavailable(
            "Хранилище полного протокола N-first не настроено."
        )
    archive = await asyncio.to_thread(
        protocol_archive,
        document_id=str(document_id),
        section_id=str(scope.section_id),
        document_ids=tuple(map(str, scope.document_ids)),
        diagnostics=result.diagnostics,
        sources=result.sources,
        page_metrics=page_metrics,
    )
    reference = await writer.save(
        archive=archive, check_cancellation=check_cancellation
    )
    await check_cancellation()
    return {"requirement_plan": {**result.summary, "protocol": reference}}


def compact_page_metrics(metrics, reference):
    """Сохраняет page-local N/T/U-счётчики; полный план уже находится в том же архиве."""
    if isinstance(metrics.get("requirement_plan"), dict):
        metrics["requirement_plan"] = {
            **transport_summary(metrics["requirement_plan"]),
            "protocol": reference,
        }
