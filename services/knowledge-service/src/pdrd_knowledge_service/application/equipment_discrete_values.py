# services/knowledge-service/src/pdrd_knowledge_service/application/equipment_discrete_values.py

"""Строгий разбор технических перечислений и явно заданного boolean."""

import re
from typing import Any

_BOOLEAN = {
    "true": True,
    "yes": True,
    "да": True,
    "required": True,
    "false": False,
    "no": False,
    "нет": False,
    "not required": False,
}


def parse_discrete_value(
    property_type: str, text: str
) -> tuple[str, dict[str, Any], str] | None:
    """Не делает числовые или логические выводы по свободному тексту."""
    value = text.strip(" |:=.")
    if property_type == "trip_curve":
        parts = re.split(r"\s*[,;/]\s*", value.upper())
        if parts and all(part in {"B", "C", "D", "K", "Z"} for part in parts):
            return "enumeration", {"values": sorted(set(parts))}, value
    if property_type == "protection_degree" and re.fullmatch(r"IP\d{2}", value, re.I):
        return "enumeration", {"values": [value.upper()]}, value
    if property_type == "requires_earthing" and value.casefold() in _BOOLEAN:
        return "boolean", {"value": _BOOLEAN[value.casefold()]}, value
    if (
        property_type in {"operating_mode", "contact_configuration"}
        and value
        and len(value) <= 120
    ):
        return "text", {"value": value}, value
    return None
