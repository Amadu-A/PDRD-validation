# services/api-gateway/src/pdrd_api_gateway/infrastructure/knowledge/user_package_catalog.py

"""HTTP-адаптер личных пакетов каталога Knowledge Service."""

import json
from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
)

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
)
from pdrd_api_gateway.core.settings import (
    KnowledgeServiceSettings,
)

_USER_PACKAGE_AREA = "user_package"


class _CategoryPayload(BaseModel):
    """Внутренние данные категории Knowledge Service."""

    model_config = ConfigDict(
        extra="ignore",
    )

    category_id: UUID

    section_id: UUID

    parent_id: UUID | None

    name: str

    area: str

    owner_user_id: UUID | None = None

    created_at: datetime

    updated_at: datetime

    def to_view(
        self,
    ) -> NormativeCategoryView:
        """Преобразует внутренние данные в представление приложения."""
        return NormativeCategoryView(
            category_id=self.category_id,
            section_id=self.section_id,
            owner_user_id=self.owner_user_id,
            parent_id=self.parent_id,
            name=self.name,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class _DocumentPayload(BaseModel):
    """Внутренние данные документа Knowledge Service."""

    model_config = ConfigDict(
        extra="ignore",
    )

    document_id: UUID

    section_id: UUID

    category_id: UUID | None

    original_name: str

    mime_type: str

    size_bytes: int

    area: str

    owner_user_id: UUID | None = None

    index_status: NormativeIndexingStatus

    index_error: str | None

    indexed_at: datetime | None

    ready_for_analysis: bool

    created_at: datetime

    updated_at: datetime

    def to_view(
        self,
    ) -> NormativeDocumentView:
        """Преобразует внутренние данные в представление приложения."""
        return NormativeDocumentView(
            document_id=self.document_id,
            section_id=self.section_id,
            owner_user_id=self.owner_user_id,
            category_id=self.category_id,
            original_name=self.original_name,
            mime_type=self.mime_type,
            size_bytes=self.size_bytes,
            index_status=self.index_status,
            index_error=self.index_error,
            indexed_at=self.indexed_at,
            ready_for_analysis=self.ready_for_analysis,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class _DeleteCategoryPayload(BaseModel):
    """Внутренний ответ об удалении категории."""

    category_id: UUID


class _DeleteDocumentPayload(BaseModel):
    """Внутренний ответ об удалении документа."""

    document_id: UUID


class HttpUserPackageCatalogManager:
    """Управляет user-package catalog через Knowledge Service."""

    def __init__(
        self,
        *,
        settings: KnowledgeServiceSettings,
        transport: httpx.AsyncBaseTransport | None = None,
        owner_user_id: UUID | None = None,
    ) -> None:
        """Сохраняет настройки и необязательный тестовый транспорт."""
        self._settings = settings

        self._transport = transport
        self._owner_user_id = owner_user_id

    def for_owner(self, owner_user_id: UUID) -> "HttpUserPackageCatalogManager":
        """Создаёт отдельный адаптер без изменения общего состояния приложения."""
        return HttpUserPackageCatalogManager(
            settings=self._settings,
            transport=self._transport,
            owner_user_id=owner_user_id,
        )

    async def list_categories(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeCategoryView,
        ...,
    ]:
        """Возвращает только user-package categories."""
        payload = await self._request_json(
            method="GET",
            path=(f"/internal/v1/normative/sections/{section_id}/categories"),
            params={
                "area": _USER_PACKAGE_AREA,
                **(
                    {"owner_user_id": str(self._owner_user_id)}
                    if self._owner_user_id
                    else {}
                ),
            },
        )

        if not isinstance(
            payload,
            list,
        ):
            raise NormativeCatalogProtocolError(
                "Knowledge Service вернул некорректный список user-package categories.",
            )

        return tuple(
            self._parse_user_package_category(
                item,
            ).to_view()
            for item in payload
        )

    async def create_category(
        self,
        *,
        section_id: UUID,
        name: str,
        parent_id: UUID | None,
    ) -> NormativeCategoryView:
        """Создаёт категорию области user_package."""
        if parent_id is not None:
            parent = await self.get_category(category_id=parent_id)
            if parent.section_id != section_id:
                raise NormativeCatalogValidationError(
                    "Родитель пакета находится в другом разделе."
                )
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
                "area": _USER_PACKAGE_AREA,
                **(
                    {"owner_user_id": str(self._owner_user_id)}
                    if self._owner_user_id
                    else {}
                ),
            },
        )

        return self._parse_user_package_category(
            payload,
        ).to_view()

    async def get_category(
        self,
        *,
        category_id: UUID,
    ) -> NormativeCategoryView:
        """Возвращает категорию личного пакета."""
        payload = await self._request_json(
            method="GET",
            path=(f"/internal/v1/normative/categories/{category_id}"),
        )

        return self._parse_user_package_category(
            payload,
        ).to_view()

    async def update_category(
        self,
        *,
        category_id: UUID,
        changes: Mapping[
            str,
            object,
        ],
    ) -> NormativeCategoryView:
        """Изменяет только категорию области user_package."""
        if changes.get("parent_id") is not None:
            await self.get_category(category_id=UUID(str(changes["parent_id"])))
        await self.get_category(
            category_id=category_id,
        )

        payload = await self._request_json(
            method="PATCH",
            path=(f"/internal/v1/normative/categories/{category_id}"),
            json_body=self._encode_mapping(
                changes,
            ),
        )

        return self._parse_user_package_category(
            payload,
        ).to_view()

    async def delete_category(
        self,
        *,
        category_id: UUID,
    ) -> UUID:
        """Удаляет только категорию области user_package."""
        await self.get_category(
            category_id=category_id,
        )

        payload = await self._request_json(
            method="DELETE",
            path=(f"/internal/v1/normative/categories/{category_id}"),
        )

        try:
            response = _DeleteCategoryPayload.model_validate(
                payload,
            )

        except ValidationError as error:
            raise NormativeCatalogProtocolError(
                "Knowledge Service вернул некорректный delete category payload.",
            ) from error

        return response.category_id

    async def list_documents(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeDocumentView,
        ...,
    ]:
        """Возвращает только user-документы личных пакетов."""
        payload = await self._request_json(
            method="GET",
            path=(f"/internal/v1/normative/sections/{section_id}/documents"),
            params={
                "area": _USER_PACKAGE_AREA,
                **(
                    {"owner_user_id": str(self._owner_user_id)}
                    if self._owner_user_id
                    else {}
                ),
            },
        )

        if not isinstance(
            payload,
            list,
        ):
            raise NormativeCatalogProtocolError(
                "Knowledge Service вернул некорректный список user-package documents.",
            )

        return tuple(
            self._parse_user_package_document(
                item,
            ).to_view()
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
        """Загружает user-package PDF/DOC/DOCX."""
        if category_id is not None:
            await self.get_category(category_id=category_id)
        data = {
            "area": _USER_PACKAGE_AREA,
        }

        if self._owner_user_id is not None:
            data["owner_user_id"] = str(self._owner_user_id)

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

        return self._parse_user_package_document(
            self._response_json(
                response,
            )
        ).to_view()

    async def get_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Возвращает документ личного пакета."""
        payload = await self._request_json(
            method="GET",
            path=(f"/internal/v1/normative/documents/{document_id}"),
        )

        return self._parse_user_package_document(
            payload,
        ).to_view()

    async def move_document(
        self,
        *,
        document_id: UUID,
        category_id: UUID | None,
    ) -> NormativeDocumentView:
        """Перемещает только документ личного пакета."""
        if category_id is not None:
            await self.get_category(category_id=category_id)
        await self.get_document(
            document_id=document_id,
        )

        payload = await self._request_json(
            method="PATCH",
            path=(f"/internal/v1/normative/documents/{document_id}"),
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

        return self._parse_user_package_document(
            payload,
        ).to_view()

    async def delete_document(
        self,
        *,
        document_id: UUID,
    ) -> UUID:
        """Удаляет только документ личного пакета."""
        await self.get_document(
            document_id=document_id,
        )

        payload = await self._request_json(
            method="DELETE",
            path=(f"/internal/v1/normative/documents/{document_id}"),
        )

        try:
            response = _DeleteDocumentPayload.model_validate(
                payload,
            )

        except ValidationError as error:
            raise NormativeCatalogProtocolError(
                "Knowledge Service вернул некорректный delete document payload.",
            ) from error

        return response.document_id

    async def queue_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Ставит user-документ пакета в очередь индексации."""
        await self.get_document(
            document_id=document_id,
        )

        payload = await self._request_json(
            method="POST",
            path=(f"/internal/v1/normative/documents/{document_id}/index"),
        )

        return self._parse_user_package_document(
            payload,
        ).to_view()

    async def get_document_content(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentContent:
        """Возвращает доступное для просмотра в браузере package PDF."""
        await self.get_document(
            document_id=document_id,
        )

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
        params: Mapping[
            str,
            str,
        ]
        | None = None,
    ) -> object:
        """Выполняет запрос и возвращает JSON."""
        response = await self._request(
            method=method,
            path=path,
            json_body=json_body,
            params=params,
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
        params: Mapping[
            str,
            str,
        ]
        | None = None,
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
        """Выполняет внутренний HTTP-запрос Knowledge Service."""
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
                response = await client.request(
                    method,
                    base_url + path,
                    headers=(
                        {"X-PDRD-Package-Owner": str(self._owner_user_id)}
                        if self._owner_user_id
                        else {}
                    ),
                    params=params,
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
        """Преобразует upstream HTTP status в прикладную ошибку."""
        if response.status_code < 400:
            return

        detail = HttpUserPackageCatalogManager._response_detail(
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
        """Извлекает безопасное upstream описание ошибки."""
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
        """Разбирает ответ JSON."""
        try:
            return response.json()

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
        """Преобразует UUID в JSON-compatible значения."""
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

    def _parse_user_package_category(
        self,
        payload: object,
    ) -> _CategoryPayload:
        """Разбирает категорию и запрещает переход в чужую область."""
        try:
            category = _CategoryPayload.model_validate(
                payload,
            )

        except ValidationError as error:
            raise NormativeCatalogProtocolError(
                "Knowledge Service вернул некорректный category payload.",
            ) from error

        if category.area != _USER_PACKAGE_AREA or (
            self._owner_user_id is not None
            and category.owner_user_id != self._owner_user_id
        ):
            raise NormativeCatalogNotFoundError(
                "Пользовательский пакет не найден.",
            )

        return category

    def _parse_user_package_document(
        self,
        payload: object,
    ) -> _DocumentPayload:
        """Разбирает документ и запрещает переход в чужую область."""
        try:
            document = _DocumentPayload.model_validate(
                payload,
            )

        except ValidationError as error:
            raise NormativeCatalogProtocolError(
                "Knowledge Service вернул некорректный document payload.",
            ) from error

        if document.area != _USER_PACKAGE_AREA or (
            self._owner_user_id is not None
            and document.owner_user_id != self._owner_user_id
        ):
            raise NormativeCatalogNotFoundError(
                "Документ пользовательского пакета не найден.",
            )

        return document
