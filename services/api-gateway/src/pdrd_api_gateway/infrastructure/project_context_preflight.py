# services/api-gateway/src/pdrd_api_gateway/infrastructure/project_context_preflight.py

"""HTTP orchestration preflight-проверки Project Context / ПЗ."""

import json
import logging
from typing import Any
from uuid import uuid4

import httpx

from pdrd_api_gateway.application.ports.project_context_preflight import (
    InvalidProjectContextPreflightError,
    ProjectContextPreflightCoordinator,
    ProjectContextPreflightResult,
    ProjectContextPreflightUnavailableError,
    ProjectContextPreflightWarning,
)
from pdrd_api_gateway.core.settings import (
    AnalysisServiceSettings,
    DocumentServiceSettings,
    KnowledgeServiceSettings,
    ProjectContextPreflightSettings,
)

logger = logging.getLogger(
    "uvicorn.error",
)


class HttpProjectContextPreflightCoordinator(
    ProjectContextPreflightCoordinator,
):
    """Проверяет ПЗ через existing internal services и подготавливает cache."""

    def __init__(
        self,
        *,
        document_service: DocumentServiceSettings,
        analysis_service: AnalysisServiceSettings,
        knowledge_service: KnowledgeServiceSettings,
        settings: ProjectContextPreflightSettings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Сохраняет service endpoints и bounded HTTP settings."""
        self._document_base_url = document_service.base_url.rstrip(
            "/",
        )

        self._analysis_base_url = analysis_service.base_url.rstrip(
            "/",
        )

        self._knowledge_base_url = knowledge_service.base_url.rstrip(
            "/",
        )

        self._request_timeout_seconds = settings.request_timeout_seconds

        self._connect_timeout_seconds = settings.connect_timeout_seconds

        self._transport = transport

    async def execute(
        self,
        *,
        pdf_content: bytes,
        file_name: str,
        start_page: int,
        end_page: int,
    ) -> ProjectContextPreflightResult:
        """Проверяет диапазон и гарантирует ready reusable cache."""
        if not pdf_content:
            raise InvalidProjectContextPreflightError(
                "Загруженный PDF-файл пуст.",
            )

        if start_page < 1 or end_page <= start_page:
            raise InvalidProjectContextPreflightError(
                "Диапазон ПЗ должен содержать положительные "
                "номера страниц, а конечная страница должна "
                "быть больше начальной.",
            )

        timeout = httpx.Timeout(
            self._request_timeout_seconds,
            connect=(self._connect_timeout_seconds),
        )

        async with httpx.AsyncClient(
            timeout=timeout,
            transport=self._transport,
        ) as client:
            pages = await self._extract_project_context_pages(
                client=client,
                pdf_content=pdf_content,
                file_name=file_name,
                start_page=start_page,
                end_page=end_page,
            )

            resolved = await self._post_json(
                client=client,
                url=(
                    f"{self._knowledge_base_url}"
                    "/internal/v1/project-contexts/resolve-cache"
                ),
                json_payload={
                    "context_id": str(
                        uuid4(),
                    ),
                    "enabled": True,
                    "pages": pages,
                },
            )

            context_id = str(
                resolved.get(
                    "context_id",
                    "",
                )
            ).strip()

            cache_key = (
                str(
                    resolved.get(
                        "cache_key",
                        "",
                    )
                ).strip()
                or None
            )

            cache_hit = bool(
                resolved.get(
                    "cache_hit",
                    False,
                )
            )

            cached_validation = (
                resolved.get(
                    "validation",
                )
                if cache_hit
                else None
            )

            validation = await self._post_json(
                client=client,
                url=(f"{self._analysis_base_url}/internal/v1/project-context/validate"),
                json_payload={
                    "enabled": True,
                    "pages": pages,
                    "cached_validation": (cached_validation),
                },
            )

            if not context_id:
                raise ProjectContextPreflightUnavailableError(
                    "Knowledge Service не вернул Project Context context_id.",
                )

            if cache_key is None:
                raise ProjectContextPreflightUnavailableError(
                    "Knowledge Service не вернул Project Context cache_key.",
                )

            cache_built = False

            if not cache_hit:
                created = await self._post_json(
                    client=client,
                    url=(f"{self._knowledge_base_url}/internal/v1/project-contexts"),
                    json_payload={
                        "context_id": context_id,
                        "enabled": True,
                        "cache_key": cache_key,
                        "validation": (
                            self._validation_snapshot(
                                validation,
                            )
                        ),
                        "pages": pages,
                    },
                )

                created_hit = bool(
                    created.get(
                        "cache_hit",
                        False,
                    )
                )

                cache_hit = cache_hit or created_hit

                cache_built = not created_hit

            warnings = self._warnings_from_validation(
                validation,
            )

            result = ProjectContextPreflightResult(
                cache_hit=cache_hit,
                cache_built=cache_built,
                pages_count=len(
                    pages,
                ),
                requires_confirmation=bool(
                    validation.get(
                        "requires_confirmation",
                        bool(
                            warnings,
                        ),
                    )
                ),
                warnings=warnings,
            )

            logger.info(
                (
                    "project_context_preflight "
                    "cache_hit=%s "
                    "cache_built=%s "
                    "pages=%s "
                    "warnings=%s "
                    "requires_confirmation=%s"
                ),
                result.cache_hit,
                result.cache_built,
                result.pages_count,
                len(
                    result.warnings,
                ),
                result.requires_confirmation,
            )

            return result

    async def _extract_project_context_pages(
        self,
        *,
        client: httpx.AsyncClient,
        pdf_content: bytes,
        file_name: str,
        start_page: int,
        end_page: int,
    ) -> list[
        dict[
            str,
            object,
        ]
    ]:
        """Извлекает только text-only диапазон ПЗ через Document Service."""
        payload = await self._post_json(
            client=client,
            url=(f"{self._document_base_url}/internal/v1/pdf/extract"),
            files={
                "file": (
                    file_name,
                    pdf_content,
                    "application/pdf",
                ),
            },
            data={
                "pages": str(
                    start_page,
                ),
                "use_explanatory_note": ("true"),
                "include_text_geometry": ("false"),
                "note_start_page": str(
                    start_page,
                ),
                "note_end_page": str(
                    end_page,
                ),
            },
        )

        context = payload.get(
            "explanatory_note_context",
        )

        if not isinstance(
            context,
            dict,
        ):
            raise ProjectContextPreflightUnavailableError(
                "Document Service не вернул explanatory_note_context.",
            )

        if (
            context.get(
                "enabled",
            )
            is not True
        ):
            raise ProjectContextPreflightUnavailableError(
                "Document Service вернул выключенный контекст ПЗ.",
            )

        raw_pages = context.get(
            "pages",
            [],
        )

        if (
            not isinstance(
                raw_pages,
                list,
            )
            or not raw_pages
        ):
            raise InvalidProjectContextPreflightError(
                "Выбранный диапазон ПЗ не содержит извлекаемых страниц.",
            )

        pages: list[
            dict[
                str,
                object,
            ]
        ] = []

        for raw_page in raw_pages:
            if not isinstance(
                raw_page,
                dict,
            ):
                raise ProjectContextPreflightUnavailableError(
                    "Document Service вернул некорректную страницу ПЗ.",
                )

            try:
                page_number = int(raw_page["page_number"])

            except (
                KeyError,
                TypeError,
                ValueError,
            ) as error:
                raise ProjectContextPreflightUnavailableError(
                    "Document Service вернул некорректный номер страницы ПЗ.",
                ) from error

            pages.append(
                {
                    "page_number": page_number,
                    "text": str(
                        raw_page.get(
                            "text",
                            "",
                        )
                    ),
                }
            )

        return pages

    async def _post_json(
        self,
        *,
        client: httpx.AsyncClient,
        url: str,
        json_payload: dict[
            str,
            object,
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
        data: dict[
            str,
            str,
        ]
        | None = None,
    ) -> dict[
        str,
        Any,
    ]:
        """Выполняет internal POST и нормализует downstream ошибки."""
        try:
            response = await client.post(
                url,
                json=json_payload,
                files=files,
                data=data,
            )

        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as error:
            raise ProjectContextPreflightUnavailableError(
                "Внутренний сервис preflight ПЗ временно недоступен: "
                f"{type(error).__name__}: {error}",
            ) from error

        except httpx.HTTPError as error:
            raise ProjectContextPreflightUnavailableError(
                "Не удалось выполнить internal HTTP-запрос preflight ПЗ: "
                f"{type(error).__name__}: {error}",
            ) from error

        if response.status_code >= 400:
            detail = self._response_detail(
                response,
            )

            if response.status_code in {
                400,
                409,
                413,
                422,
            }:
                raise InvalidProjectContextPreflightError(
                    detail,
                )

            raise ProjectContextPreflightUnavailableError(
                "Внутренний сервис preflight ПЗ вернул "
                f"HTTP {response.status_code}: {detail}",
            )

        try:
            payload = response.json()

        except ValueError as error:
            raise ProjectContextPreflightUnavailableError(
                "Внутренний сервис preflight ПЗ вернул невалидный JSON.",
            ) from error

        if not isinstance(
            payload,
            dict,
        ):
            raise ProjectContextPreflightUnavailableError(
                "Internal preflight response должен быть JSON object.",
            )

        return payload

    @staticmethod
    def _validation_snapshot(
        validation: dict[
            str,
            Any,
        ],
    ) -> dict[
        str,
        object,
    ]:
        """Оставляет только persisted часть validation contract."""
        classifications = validation.get(
            "classifications",
            [],
        )

        warnings = validation.get(
            "warnings",
            [],
        )

        if not isinstance(
            classifications,
            list,
        ) or not isinstance(
            warnings,
            list,
        ):
            raise ProjectContextPreflightUnavailableError(
                "Analysis Service вернул некорректный validation payload.",
            )

        try:
            pages_count = int(
                validation.get(
                    "pages_count",
                    0,
                )
                or 0
            )

        except (
            TypeError,
            ValueError,
        ) as error:
            raise ProjectContextPreflightUnavailableError(
                "Analysis Service вернул некорректный pages_count.",
            ) from error

        if pages_count < 1:
            raise ProjectContextPreflightUnavailableError(
                "Analysis Service вернул пустой validation диапазона ПЗ.",
            )

        return {
            "enabled": bool(
                validation.get(
                    "enabled",
                    False,
                )
            ),
            "pages_count": pages_count,
            "classifications": classifications,
            "warnings": warnings,
        }

    @staticmethod
    def _warnings_from_validation(
        validation: dict[
            str,
            Any,
        ],
    ) -> tuple[
        ProjectContextPreflightWarning,
        ...,
    ]:
        """Преобразует semantic warnings Analysis Service в frontend contract."""
        raw_warnings = validation.get(
            "warnings",
            [],
        )

        if not isinstance(
            raw_warnings,
            list,
        ):
            raise ProjectContextPreflightUnavailableError(
                "Analysis Service вернул некорректный warnings payload.",
            )

        warnings: list[ProjectContextPreflightWarning] = []

        for item in raw_warnings:
            if not isinstance(
                item,
                dict,
            ):
                raise ProjectContextPreflightUnavailableError(
                    "Analysis Service вернул некорректный warning ПЗ.",
                )

            try:
                page_number = int(item["page_number"])

                confidence = float(
                    item.get(
                        "confidence",
                        0.0,
                    )
                )

            except (
                KeyError,
                TypeError,
                ValueError,
            ) as error:
                raise ProjectContextPreflightUnavailableError(
                    "Analysis Service вернул некорректные warning fields ПЗ.",
                ) from error

            warnings.append(
                ProjectContextPreflightWarning(
                    page_number=page_number,
                    kind=str(
                        item.get(
                            "kind",
                            "other",
                        )
                    ),
                    confidence=min(
                        max(
                            confidence,
                            0.0,
                        ),
                        1.0,
                    ),
                    reason=str(
                        item.get(
                            "reason",
                            "",
                        )
                    ).strip(),
                )
            )

        return tuple(
            warnings,
        )

    @staticmethod
    def _response_detail(
        response: httpx.Response,
    ) -> str:
        """Извлекает безопасный человекочитаемый downstream detail."""
        try:
            payload = response.json()

        except ValueError:
            return response.text.strip() or "Неизвестная ошибка внутреннего сервиса."

        if not isinstance(
            payload,
            dict,
        ):
            return str(
                payload,
            )

        detail = payload.get(
            "detail",
        )

        if (
            isinstance(
                detail,
                str,
            )
            and detail.strip()
        ):
            return detail.strip()

        if detail is not None:
            return json.dumps(
                detail,
                ensure_ascii=False,
            )

        message = payload.get(
            "message",
        )

        if (
            isinstance(
                message,
                str,
            )
            and message.strip()
        ):
            return message.strip()

        return json.dumps(
            payload,
            ensure_ascii=False,
        )
