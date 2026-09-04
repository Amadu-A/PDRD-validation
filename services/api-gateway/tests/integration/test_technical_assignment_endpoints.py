# services/api-gateway/tests/integration/test_technical_assignment_endpoints.py

"""HTTP contract tests optional technical assignment upload."""

from collections.abc import (
    Awaitable,
    Callable,
)
from hashlib import sha256
from typing import Any
from uuid import (
    UUID,
    uuid4,
)

from fastapi.testclient import TestClient
from pdrd_api_gateway.application.use_cases.check_readiness import (
    CheckReadiness,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.core.settings import (
    DatabaseSettings,
    Settings,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
)
from pdrd_api_gateway.domain.normative_snapshot import (
    NormativeAnalysisSnapshot,
)
from pdrd_api_gateway.domain.technical_assignment import (
    TechnicalAssignmentSnapshot,
)
from pdrd_api_gateway.main import (
    create_app,
)


class StaticReadiness:
    """Fake infrastructure readiness."""

    async def is_ready(
        self,
    ) -> bool:
        """Всегда готов."""
        return True


class TechnicalAssignmentSubmitStub:
    """Captures multipart ТЗ transport."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует captured args."""
        self.technical_assignment_content: bytes | None = None

        self.technical_assignment_file_name: str | None = None

    async def execute(
        self,
        **kwargs: Any,
    ) -> AnalysisJob:
        """Возвращает fake job с T snapshot."""
        self.technical_assignment_content = kwargs.get(
            "technical_assignment_content",
        )

        self.technical_assignment_file_name = kwargs.get(
            "technical_assignment_file_name",
        )

        document_id = uuid4()

        section_id = kwargs["normative_section_id"]

        normative_document_ids = kwargs["normative_document_ids"]

        assert isinstance(
            section_id,
            UUID,
        )

        assert isinstance(
            self.technical_assignment_content,
            bytes,
        )

        assert isinstance(
            self.technical_assignment_file_name,
            str,
        )

        technical_assignment = TechnicalAssignmentSnapshot.create(
            analysis_document_id=document_id,
            section_id=section_id,
            source_file=(self.technical_assignment_file_name),
            content=(self.technical_assignment_content),
        )

        snapshot = NormativeAnalysisSnapshot.create(
            section_id=section_id,
            document_ids=(normative_document_ids or ()),
            system_prompt="stub prompt",
            technical_assignment=(technical_assignment),
        )

        return AnalysisJob.create(
            document_id=document_id,
            normative_snapshot=snapshot,
        )


class GetAnalysisStub:
    """Fake GetAnalysisJob."""

    async def execute(
        self,
        *,
        job_id: UUID,
    ) -> None:
        """Не содержит jobs."""
        return None


async def noop_shutdown() -> None:
    """Fake shutdown callback."""


def build_client(
    submit_stub: TechnicalAssignmentSubmitStub,
) -> TestClient:
    """Создаёт test client API Gateway."""
    settings = Settings(
        _env_file=None,
        environment="test",
        database=DatabaseSettings(
            password="test-password",
        ),
    )

    check_readiness = CheckReadiness(
        database=StaticReadiness(),
        broker=StaticReadiness(),
    )

    shutdown_callback: Callable[
        [],
        Awaitable[None],
    ] = noop_shutdown

    container = ApplicationContainer(
        settings=settings,
        check_readiness=check_readiness,
        shutdown_callback=shutdown_callback,
        get_analysis_job=(  # type: ignore[arg-type]
            GetAnalysisStub()
        ),
        submit_analysis=(  # type: ignore[arg-type]
            submit_stub
        ),
    )

    return TestClient(
        create_app(
            container=container,
        )
    )


def test_create_analysis_accepts_technical_assignment() -> None:
    """Multipart transport передаёт один optional T-file."""
    submit_stub = TechnicalAssignmentSubmitStub()

    section_id = uuid4()

    normative_document_id = uuid4()

    technical_assignment_content = b"technical-assignment"

    with build_client(
        submit_stub,
    ) as client:
        response = client.post(
            "/api/v1/analyses",
            files={
                "pdf": (
                    "drawing.pdf",
                    b"pdf",
                    "application/pdf",
                ),
                "technical_assignment": (
                    "ТЗ объекта.pdf",
                    technical_assignment_content,
                    "application/pdf",
                ),
            },
            data={
                "pages": "1",
                "normative_section_id": str(
                    section_id,
                ),
                "normative_document_ids": (f'["{normative_document_id}"]'),
            },
        )

    assert response.status_code == 202

    payload = response.json()

    technical_assignment = payload["technical_assignment"]

    assert technical_assignment["section_id"] == str(
        section_id,
    )

    assert technical_assignment["source_file"] == "ТЗ объекта.pdf"

    assert technical_assignment["size_bytes"] == len(
        technical_assignment_content,
    )

    assert (
        technical_assignment["sha256"]
        == sha256(
            technical_assignment_content,
        ).hexdigest()
    )

    assert submit_stub.technical_assignment_content == technical_assignment_content

    assert submit_stub.technical_assignment_file_name == "ТЗ объекта.pdf"
