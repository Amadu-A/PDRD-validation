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
        return await self._post(
            document_id=document_id, stage=stage, current=current, total=total
        )

    async def report_gpu_state(
        self,
        *,
        document_id: UUID,
        stage: str,
        waiting: int,
        running: int,
    ) -> bool:
        """Сохраняет счётчики очереди модели независимо от основного этапа."""
        return await self._post(
            document_id=document_id,
            stage=stage,
            current=0,
            total=0,
            gpu={"waiting": waiting, "running": running},
        )

    async def _post(
        self,
        *,
        document_id: UUID,
        stage: str,
        current: int,
        total: int,
        gpu: dict[str, int] | None = None,
    ) -> bool:
        """Передаёт best-effort heartbeat, возвращая durable сигнал отмены."""
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
                        **({"gpu": gpu} if gpu is not None else {}),
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
