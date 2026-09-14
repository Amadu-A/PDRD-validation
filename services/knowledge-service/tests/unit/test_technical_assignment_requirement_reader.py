# services/knowledge-service/tests/unit/test_technical_assignment_requirement_reader.py

"""Unit tests Qdrant reader atomic T requirements."""

from typing import (
    Any,
    ClassVar,
    Self,
)
from uuid import uuid4

import pdrd_knowledge_service.infrastructure.vector_store.technical_assignment_requirements as reader_module
import pytest
from pdrd_knowledge_service.application.ports.technical_assignment_requirement_reader import (
    TechnicalAssignmentRequirementReaderError,
)
from pdrd_knowledge_service.infrastructure.vector_store.technical_assignment_requirements import (
    QdrantTechnicalAssignmentRequirementReader,
)


class _FakeResponse:
    """Fake Qdrant response."""

    def __init__(
        self,
        payload: dict[str, Any],
    ) -> None:
        """Сохраняет JSON payload."""
        self._payload = payload

    def raise_for_status(
        self,
    ) -> None:
        """Имитирует успешный HTTP status."""

    def json(
        self,
    ) -> dict[str, Any]:
        """Возвращает configured payload."""
        return self._payload


class _FakeAsyncClient:
    """Fake httpx.AsyncClient с двумя scroll pages."""

    responses: ClassVar[list[_FakeResponse]] = []

    requests: ClassVar[
        list[
            dict[
                str,
                Any,
            ]
        ]
    ] = []

    def __init__(
        self,
        *args: object,
        **kwargs: object,
    ) -> None:
        """Игнорирует transport settings."""
        del args
        del kwargs

    async def __aenter__(
        self,
    ) -> Self:
        """Открывает fake client."""
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        """Закрывает fake client."""
        del exc_type
        del exc_value
        del traceback

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
    ) -> _FakeResponse:
        """Записывает request и отдаёт следующий response."""
        self.requests.append(
            {
                "url": url,
                "json": json,
            }
        )

        return self.responses.pop(
            0,
        )


def _point(
    *,
    point_id: str,
    requirement_id: str,
    requirement_index: int,
    page: int,
    technical_assignment_id: str,
    analysis_document_id: str,
    section_id: str,
) -> dict[str, Any]:
    """Создаёт valid Qdrant requirement point."""
    return {
        "id": point_id,
        "payload": {
            "source_type": "technical_assignment",
            "representation": "requirement_text",
            "technical_assignment_id": (technical_assignment_id),
            "analysis_document_id": (analysis_document_id),
            "section_id": section_id,
            "source_file": "ТЗ.pdf",
            "source_sha256": "a" * 64,
            "page": page,
            "requirement_id": requirement_id,
            "requirement_index": requirement_index,
            "requirement_strength": "candidate",
            "scopes": [
                "thermal",
            ],
            "normative_refs": [],
            "source_text": ("Система трубопроводов — четырехтрубная."),
            "text": ("Система трубопроводов — четырехтрубная."),
        },
    }


@pytest.mark.asyncio
async def test_reader_filters_scope_paginates_and_sorts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reader читает только immutable requirement_text scope."""
    technical_assignment_id = uuid4()

    analysis_document_id = uuid4()

    section_id = uuid4()

    _FakeAsyncClient.requests = []

    _FakeAsyncClient.responses = [
        _FakeResponse(
            {
                "result": {
                    "points": [
                        _point(
                            point_id=str(
                                uuid4(),
                            ),
                            requirement_id="T-R2",
                            requirement_index=2,
                            page=2,
                            technical_assignment_id=str(
                                technical_assignment_id,
                            ),
                            analysis_document_id=str(
                                analysis_document_id,
                            ),
                            section_id=str(
                                section_id,
                            ),
                        )
                    ],
                    "next_page_offset": "next",
                }
            }
        ),
        _FakeResponse(
            {
                "result": {
                    "points": [
                        _point(
                            point_id=str(
                                uuid4(),
                            ),
                            requirement_id="T-R1",
                            requirement_index=1,
                            page=1,
                            technical_assignment_id=str(
                                technical_assignment_id,
                            ),
                            analysis_document_id=str(
                                analysis_document_id,
                            ),
                            section_id=str(
                                section_id,
                            ),
                        )
                    ],
                    "next_page_offset": None,
                }
            }
        ),
    ]

    monkeypatch.setattr(
        reader_module.httpx,
        "AsyncClient",
        _FakeAsyncClient,
    )

    reader = QdrantTechnicalAssignmentRequirementReader(
        base_url="http://qdrant:6333",
        request_timeout_seconds=30.0,
        collection="dva_technical_assignment_active",
    )

    requirements = await reader.list_requirements(
        technical_assignment_id=technical_assignment_id,
        analysis_document_id=analysis_document_id,
        section_id=section_id,
    )

    assert [requirement.requirement_id for requirement in requirements] == [
        "T-R1",
        "T-R2",
    ]

    assert (
        len(
            _FakeAsyncClient.requests,
        )
        == 2
    )

    first_body = _FakeAsyncClient.requests[0]["json"]

    assert first_body["with_vector"] is False

    must = first_body["filter"]["must"]

    assert {condition["key"]: condition["match"]["value"] for condition in must} == {
        "technical_assignment_id": str(
            technical_assignment_id,
        ),
        "analysis_document_id": str(
            analysis_document_id,
        ),
        "section_id": str(
            section_id,
        ),
        "source_type": "technical_assignment",
        "representation": "requirement_text",
    }

    second_body = _FakeAsyncClient.requests[1]["json"]

    assert second_body["offset"] == "next"


def test_reader_rejects_malformed_requirement_instead_of_skipping() -> None:
    """Повреждённый atomic point не теряется молча."""
    technical_assignment_id = uuid4()

    analysis_document_id = uuid4()

    section_id = uuid4()

    point = _point(
        point_id=str(
            uuid4(),
        ),
        requirement_id="T-R1",
        requirement_index=1,
        page=1,
        technical_assignment_id=str(
            technical_assignment_id,
        ),
        analysis_document_id=str(
            analysis_document_id,
        ),
        section_id=str(
            section_id,
        ),
    )

    del point["payload"]["text"]

    with pytest.raises(
        TechnicalAssignmentRequirementReaderError,
    ):
        QdrantTechnicalAssignmentRequirementReader._build_requirement(
            point,
            technical_assignment_id=(technical_assignment_id),
            analysis_document_id=(analysis_document_id),
            section_id=section_id,
        )
