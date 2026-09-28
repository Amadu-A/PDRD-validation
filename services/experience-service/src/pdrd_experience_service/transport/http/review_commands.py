# services/experience-service/src/pdrd_experience_service/transport/http/review_commands.py

"""Перевод HTTP-команд в существующие сценарии Review без бизнес-логики."""

from uuid import UUID

from pdrd_experience_service.application.use_cases.review import ChangeReview
from pdrd_experience_service.domain.review import Decision, Rectangle, ReviewSession
from pdrd_experience_service.transport.http.schemas.review import (
    AddCommand,
    ApproveCommand,
    Box,
    DecideCommand,
    EditCommand,
    GeometryCommand,
    ResetCommand,
    ReviewCommand,
)


def rectangle(box: Box | None) -> Rectangle | None:
    """Преобразует валидированный JSON в доменный прямоугольник."""
    return Rectangle(**box.model_dump()) if box is not None else None


async def execute_command(
    use_case: ChangeReview,
    *,
    job_id: UUID,
    actor: str,
    command: ReviewCommand,
) -> ReviewSession:
    """Передаёт доверенного инженера отдельно от тела пользовательской команды."""
    arguments = {
        "job_id": job_id,
        "actor": actor,
        "expected_revision": command.expected_revision,
    }
    if isinstance(command, ApproveCommand):
        return await use_case.approve(**arguments)
    arguments["finding_id"] = command.finding_id
    if isinstance(command, AddCommand):
        return await use_case.add_manual(
            **arguments,
            page_number=command.page_number,
            text=command.text,
            normative_basis=command.normative_basis,
            issue_box=rectangle(command.issue_box),
            callout_box=rectangle(command.callout_box),
        )
    if isinstance(command, EditCommand):
        return await use_case.edit(
            **arguments, text=command.text, normative_basis=command.normative_basis
        )
    if isinstance(command, DecideCommand):
        return await use_case.decide(**arguments, decision=Decision(command.decision))
    if isinstance(command, ResetCommand):
        return await use_case.reset_decision(**arguments)
    if isinstance(command, GeometryCommand):
        return await use_case.change_geometry(
            **arguments,
            regions=tuple(rectangle(box) for box in command.regions),
            callout_box=rectangle(command.callout_box),
        )
    raise ValueError("Неизвестная команда Review.")
