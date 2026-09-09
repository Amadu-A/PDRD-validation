# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/vector_store/technical_assignment_requirements.py

"""Qdrant read adapter atomic requirements технического задания."""

from typing import Any
from uuid import UUID

import httpx

from pdrd_knowledge_service.application.ports.technical_assignment_requirement_reader import (
    TechnicalAssignmentRequirementReaderError,
)
from pdrd_knowledge_service.domain.technical_assignment_requirements import (
    TechnicalAssignmentRequirementSource,
)

_SCROLL_BATCH_SIZE = 256

_ALLOWED_STRENGTHS: set[str] = {
    "explicit",
    "candidate",
}


class QdrantTechnicalAssignmentRequirementReader:
    """Читает только requirement_text points одного immutable ТЗ."""

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: float,
        collection: str,
    ) -> None:
        """Сохраняет Qdrant settings."""
        self._base_url = base_url.rstrip(
            "/",
        )

        self._request_timeout_seconds = request_timeout_seconds

        self._collection = collection

    async def list_requirements(
        self,
        *,
        technical_assignment_id: UUID,
        analysis_document_id: UUID,
        section_id: UUID,
    ) -> tuple[
        TechnicalAssignmentRequirementSource,
        ...,
    ]:
        """Scroll-читает filtered atomic requirements без vectors."""
        result: list[TechnicalAssignmentRequirementSource] = []

        offset: object | None = None

        try:
            async with httpx.AsyncClient(
                timeout=self._request_timeout_seconds,
            ) as client:
                while True:
                    body: dict[str, Any] = {
                        "limit": _SCROLL_BATCH_SIZE,
                        "with_payload": True,
                        "with_vector": False,
                        "filter": {
                            "must": [
                                {
                                    "key": ("technical_assignment_id"),
                                    "match": {
                                        "value": str(
                                            technical_assignment_id,
                                        ),
                                    },
                                },
                                {
                                    "key": ("analysis_document_id"),
                                    "match": {
                                        "value": str(
                                            analysis_document_id,
                                        ),
                                    },
                                },
                                {
                                    "key": "section_id",
                                    "match": {
                                        "value": str(
                                            section_id,
                                        ),
                                    },
                                },
                                {
                                    "key": "source_type",
                                    "match": {
                                        "value": ("technical_assignment"),
                                    },
                                },
                                {
                                    "key": "representation",
                                    "match": {
                                        "value": ("requirement_text"),
                                    },
                                },
                            ],
                        },
                    }

                    if offset is not None:
                        body["offset"] = offset

                    response = await client.post(
                        (
                            f"{self._base_url}/collections/"
                            f"{self._collection}/points/scroll"
                        ),
                        json=body,
                    )

                    response.raise_for_status()

                    payload = self._scroll_result(
                        response,
                    )

                    points = payload.get(
                        "points",
                        [],
                    )

                    if not isinstance(
                        points,
                        list,
                    ):
                        raise (
                            TechnicalAssignmentRequirementReaderError(
                                "Qdrant вернул invalid technical-assignment points.",
                            )
                        )

                    for point in points:
                        if not isinstance(
                            point,
                            dict,
                        ):
                            raise (
                                TechnicalAssignmentRequirementReaderError(
                                    "Qdrant вернул invalid technical-assignment point.",
                                )
                            )

                        result.append(
                            self._build_requirement(
                                point,
                                technical_assignment_id=(technical_assignment_id),
                                analysis_document_id=(analysis_document_id),
                                section_id=section_id,
                            )
                        )

                    offset = payload.get(
                        "next_page_offset",
                    )

                    if offset is None:
                        break

        except TechnicalAssignmentRequirementReaderError:
            raise

        except (
            httpx.HTTPError,
            ValueError,
        ) as error:
            raise TechnicalAssignmentRequirementReaderError(
                "Не удалось прочитать atomic requirements ТЗ из Qdrant.",
            ) from error

        return tuple(
            sorted(
                result,
                key=lambda requirement: (
                    requirement.requirement_index,
                    requirement.point_id,
                ),
            )
        )

    @staticmethod
    def _scroll_result(
        response: httpx.Response,
    ) -> dict[str, Any]:
        """Проверяет Qdrant scroll envelope."""
        raw = response.json()

        if not isinstance(
            raw,
            dict,
        ):
            raise TechnicalAssignmentRequirementReaderError(
                "Qdrant вернул invalid JSON envelope.",
            )

        result = raw.get(
            "result",
        )

        if not isinstance(
            result,
            dict,
        ):
            raise TechnicalAssignmentRequirementReaderError(
                "Qdrant вернул invalid scroll result.",
            )

        return result

    @classmethod
    def _build_requirement(
        cls,
        point: dict[str, Any],
        *,
        technical_assignment_id: UUID,
        analysis_document_id: UUID,
        section_id: UUID,
    ) -> TechnicalAssignmentRequirementSource:
        """Преобразует persisted requirement_text payload."""
        point_id = cls._required_string(
            point,
            "id",
        )

        payload = point.get(
            "payload",
        )

        if not isinstance(
            payload,
            dict,
        ):
            raise TechnicalAssignmentRequirementReaderError(
                "Atomic T point не содержит payload.",
            )

        expected_scope = {
            "technical_assignment_id": str(
                technical_assignment_id,
            ),
            "analysis_document_id": str(
                analysis_document_id,
            ),
            "section_id": str(
                section_id,
            ),
        }

        actual_scope = {
            key: cls._required_string(
                payload,
                key,
            )
            for key in expected_scope
        }

        if actual_scope != expected_scope:
            raise TechnicalAssignmentRequirementReaderError(
                "Atomic T point нарушает immutable scope.",
            )

        source_type = cls._required_string(
            payload,
            "source_type",
        )

        representation = cls._required_string(
            payload,
            "representation",
        )

        if (
            source_type != "technical_assignment"
            or representation != "requirement_text"
        ):
            raise TechnicalAssignmentRequirementReaderError(
                "Atomic T point имеет неверную representation.",
            )

        strength = cls._required_string(
            payload,
            "requirement_strength",
        )

        if strength not in _ALLOWED_STRENGTHS:
            raise TechnicalAssignmentRequirementReaderError(
                "Atomic T point имеет неизвестный requirement_strength.",
            )

        return TechnicalAssignmentRequirementSource(
            point_id=point_id,
            requirement_id=cls._required_string(
                payload,
                "requirement_id",
            ),
            requirement_index=cls._required_positive_int(
                payload,
                "requirement_index",
            ),
            technical_assignment_id=(actual_scope["technical_assignment_id"]),
            analysis_document_id=(actual_scope["analysis_document_id"]),
            section_id=actual_scope["section_id"],
            source_file=cls._optional_string(
                payload.get(
                    "source_file",
                )
            ),
            source_sha256=cls._optional_string(
                payload.get(
                    "source_sha256",
                )
            ),
            page=cls._required_positive_int(
                payload,
                "page",
            ),
            strength=strength,
            scopes=cls._string_tuple(
                payload.get(
                    "scopes",
                )
            ),
            normative_refs=cls._string_tuple(
                payload.get(
                    "normative_refs",
                )
            ),
            source_text=cls._required_string(
                payload,
                "source_text",
            ),
            text=cls._required_string(
                payload,
                "text",
            ),
        )

    @staticmethod
    def _required_string(
        source: dict[str, Any],
        key: str,
    ) -> str:
        """Читает обязательную непустую строку."""
        value = source.get(
            key,
        )

        if not isinstance(
            value,
            str,
        ):
            raise TechnicalAssignmentRequirementReaderError(
                f"Atomic T point: {key} должен быть строкой.",
            )

        normalized = value.strip()

        if not normalized:
            raise TechnicalAssignmentRequirementReaderError(
                f"Atomic T point: {key} не должен быть пустым.",
            )

        return normalized

    @staticmethod
    def _required_positive_int(
        source: dict[str, Any],
        key: str,
    ) -> int:
        """Читает обязательное положительное целое."""
        value = source.get(
            key,
        )

        if (
            not isinstance(
                value,
                int,
            )
            or isinstance(
                value,
                bool,
            )
            or value < 1
        ):
            raise TechnicalAssignmentRequirementReaderError(
                f"Atomic T point: {key} должен быть positive int.",
            )

        return value

    @staticmethod
    def _optional_string(
        value: Any,
    ) -> str | None:
        """Нормализует optional payload string."""
        if value is None:
            return None

        if not isinstance(
            value,
            str,
        ):
            raise TechnicalAssignmentRequirementReaderError(
                "Atomic T point содержит invalid optional string.",
            )

        normalized = value.strip()

        return normalized if normalized else None

    @staticmethod
    def _string_tuple(
        value: Any,
    ) -> tuple[
        str,
        ...,
    ]:
        """Нормализует list[str] без потери source order."""
        if value is None:
            return ()

        if not isinstance(
            value,
            (
                list,
                tuple,
            ),
        ):
            raise TechnicalAssignmentRequirementReaderError(
                "Atomic T point содержит invalid string list.",
            )

        result: list[str] = []

        seen: set[str] = set()

        for item in value:
            if not isinstance(
                item,
                str,
            ):
                raise TechnicalAssignmentRequirementReaderError(
                    "Atomic T point содержит non-string list item.",
                )

            normalized = item.strip()

            if not normalized or normalized in seen:
                continue

            seen.add(
                normalized,
            )

            result.append(
                normalized,
            )

        return tuple(
            result,
        )
