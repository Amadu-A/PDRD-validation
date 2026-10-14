# services/equipment-search-service/src/pdrd_equipment_search_service/domain/source_management.py

"""Ошибки изменения существующего каталога источников."""


class SourceAlreadyExistsError(ValueError):
    """Повторное добавление не должно менять разрешение существующего сайта."""
