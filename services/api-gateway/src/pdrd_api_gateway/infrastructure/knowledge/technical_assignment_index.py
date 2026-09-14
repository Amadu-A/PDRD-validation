# services/api-gateway/src/pdrd_api_gateway/infrastructure/knowledge/technical_assignment_index.py

"""Knowledge Service adapter T indexing lifecycle."""

import asyncio
from time import monotonic
from uuid import UUID

import httpx

from pdrd_api_gateway.application.ports.technical_assignment_index import (
    TechnicalAssignmentIndexError,
    TechnicalAssignmentIndexState,
)
from pdrd_api_gateway.domain.technical_assignment import (
    TechnicalAssignmentSnapshot,
)

_ALLOWED_INDEX_STATUSES = frozenset(
    {
        "uploaded",
        "queued",
        "indexing",
        "ready",
        "failed",
    }
)


class KnowledgeTechnicalAssignmentIndexCoordinator:
    """Регистрирует ТЗ, читает status и ждёт READY."""

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

    async def register(
        self,
        *,
        snapshot: TechnicalAssignmentSnapshot,
        content: bytes,
    ) -> TechnicalAssignmentIndexState:
        """Регистрирует ТЗ одним HTTP-вызовом."""
        async with self._build_client() as client:
            try:
                response = await self._post_registration(
                    client=client,
                    snapshot=snapshot,
                    content=content,
                )

            except httpx.HTTPError as error:
                raise TechnicalAssignmentIndexError(
                    "Knowledge Service недоступен при регистрации ТЗ.",
                ) from error

            self._raise_registration_error(
                response,
            )

            return self._parse_state(
                response,
            )

    async def get_status(
        self,
        *,
        technical_assignment_id: UUID,
    ) -> TechnicalAssignmentIndexState:
        """Возвращает один snapshot indexing lifecycle."""
        async with self._build_client() as client:
            try:
                response = await client.get(
                    self._status_url(
                        technical_assignment_id,
                    )
                )

            except httpx.HTTPError as error:
                raise TechnicalAssignmentIndexError(
                    "Knowledge Service недоступен при чтении статуса ТЗ.",
                ) from error

            try:
                response.raise_for_status()

            except httpx.HTTPStatusError as error:
                raise TechnicalAssignmentIndexError(
                    "Knowledge Service не может вернуть "
                    "status ТЗ: "
                    f"{response.status_code}: "
                    f"{response.text[:1000]}",
                ) from error

            return self._parse_state(
                response,
            )

    async def ensure_ready(
        self,
        *,
        snapshot: TechnicalAssignmentSnapshot,
        content: bytes,
    ) -> None:
        """Register idempotently and block Analysis until READY."""
        deadline = monotonic() + self._wait_timeout_seconds

        async with self._build_client() as client:
            initial_state = await self._register_until_available(
                client=client,
                snapshot=snapshot,
                content=content,
                deadline=deadline,
            )

            self._raise_failed_state(
                initial_state,
            )

            if initial_state.index_status == "ready":
                return

            while True:
                if monotonic() >= deadline:
                    raise TechnicalAssignmentIndexError(
                        "Истёк timeout ожидания индексации ТЗ.",
                    )

                try:
                    response = await client.get(
                        self._status_url(
                            snapshot.technical_assignment_id,
                        )
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

                state = self._parse_state(
                    response,
                )

                if state.index_status == "ready":
                    return

                self._raise_failed_state(
                    state,
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
    ) -> TechnicalAssignmentIndexState:
        """Retry регистрации при временной недоступности Knowledge."""
        while True:
            if monotonic() >= deadline:
                raise TechnicalAssignmentIndexError(
                    "Истёк timeout регистрации ТЗ.",
                )

            try:
                response = await self._post_registration(
                    client=client,
                    snapshot=snapshot,
                    content=content,
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

            self._raise_registration_error(
                response,
            )

            return self._parse_state(
                response,
            )

    async def _post_registration(
        self,
        *,
        client: httpx.AsyncClient,
        snapshot: TechnicalAssignmentSnapshot,
        content: bytes,
    ) -> httpx.Response:
        """Отправляет immutable T snapshot в Knowledge."""
        return await client.post(
            (f"{self._base_url}/internal/v1/technical-assignments"),
            data={
                "technical_assignment_id": str(
                    snapshot.technical_assignment_id,
                ),
                "analysis_document_id": str(
                    snapshot.analysis_document_id,
                ),
                "section_id": str(
                    snapshot.section_id,
                ),
                "source_file": snapshot.source_file,
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

    def _build_client(
        self,
    ) -> httpx.AsyncClient:
        """Создаёт bounded internal HTTP client."""
        return httpx.AsyncClient(
            timeout=httpx.Timeout(
                self._request_timeout_seconds,
                connect=self._connect_timeout_seconds,
            ),
        )

    def _status_url(
        self,
        technical_assignment_id: UUID,
    ) -> str:
        """Строит internal lifecycle URL."""
        return (
            f"{self._base_url}"
            "/internal/v1/technical-assignments/"
            f"{technical_assignment_id}"
        )

    @staticmethod
    def _raise_registration_error(
        response: httpx.Response,
    ) -> None:
        """Преобразует rejected registration в application error."""
        try:
            response.raise_for_status()

        except httpx.HTTPStatusError as error:
            raise TechnicalAssignmentIndexError(
                "Knowledge Service отклонил ТЗ: "
                f"{response.status_code}: "
                f"{response.text[:1000]}",
            ) from error

    @staticmethod
    def _parse_state(
        response: httpx.Response,
    ) -> TechnicalAssignmentIndexState:
        """Преобразует Knowledge JSON в immutable state."""
        try:
            payload = response.json()

            technical_assignment_id = UUID(str(payload["technical_assignment_id"]))

            index_status = str(payload["index_status"])

            raw_error = payload.get(
                "index_error",
            )

        except (
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise TechnicalAssignmentIndexError(
                "Knowledge Service вернул некорректный lifecycle ТЗ.",
            ) from error

        if index_status not in _ALLOWED_INDEX_STATUSES:
            raise TechnicalAssignmentIndexError(
                f"Knowledge Service вернул неизвестный status ТЗ: {index_status}.",
            )

        index_error = (
            str(
                raw_error,
            )
            if raw_error is not None
            else None
        )

        return TechnicalAssignmentIndexState(
            technical_assignment_id=technical_assignment_id,
            index_status=index_status,
            index_error=index_error,
        )

    @staticmethod
    def _raise_failed_state(
        state: TechnicalAssignmentIndexState,
    ) -> None:
        """Останавливает lifecycle при terminal failure."""
        if state.index_status != "failed":
            return

        raise TechnicalAssignmentIndexError(
            "Индексация ТЗ завершилась ошибкой: "
            f"{state.index_error or 'причина не указана'}",
        )
