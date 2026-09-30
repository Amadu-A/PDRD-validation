# services/experience-service/src/pdrd_experience_service/transport/http/schemas/review.py

"""Строгие команды Review: браузер не задаёт оригиналы, теги или инженера.

Все координаты нормализованы в 0..1000. Домен повторно проверяет геометрию,
источник находки, принадлежность листа и ожидаемую ревизию.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictCommand(BaseModel):
    """Запрещает неизвестные поля и неявное преобразование типов JSON."""

    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)


class Box(BaseModel):
    """Прямоугольник в системе координат физического листа."""

    model_config = ConfigDict(extra="forbid", strict=True)
    x_min: float = Field(ge=0, le=1000, allow_inf_nan=False)
    y_min: float = Field(ge=0, le=1000, allow_inf_nan=False)
    x_max: float = Field(ge=0, le=1000, allow_inf_nan=False)
    y_max: float = Field(ge=0, le=1000, allow_inf_nan=False)


class FindingCommand(StrictCommand):
    """Команда для существующей серверной находки."""

    finding_id: str = Field(min_length=1, max_length=256)


class DecideCommand(FindingCommand):
    """Явное принятие или отклонение; pending задаётся только через reset."""

    action: Literal["decide"]
    decision: Literal["accepted", "rejected"]


class ResetCommand(FindingCommand):
    """Отмена предыдущего решения с сохранением истории."""

    action: Literal["reset"]


class EditCommand(FindingCommand):
    """Исправление текста и нормативного основания."""

    action: Literal["edit"]
    text: str = Field(min_length=1, max_length=10000)
    normative_basis: str = Field(max_length=2000)


class AddCommand(EditCommand):
    """Новая Gold-находка с двумя областями одного листа."""

    action: Literal["add"]
    page_number: int = Field(ge=1)
    issue_box: Box
    callout_box: Box


class GeometryCommand(FindingCommand):
    """Геометрия отображения, не подтверждение для обучающей базы."""

    action: Literal["geometry"]
    regions: list[Box] = Field(max_length=4)
    callout_box: Box | None


class ApproveCommand(StrictCommand):
    """Утверждение рассмотренной редакции для последующих этапов."""

    action: Literal["approve"]


class ConfirmAreaCommand(FindingCommand):
    """Явная проверка области; версии Review и подтверждения независимы."""

    action: Literal["confirm_area"]
    expected_confirmation_revision: int = Field(ge=0)
    regions: list[Box] = Field(min_length=1, max_length=4)
    mode: Literal["proposed", "redrawn"]
    note: str = Field(max_length=1000)


class RevokeAreaCommand(FindingCommand):
    """Отзыв подтверждения сохраняет причину и прежние координаты в аудите."""

    action: Literal["revoke_area"]
    expected_confirmation_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)


ReviewCommand = Annotated[
    DecideCommand
    | ResetCommand
    | AddCommand
    | EditCommand
    | GeometryCommand
    | ApproveCommand
    | ConfirmAreaCommand
    | RevokeAreaCommand,
    Field(discriminator="action"),
]
