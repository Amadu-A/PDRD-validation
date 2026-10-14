# services/admin-service/src/pdrd_admin_service/contracts/equipment_sources_access_models.py

"""Строгие контракты назначения права управления источниками оборудования через административный фасад."""

from pydantic import Field, StrictBool, StrictInt

from pdrd_admin_service.contracts.models import StrictModel, UserResponse


class ChangeEquipmentSourcesAccessRequest(StrictModel):
    """Содержит только новое значение галочки и ожидаемую версию прав."""

    enabled: StrictBool
    authorization_version: StrictInt = Field(ge=1)


class EquipmentSourcesAccessChangeResponse(StrictModel):
    """Возвращает подтверждённые User Service профиль и состояние права управления источниками оборудования."""

    user: UserResponse
    equipment_sources_access: StrictBool
    equipment_sources_access_automatic: StrictBool
    equipment_sources_access_editable: StrictBool
