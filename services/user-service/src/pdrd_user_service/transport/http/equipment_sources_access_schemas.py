# services/user-service/src/pdrd_user_service/transport/http/equipment_sources_access_schemas.py

"""Строгие команды и ответы назначения права управления источниками оборудования без полей аутентификации."""

from dataclasses import asdict

from pydantic import Field, StrictBool, StrictInt

from pdrd_user_service.application.use_cases.equipment_sources_access import (
    EquipmentSourcesAccessChange,
)
from pdrd_user_service.transport.http.schemas import StrictSchema, UserResponse


class ChangeEquipmentSourcesAccessRequest(StrictSchema):
    """Принимает только булев флаг и целую ожидаемую версию."""

    enabled: StrictBool
    authorization_version: StrictInt = Field(ge=1)


class EquipmentSourcesAccessChangeResponse(StrictSchema):
    """Возвращает сохранённую версию и действующие права выбранного профиля."""

    user: UserResponse
    equipment_sources_access: bool
    equipment_sources_access_automatic: bool
    equipment_sources_access_editable: bool

    @classmethod
    def from_domain(
        cls, result: EquipmentSourcesAccessChange
    ) -> "EquipmentSourcesAccessChangeResponse":
        """Сериализует только разрешённый профиль и вычисленное состояние."""
        return cls(user=UserResponse.from_domain(result.user), **asdict(result.state))
