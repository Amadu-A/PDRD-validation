# services/api-gateway/src/pdrd_api_gateway/infrastructure/knowledge/normative_catalog_management.py

"""HTTP adapter полного управления managed normative catalog."""

import json
from collections.abc import Mapping
from datetime import datetime
from typing import cast
from uuid import UUID

import httpx

from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeCatalogConflictError,
    NormativeCatalogNotFoundError,
    NormativeCatalogProtocolError,
    NormativeCatalogUnavailableError,
    NormativeCatalogValidationError,
    NormativeCategoryView,
    NormativeDocumentContent,
    NormativeDocumentView,
    NormativeIndexingStatus,
    NormativeSectionView,
)
from pdrd_api_gateway.core.settings import (
    KnowledgeServiceSettings,
)

_INDEXING_STATUSES = {
    "uploaded",
    "queued",
    "indexing",
    "ready",
    "failed",
    "deleting",
}


class HttpNormativeCatalogManager:
    """Управляет normative catalog через internal Knowledge HTTP API."""

    def __init__(
        self,
        *,
        settings: KnowledgeServiceSettings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Сохраняет настройки и optional test transport."""
        self._settings = settings
        self._transport = transport

    async def list_sections(
        self,
    ) -> tuple[
        NormativeSectionView,
        ...,
    ]:
        """Возвращает все sections."""
        payload = await self._request_json(
            method="GET",
            path="/internal/v1/normative/sections",
        )

        if not isinstance(
            payload,
            list,
        ):
            raise NormativeCatalogProtocolError(
                "Knowledge Service вернул некорректный список sections.",
            )

        return tuple(
            self._parse_section(
                item,
            )
            for item in payload
        )

    async def create_section(
        self,
        *,
        name: str,
    ) -> NormativeSectionView:
        """Создаёт section."""
        payload = await self._request_json(
            method="POST",
            path="/internal/v1/normative/sections",
            json_body={
                "name": name,
            },
        )

        return self._parse_section(
            payload,
        )

    async def get_section(
        self,
        *,
        section_id: UUID,
    ) -> NormativeSectionView:
        """Возвращает section."""
        payload = await self._request_json(
            method="GET",
            path=f"/internal/v1/normative/sections/{section_id}",
        )

        return self._parse_section(
            payload,
        )

    async def update_section(
        self,
        *,
        section_id: UUID,
        changes: Mapping[
            str,
            object,
        ],
    ) -> NormativeSectionView:
        """Изменяет section."""
        payload = await self._request_json(
            method="PATCH",
            path=f"/internal/v1/normative/sections/{section_id}",
            json_body=self._encode_mapping(
                changes,
            ),
        )

        return self._parse_section(
            payload,
        )

    async def delete_section(
        self,
        *,
        section_id: UUID,
    ) -> UUID:
        """Удаляет section."""
        payload = await self._request_json(
            method="DELETE",
            path=f"/internal/v1/normative/sections/{section_id}",
        )

        return self._parse_deleted_id(
            payload,
            field_name="section_id",
        )

    async def list_categories(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeCategoryView,
        ...,
    ]:
        """Возвращает категории section."""
        payload = await self._request_json(
            method="GET",
            path=(f"/internal/v1/normative/sections/{section_id}/categories"),
        )

        if not isinstance(
            payload,
            list,
        ):
            raise NormativeCatalogProtocolError(
                "Knowledge Service вернул некорректный список categories.",
            )

        return tuple(
            self._parse_category(
                item,
            )
            for item in payload
        )

    async def create_category(
        self,
        *,
        section_id: UUID,
        name: str,
        parent_id: UUID | None,
    ) -> NormativeCategoryView:
        """Создаёт category."""
        payload = await self._request_json(
            method="POST",
            path=(f"/internal/v1/normative/sections/{section_id}/categories"),
            json_body={
                "name": name,
                "parent_id": (
                    str(
                        parent_id,
                    )
                    if parent_id is not None
                    else None
                ),
            },
        )

        return self._parse_category(
            payload,
        )

    async def get_category(
        self,
        *,
        category_id: UUID,
    ) -> NormativeCategoryView:
        """Возвращает category."""
        payload = await self._request_json(
            method="GET",
            path=f"/internal/v1/normative/categories/{category_id}",
        )

        return self._parse_category(
            payload,
        )

    async def update_category(
        self,
        *,
        category_id: UUID,
        changes: Mapping[
            str,
            object,
        ],
    ) -> NormativeCategoryView:
        """Изменяет category."""
        payload = await self._request_json(
            method="PATCH",
            path=f"/internal/v1/normative/categories/{category_id}",
            json_body=self._encode_mapping(
                changes,
            ),
        )

        return self._parse_category(
            payload,
        )

    async def delete_category(
        self,
        *,
        category_id: UUID,
    ) -> UUID:
        """Удаляет category."""
        payload = await self._request_json(
            method="DELETE",
            path=f"/internal/v1/normative/categories/{category_id}",
        )

        return self._parse_deleted_id(
            payload,
            field_name="category_id",
        )

    async def list_documents(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeDocumentView,
        ...,
    ]:
        """Возвращает documents section."""
        payload = await self._request_json(
            method="GET",
            path=(f"/internal/v1/normative/sections/{section_id}/documents"),
        )

        if not isinstance(
            payload,
            list,
        ):
            raise NormativeCatalogProtocolError(
                "Knowledge Service вернул некорректный список documents.",
            )

        return tuple(
            self._parse_document(
                item,
            )
            for item in payload
        )

    async def upload_document(
        self,
        *,
        section_id: UUID,
        category_id: UUID | None,
        original_name: str,
        content: bytes,
        content_type: str,
    ) -> NormativeDocumentView:
        """Пересылает multipart PDF в Knowledge Service."""
        data: dict[
            str,
            str,
        ] = {}

        if category_id is not None:
            data["category_id"] = str(
                category_id,
            )

        response = await self._request(
            method="POST",
            path=(f"/internal/v1/normative/sections/{section_id}/documents"),
            data=data,
            files={
                "file": (
                    original_name,
                    content,
                    content_type,
                ),
            },
        )

        return self._parse_document(
            self._response_json(
                response,
            )
        )

    async def get_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Возвращает document metadata."""
        payload = await self._request_json(
            method="GET",
            path=f"/internal/v1/normative/documents/{document_id}",
        )

        return self._parse_document(
            payload,
        )

    async def move_document(
        self,
        *,
        document_id: UUID,
        category_id: UUID | None,
    ) -> NormativeDocumentView:
        """Перемещает document."""
        payload = await self._request_json(
            method="PATCH",
            path=f"/internal/v1/normative/documents/{document_id}",
            json_body={
                "category_id": (
                    str(
                        category_id,
                    )
                    if category_id is not None
                    else None
                ),
            },
        )

        return self._parse_document(
            payload,
        )

    async def delete_document(
        self,
        *,
        document_id: UUID,
    ) -> UUID:
        """Удаляет document."""
        payload = await self._request_json(
            method="DELETE",
            path=f"/internal/v1/normative/documents/{document_id}",
        )

        return self._parse_deleted_id(
            payload,
            field_name="document_id",
        )

    async def queue_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Запускает durable indexing."""
        payload = await self._request_json(
            method="POST",
            path=(f"/internal/v1/normative/documents/{document_id}/index"),
        )

        return self._parse_document(
            payload,
        )

    async def get_document_content(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentContent:
        """Возвращает PDF content."""
        response = await self._request(
            method="GET",
            path=(f"/internal/v1/normative/documents/{document_id}/content"),
        )

        content_type = response.headers.get(
            "content-type",
            "application/pdf",
        )

        mime_type = content_type.split(
            ";",
            maxsplit=1,
        )[0].strip()

        return NormativeDocumentContent(
            content=response.content,
            mime_type=mime_type,
        )

    async def _request_json(
        self,
        *,
        method: str,
        path: str,
        json_body: object | None = None,
    ) -> object:
        """Выполняет HTTP request и разбирает JSON."""
        response = await self._request(
            method=method,
            path=path,
            json_body=json_body,
        )

        return self._response_json(
            response,
        )

    async def _request(
        self,
        *,
        method: str,
        path: str,
        json_body: object | None = None,
        data: dict[
            str,
            str,
        ]
        | None = None,
        files: dict[
            str,
            tuple[
                str,
                bytes,
                str,
            ],
        ]
        | None = None,
    ) -> httpx.Response:
        """Выполняет internal HTTP request."""
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
                transport=self._transport,
            ) as client:
                if json_body is None:
                    response = await client.request(
                        method,
                        base_url + path,
                        data=data,
                        files=files,
                    )

                else:
                    response = await client.request(
                        method,
                        base_url + path,
                        json=json_body,
                        data=data,
                        files=files,
                    )

        except httpx.HTTPError as error:
            raise NormativeCatalogUnavailableError(
                "Не удалось обратиться к Knowledge Service: "
                f"{type(error).__name__}: {error}",
            ) from error

        self._raise_for_status(
            response,
        )

        return response

    @staticmethod
    def _raise_for_status(
        response: httpx.Response,
    ) -> None:
        """Преобразует Knowledge HTTP status в application error."""
        if response.status_code < 400:
            return

        detail = HttpNormativeCatalogManager._response_detail(
            response,
        )

        if response.status_code == 404:
            raise NormativeCatalogNotFoundError(
                detail,
            )

        if response.status_code == 409:
            raise NormativeCatalogConflictError(
                detail,
            )

        if response.status_code in {
            400,
            413,
            422,
        }:
            raise NormativeCatalogValidationError(
                detail,
            )

        raise NormativeCatalogUnavailableError(
            detail,
        )

    @staticmethod
    def _response_detail(
        response: httpx.Response,
    ) -> str:
        """Возвращает безопасное описание upstream ошибки."""
        try:
            payload = response.json()

        except ValueError:
            text = response.text.strip()

            return text[:1000] or (
                f"Knowledge Service вернул HTTP {response.status_code}."
            )

        if isinstance(
            payload,
            dict,
        ):
            detail = payload.get(
                "detail",
            )

            if isinstance(
                detail,
                str,
            ):
                return detail

            if detail is not None:
                return json.dumps(
                    detail,
                    ensure_ascii=False,
                )[:1000]

        return f"Knowledge Service вернул HTTP {response.status_code}."

    @staticmethod
    def _response_json(
        response: httpx.Response,
    ) -> object:
        """Разбирает JSON response."""
        try:
            return cast(
                object,
                response.json(),
            )

        except ValueError as error:
            raise NormativeCatalogProtocolError(
                "Knowledge Service вернул невалидный JSON.",
            ) from error

    @staticmethod
    def _encode_mapping(
        values: Mapping[
            str,
            object,
        ],
    ) -> dict[
        str,
        object,
    ]:
        """Преобразует UUID values в JSON-compatible значения."""
        result: dict[
            str,
            object,
        ] = {}

        for key, value in values.items():
            result[key] = (
                str(
                    value,
                )
                if isinstance(
                    value,
                    UUID,
                )
                else value
            )

        return result

    @classmethod
    def _parse_section(
        cls,
        payload: object,
    ) -> NormativeSectionView:
        """Разбирает section payload."""
        data = cls._mapping(
            payload,
            context="section",
        )

        return NormativeSectionView(
            section_id=cls._uuid(
                data,
                "section_id",
            ),
            name=cls._string(
                data,
                "name",
            ),
            system_prompt=cls._string(
                data,
                "system_prompt",
            ),
            created_at=cls._datetime(
                data,
                "created_at",
            ),
            updated_at=cls._datetime(
                data,
                "updated_at",
            ),
        )

    @classmethod
    def _parse_category(
        cls,
        payload: object,
    ) -> NormativeCategoryView:
        """Разбирает category payload."""
        data = cls._mapping(
            payload,
            context="category",
        )

        return NormativeCategoryView(
            category_id=cls._uuid(
                data,
                "category_id",
            ),
            section_id=cls._uuid(
                data,
                "section_id",
            ),
            parent_id=cls._optional_uuid(
                data,
                "parent_id",
            ),
            name=cls._string(
                data,
                "name",
            ),
            created_at=cls._datetime(
                data,
                "created_at",
            ),
            updated_at=cls._datetime(
                data,
                "updated_at",
            ),
        )

    @classmethod
    def _parse_document(
        cls,
        payload: object,
    ) -> NormativeDocumentView:
        """Разбирает document payload."""
        data = cls._mapping(
            payload,
            context="document",
        )

        status_value = cls._string(
            data,
            "index_status",
        )

        if status_value not in _INDEXING_STATUSES:
            raise NormativeCatalogProtocolError(
                "Knowledge Service вернул неизвестный index_status.",
            )

        size_bytes = data.get(
            "size_bytes",
        )

        ready_for_analysis = data.get(
            "ready_for_analysis",
        )

        if not isinstance(
            size_bytes,
            int,
        ):
            raise NormativeCatalogProtocolError(
                "Knowledge Service вернул некорректный size_bytes.",
            )

        if not isinstance(
            ready_for_analysis,
            bool,
        ):
            raise NormativeCatalogProtocolError(
                "Knowledge Service вернул некорректный ready_for_analysis.",
            )

        return NormativeDocumentView(
            document_id=cls._uuid(
                data,
                "document_id",
            ),
            section_id=cls._uuid(
                data,
                "section_id",
            ),
            category_id=cls._optional_uuid(
                data,
                "category_id",
            ),
            original_name=cls._string(
                data,
                "original_name",
            ),
            mime_type=cls._string(
                data,
                "mime_type",
            ),
            size_bytes=size_bytes,
            index_status=cast(
                NormativeIndexingStatus,
                status_value,
            ),
            index_error=cls._optional_string(
                data,
                "index_error",
            ),
            indexed_at=cls._optional_datetime(
                data,
                "indexed_at",
            ),
            ready_for_analysis=ready_for_analysis,
            created_at=cls._datetime(
                data,
                "created_at",
            ),
            updated_at=cls._datetime(
                data,
                "updated_at",
            ),
        )

    @classmethod
    def _parse_deleted_id(
        cls,
        payload: object,
        *,
        field_name: str,
    ) -> UUID:
        """Разбирает delete response."""
        data = cls._mapping(
            payload,
            context="delete response",
        )

        return cls._uuid(
            data,
            field_name,
        )

    @staticmethod
    def _mapping(
        payload: object,
        *,
        context: str,
    ) -> Mapping[
        str,
        object,
    ]:
        """Проверяет JSON object."""
        if not isinstance(
            payload,
            dict,
        ):
            raise NormativeCatalogProtocolError(
                f"Knowledge Service вернул некорректный {context} payload.",
            )

        return cast(
            Mapping[
                str,
                object,
            ],
            payload,
        )

    @staticmethod
    def _string(
        data: Mapping[
            str,
            object,
        ],
        field_name: str,
    ) -> str:
        """Извлекает обязательную строку."""
        value = data.get(
            field_name,
        )

        if not isinstance(
            value,
            str,
        ):
            raise NormativeCatalogProtocolError(
                f"Некорректное поле {field_name} от Knowledge Service.",
            )

        return value

    @classmethod
    def _optional_string(
        cls,
        data: Mapping[
            str,
            object,
        ],
        field_name: str,
    ) -> str | None:
        """Извлекает nullable string."""
        value = data.get(
            field_name,
        )

        if value is None:
            return None

        if not isinstance(
            value,
            str,
        ):
            raise NormativeCatalogProtocolError(
                f"Некорректное поле {field_name} от Knowledge Service.",
            )

        return value

    @staticmethod
    def _uuid(
        data: Mapping[
            str,
            object,
        ],
        field_name: str,
    ) -> UUID:
        """Извлекает обязательный UUID."""
        value = data.get(
            field_name,
        )

        if not isinstance(
            value,
            str,
        ):
            raise NormativeCatalogProtocolError(
                f"Некорректное поле {field_name} от Knowledge Service.",
            )

        try:
            return UUID(
                value,
            )

        except ValueError as error:
            raise NormativeCatalogProtocolError(
                f"Некорректный UUID в поле {field_name}.",
            ) from error

    @classmethod
    def _optional_uuid(
        cls,
        data: Mapping[
            str,
            object,
        ],
        field_name: str,
    ) -> UUID | None:
        """Извлекает nullable UUID."""
        value = data.get(
            field_name,
        )

        if value is None:
            return None

        if not isinstance(
            value,
            str,
        ):
            raise NormativeCatalogProtocolError(
                f"Некорректное поле {field_name} от Knowledge Service.",
            )

        try:
            return UUID(
                value,
            )

        except ValueError as error:
            raise NormativeCatalogProtocolError(
                f"Некорректный UUID в поле {field_name}.",
            ) from error

    @classmethod
    def _datetime(
        cls,
        data: Mapping[
            str,
            object,
        ],
        field_name: str,
    ) -> datetime:
        """Извлекает обязательный ISO datetime."""
        value = cls._string(
            data,
            field_name,
        )

        try:
            return datetime.fromisoformat(
                value.replace(
                    "Z",
                    "+00:00",
                )
            )

        except ValueError as error:
            raise NormativeCatalogProtocolError(
                f"Некорректный datetime в поле {field_name}.",
            ) from error

    @classmethod
    def _optional_datetime(
        cls,
        data: Mapping[
            str,
            object,
        ],
        field_name: str,
    ) -> datetime | None:
        """Извлекает nullable ISO datetime."""
        value = data.get(
            field_name,
        )

        if value is None:
            return None

        if not isinstance(
            value,
            str,
        ):
            raise NormativeCatalogProtocolError(
                f"Некорректное поле {field_name} от Knowledge Service.",
            )

        try:
            return datetime.fromisoformat(
                value.replace(
                    "Z",
                    "+00:00",
                )
            )

        except ValueError as error:
            raise NormativeCatalogProtocolError(
                f"Некорректный datetime в поле {field_name}.",
            ) from error
