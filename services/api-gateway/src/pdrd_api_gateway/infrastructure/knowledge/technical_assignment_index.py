# services/api-gateway/src/pdrd_api_gateway/infrastructure/knowledge/technical_assignment_index.py

"""Knowledge Service adapter T indexing barrier."""

import asyncio
from time import monotonic

import httpx

from pdrd_api_gateway.application.ports.technical_assignment_index import (
    TechnicalAssignmentIndexError,
)
from pdrd_api_gateway.domain.technical_assignment import (
    TechnicalAssignmentSnapshot,
)


class KnowledgeTechnicalAssignmentIndexCoordinator:
    """Регистрирует ТЗ и ждёт READY lifecycle."""

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: float,
        connect_timeout_seconds: float,
        wait_timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> None:
        """Сохраняет bounded HTTP/poll settings."""
        self._base_url = base_url.rstrip(
            "/",
        )

        self._request_timeout_seconds = request_timeout_seconds

        self._connect_timeout_seconds = connect_timeout_seconds

        self._wait_timeout_seconds = wait_timeout_seconds

        self._poll_interval_seconds = poll_interval_seconds

    async def ensure_ready(
        self,
        *,
        snapshot: TechnicalAssignmentSnapshot,
        content: bytes,
    ) -> None:
        """Register idempotently and block Analysis until READY."""
        deadline = monotonic() + self._wait_timeout_seconds

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                self._request_timeout_seconds,
                connect=(self._connect_timeout_seconds),
            ),
        ) as client:
            await self._register_until_available(
                client=client,
                snapshot=snapshot,
                content=content,
                deadline=deadline,
            )

            while True:
                if monotonic() >= deadline:
                    raise TechnicalAssignmentIndexError(
                        "Истёк timeout ожидания индексации ТЗ.",
                    )

                try:
                    response = await client.get(
                        f"{self._base_url}"
                        "/internal/v1/"
                        "technical-assignments/"
                        f"{snapshot.technical_assignment_id}"
                    )

                except httpx.HTTPError:
                    await asyncio.sleep(
                        self._poll_interval_seconds,
                    )

                    continue

                if response.status_code >= 500:
                    await asyncio.sleep(
                        self._poll_interval_seconds,
                    )

                    continue

                try:
                    response.raise_for_status()

                except httpx.HTTPStatusError as error:
                    raise TechnicalAssignmentIndexError(
                        "Knowledge Service не может вернуть "
                        "status ТЗ: "
                        f"{response.status_code}: "
                        f"{response.text[:1000]}",
                    ) from error

                payload = response.json()

                index_status = str(
                    payload.get(
                        "index_status",
                        "",
                    )
                )

                if index_status == "ready":
                    return

                if index_status == "failed":
                    raise TechnicalAssignmentIndexError(
                        "Индексация ТЗ завершилась ошибкой: "
                        f"{payload.get('index_error')}",
                    )

                await asyncio.sleep(
                    self._poll_interval_seconds,
                )

    async def _register_until_available(
        self,
        *,
        client: httpx.AsyncClient,
        snapshot: TechnicalAssignmentSnapshot,
        content: bytes,
        deadline: float,
    ) -> None:
        """Retry регистрации при временной недоступности Knowledge."""
        while True:
            if monotonic() >= deadline:
                raise TechnicalAssignmentIndexError(
                    "Истёк timeout регистрации ТЗ.",
                )

            try:
                response = await client.post(
                    (f"{self._base_url}/internal/v1/technical-assignments"),
                    data={
                        "technical_assignment_id": str(
                            snapshot.technical_assignment_id
                        ),
                        "analysis_document_id": str(snapshot.analysis_document_id),
                        "section_id": str(snapshot.section_id),
                        "source_file": (snapshot.source_file),
                        "sha256": snapshot.sha256,
                    },
                    files={
                        "file": (
                            snapshot.source_file,
                            content,
                            snapshot.mime_type,
                        ),
                    },
                )

            except httpx.HTTPError:
                await asyncio.sleep(
                    self._poll_interval_seconds,
                )

                continue

            if response.status_code >= 500:
                await asyncio.sleep(
                    self._poll_interval_seconds,
                )

                continue

            try:
                response.raise_for_status()

            except httpx.HTTPStatusError as error:
                raise TechnicalAssignmentIndexError(
                    "Knowledge Service отклонил ТЗ: "
                    f"{response.status_code}: "
                    f"{response.text[:1000]}",
                ) from error

            return
