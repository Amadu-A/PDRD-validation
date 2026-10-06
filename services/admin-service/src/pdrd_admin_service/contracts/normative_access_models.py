# services/admin-service/src/pdrd_admin_service/contracts/normative_access_models.py

"""Строгие контракты назначения права удаления нормативных объектов через административный фасад."""

from pydantic import Field, StrictBool, StrictInt

from pdrd_admin_service.contracts.models import StrictModel, UserResponse


class ChangeNormativeAccessRequest(StrictModel):
    """Содержит только новое значение галочки и ожидаемую версию прав."""

    enabled: StrictBool
    authorization_version: StrictInt = Field(ge=1)


class NormativeAccessChangeResponse(StrictModel):
    """Возвращает подтверждённые User Service профиль и состояние права удаления нормативных объектов."""

    user: UserResponse
    normative_access: StrictBool
    normative_access_automatic: StrictBool
    normative_access_editable: StrictBool
