# services/analysis-service/src/pdrd_analysis_service/infrastructure/analysis_progress.py

"""HTTP adapter progress/cancellation API Gateway."""

import logging
from dataclasses import dataclass
from uuid import UUID

import httpx

logger = logging.getLogger(
    "uvicorn.error",
)


@dataclass(frozen=True, slots=True)
class HttpAnalysisProgressProbe:
    """Best-effort cancellation probe через existing progress callback."""

    base_url: str

    request_timeout_seconds: float

    connect_timeout_seconds: float

    async def is_cancelled(
        self,
        *,
        document_id: UUID,
        stage: str,
        current: int,
        total: int,
    ) -> bool:
        """Проверяет cancellation и одновременно обновляет heartbeat."""
        url = f"{self.base_url.rstrip('/')}/internal/v1/analysis-progress/{document_id}"

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self.request_timeout_seconds,
                    connect=self.connect_timeout_seconds,
                ),
            ) as client:
                response = await client.post(
                    url,
                    json={
                        "stage": stage,
                    },
                )

                response.raise_for_status()

                payload = response.json()

        except (
            httpx.HTTPError,
            ValueError,
        ) as error:
            logger.warning(
                (
                    "analysis_stage_progress_probe_failed "
                    "document_id=%s stage=%s current=%s total=%s "
                    "error_type=%s error=%s"
                ),
                document_id,
                stage,
                current,
                total,
                type(
                    error,
                ).__name__,
                error,
            )

            # Progress API остаётся best-effort.
            # Его недоступность сама по себе не должна убивать analysis.
            return False

        cancelled = bool(
            payload.get(
                "cancelled",
                False,
            )
        )

        logger.info(
            (
                "analysis_stage_progress "
                "document_id=%s stage=%s current=%s total=%s "
                "cancelled=%s"
            ),
            document_id,
            stage,
            current,
            total,
            cancelled,
        )

        return cancelled
