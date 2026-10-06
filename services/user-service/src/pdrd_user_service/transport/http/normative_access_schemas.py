# services/user-service/src/pdrd_user_service/transport/http/normative_access_schemas.py

"""Строгие команды и ответы назначения права удаления нормативных объектов без полей аутентификации."""

from dataclasses import asdict

from pydantic import Field, StrictBool, StrictInt

from pdrd_user_service.application.use_cases.normative_access import (
    NormativeAccessChange,
)
from pdrd_user_service.transport.http.schemas import StrictSchema, UserResponse


class ChangeNormativeAccessRequest(StrictSchema):
    """Принимает только булев флаг и целую ожидаемую версию."""

    enabled: StrictBool
    authorization_version: StrictInt = Field(ge=1)


class NormativeAccessChangeResponse(StrictSchema):
    """Возвращает сохранённую версию и действующие права выбранного профиля."""

    user: UserResponse
    normative_access: bool
    normative_access_automatic: bool
    normative_access_editable: bool

    @classmethod
    def from_domain(
        cls, result: NormativeAccessChange
    ) -> "NormativeAccessChangeResponse":
        """Сериализует только разрешённый профиль и вычисленное состояние."""
        return cls(user=UserResponse.from_domain(result.user), **asdict(result.state))
