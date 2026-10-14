# services/api-gateway/src/pdrd_api_gateway/core/equipment_updates.py

"""Собирает зависимости отдельного EQ-дополнения к завершённому анализу."""

from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.application.use_cases.equipment_update import BuildEquipmentUpdate
from pdrd_api_gateway.infrastructure.equipment_comparison import HttpEquipmentComparison
from pdrd_api_gateway.infrastructure.equipment_search import EquipmentSearchClient


def build_equipment_update(container) -> BuildEquipmentUpdate:
    """Использует существующие хранилище результата и настройки сервисов."""
    if container.get_analysis_result is None:
        raise ReviewRequestError(503, "Хранилище отчёта недоступно.")
    return BuildEquipmentUpdate(
        container.get_analysis_result,
        EquipmentSearchClient(container.settings.equipment_search),
        HttpEquipmentComparison(container.settings.analysis_service),
    )
