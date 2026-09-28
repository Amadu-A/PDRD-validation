# services/experience-service/src/pdrd_experience_service/transport/http/review_commands.py

"""Перевод HTTP-команд в существующие сценарии Review без бизнес-логики."""

from uuid import UUID

from pdrd_experience_service.application.use_cases.confirm_areas import (
    ConfirmArea,
    RevokeArea,
)
from pdrd_experience_service.application.use_cases.review import ChangeReview
from pdrd_experience_service.domain.area_confirmation import ConfirmationMode
from pdrd_experience_service.domain.review import Decision, Rectangle, ReviewSession
from pdrd_experience_service.transport.http.schemas.review import (
    AddCommand,
    ApproveCommand,
    Box,
    ConfirmAreaCommand,
    DecideCommand,
    EditCommand,
    GeometryCommand,
    ResetCommand,
    ReviewCommand,
    RevokeAreaCommand,
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
    confirm_area: ConfirmArea | None = None,
    revoke_area: RevokeArea | None = None,
) -> ReviewSession:
    """Передаёт доверенного инженера отдельно от тела пользовательской команды."""
    if isinstance(command, (ConfirmAreaCommand, RevokeAreaCommand)):
        arguments = {
            "job_id": job_id,
            "finding_id": command.finding_id,
            "actor": actor,
            "expected_review_revision": command.expected_revision,
            "expected_confirmation_revision": command.expected_confirmation_revision,
        }
        if isinstance(command, ConfirmAreaCommand) and confirm_area is not None:
            await confirm_area.execute(
                **arguments,
                regions=tuple(rectangle(box) for box in command.regions),
                mode=ConfirmationMode(command.mode),
                note=command.note,
            )
            return await confirm_area.reviews.load(job_id)
        if isinstance(command, RevokeAreaCommand) and revoke_area is not None:
            await revoke_area.execute(**arguments, reason=command.reason)
            return await revoke_area.reviews.load(job_id)
        raise RuntimeError("Подтверждение областей не подключено.")
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
