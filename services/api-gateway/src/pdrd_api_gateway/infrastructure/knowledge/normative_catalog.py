# services/api-gateway/src/pdrd_api_gateway/infrastructure/knowledge/normative_catalog.py

"""HTTP adapter чтения managed normative catalog."""

from typing import Any
from uuid import UUID

import httpx

from pdrd_api_gateway.application.ports.normative_catalog import (
    NormativeCatalogNotFoundError,
    NormativeCatalogReadError,
    NormativeDocumentRecord,
    NormativeSectionRecord,
)
from pdrd_api_gateway.core.settings import (
    KnowledgeServiceSettings,
)


class HttpNormativeCatalogReader:
    """Читает normative catalog через internal Knowledge HTTP API."""

    def __init__(
        self,
        *,
        settings: KnowledgeServiceSettings,
    ) -> None:
        """Сохраняет параметры Knowledge Service."""
        self._settings = settings

    async def get_section(
        self,
        *,
        section_id: UUID,
    ) -> NormativeSectionRecord:
        """Возвращает section и его exact DB system prompt."""
        payload = await self._get_json(
            path=f"/internal/v1/normative/sections/{section_id}",
        )

        if not isinstance(
            payload,
            dict,
        ):
            raise NormativeCatalogReadError(
                "Knowledge Service вернул некорректный section payload.",
            )

        try:
            payload_section_id = UUID(str(payload["section_id"]))

            system_prompt = payload["system_prompt"]

        except (
            KeyError,
            ValueError,
        ) as error:
            raise NormativeCatalogReadError(
                "Knowledge Service вернул неполный section payload.",
            ) from error

        if not isinstance(
            system_prompt,
            str,
        ):
            raise NormativeCatalogReadError(
                "Knowledge Service вернул некорректный system_prompt.",
            )

        return NormativeSectionRecord(
            section_id=payload_section_id,
            system_prompt=system_prompt,
        )

    async def list_documents(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeDocumentRecord,
        ...,
    ]:
        """Возвращает managed documents section."""
        payload = await self._get_json(
            path=(f"/internal/v1/normative/sections/{section_id}/documents"),
        )

        if not isinstance(
            payload,
            list,
        ):
            raise NormativeCatalogReadError(
                "Knowledge Service вернул некорректный documents payload.",
            )

        records: list[NormativeDocumentRecord] = []

        for item in payload:
            if not isinstance(
                item,
                dict,
            ):
                raise NormativeCatalogReadError(
                    "Knowledge Service вернул некорректный document item.",
                )

            try:
                document_id = UUID(str(item["document_id"]))

                item_section_id = UUID(str(item["section_id"]))

                ready_for_analysis = item["ready_for_analysis"]

            except (
                KeyError,
                ValueError,
            ) as error:
                raise NormativeCatalogReadError(
                    "Knowledge Service вернул неполный document payload.",
                ) from error

            if not isinstance(
                ready_for_analysis,
                bool,
            ):
                raise NormativeCatalogReadError(
                    "Knowledge Service вернул некорректный ready_for_analysis.",
                )

            records.append(
                NormativeDocumentRecord(
                    document_id=document_id,
                    section_id=item_section_id,
                    ready_for_analysis=ready_for_analysis,
                )
            )

        return tuple(
            records,
        )

    async def _get_json(
        self,
        *,
        path: str,
    ) -> Any:
        """Выполняет один internal GET к Knowledge Service."""
        base_url = self._settings.base_url.rstrip(
            "/",
        )

        timeout = httpx.Timeout(
            timeout=self._settings.request_timeout_seconds,
            connect=self._settings.connect_timeout_seconds,
        )

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
            ) as client:
                response = await client.get(
                    base_url + path,
                )

        except httpx.HTTPError as error:
            raise NormativeCatalogReadError(
                "Не удалось обратиться к Knowledge Service: "
                f"{type(error).__name__}: {error}",
            ) from error

        if response.status_code == 404:
            raise NormativeCatalogNotFoundError(
                "Запрошенная сущность нормативного каталога не найдена.",
            )

        try:
            response.raise_for_status()

        except httpx.HTTPStatusError as error:
            raise NormativeCatalogReadError(
                "Knowledge Service вернул HTTP "
                f"{response.status_code}: {response.text[:1000]}",
            ) from error

        try:
            return response.json()

        except ValueError as error:
            raise NormativeCatalogReadError(
                "Knowledge Service вернул невалидный JSON.",
            ) from error
