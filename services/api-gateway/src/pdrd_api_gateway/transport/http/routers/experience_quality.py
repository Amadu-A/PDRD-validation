# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/experience_quality.py

"""Допуск векторной версии через общий серверный контекст и политику её заданий."""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from pdrd_api_gateway.transport.http.routers.experience import Container, invoke

router = APIRouter(prefix="/api/v1/experience-versions", tags=["experience-quality"])


class QualityRevision(BaseModel):
    """Клиент передаёт CAS, но не identity оператора или статус версии."""

    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)


class RegisterQuality(QualityRevision):
    """Структуру отчёта проверяет Experience как владелец реестра."""

    report: dict


@router.post("/{version_id}/quality")
async def register_quality(
    version_id: UUID, command: RegisterQuality, container: Container
) -> dict:
    """Права проверяются на каждое исходное задание зафиксированного состава."""
    return await invoke(
        container,
        operation="version_quality",
        example_id=version_id,
        command=command.model_dump(),
    )


@router.delete("/{version_id}/quality")
async def revoke_quality(
    version_id: UUID, command: QualityRevision, container: Container
) -> dict:
    """Отзыв не требует подмены отчёта; история решения остаётся у владельца."""
    return await invoke(
        container,
        operation="version_quality_revoke",
        example_id=version_id,
        command=command.model_dump(),
    )
