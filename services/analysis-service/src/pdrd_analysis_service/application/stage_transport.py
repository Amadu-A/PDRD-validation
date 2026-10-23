# services/analysis-service/src/pdrd_analysis_service/application/stage_transport.py

"""Передача больших обязательных findings через тот же lossless-архив, без усечения."""

import asyncio

from pdrd_analysis_service.application.ports.normative_protocol import (
    NormativeProtocolUnavailable,
)
from pdrd_analysis_service.domain.normative_protocol import protocol_archive

STAGE_RESPONSE_BYTES = 8 * 1024 * 1024


async def archive_stage_items(
    *, items, document_id, scope, writer, check_cancellation, metadata=None
):
    """Сохраняет заголовки страниц и каждую находку отдельной записью для ограниченного чтения."""
    if writer is None or scope is None:
        raise NormativeProtocolUnavailable(
            "Большой результат требует хранилище и нормативный snapshot."
        )

    def prepare():
        """Не блокирует event loop канонизацией proof records."""
        rows = [{"type": "metadata", "value": metadata}] if metadata else []
        for item in items:
            result = item["result"]
            rows.append(
                {
                    "type": "page",
                    "page_number": item["page_number"],
                    "result": {**result, "findings": []},
                }
            )
            rows.extend(
                {
                    "type": "finding",
                    "page_number": item["page_number"],
                    "finding": finding,
                }
                for finding in result.get("findings", [])
            )
        return protocol_archive(
            document_id=str(document_id),
            section_id=str(scope.section_id),
            document_ids=tuple(map(str, scope.document_ids)),
            diagnostics={},
            sources=(),
            stage_records=rows,
        )

    archive = await asyncio.to_thread(prepare)
    reference = await writer.save(
        archive=archive, check_cancellation=check_cancellation
    )
    await check_cancellation()
    return {**reference, "pages": len(items), "encoding": "stage-records-v1"}
