# services/document-service/src/pdrd_document_service/domain/specification_parameters.py

"""Сохраняет явно записанные характеристики из столбца наименования, отдельно от Caption."""

import re

_VALUE = r"[+-]?\d+(?:[.,]\d+)?(?:\s*(?:\.{2,}|[–—])\s*[+-]?\d+(?:[.,]\d+)?)?"
_LABELLED = (
    ("rated_voltage", r"напряжение(?: питания| номинальное)?", r"[кКмМ]?[ВвVv]", ""),
    ("input_current", r"потребляемый ток|ток потребления", r"[мМ]?[АаAa]", "input"),
    (
        "power",
        r"потребляемая(?: электрическая)? мощность|электрическая мощность",
        r"[кК]?(?:Вт|W)",
        "input",
    ),
    (
        "thermal_power",
        r"теплопроизводительность|тепловая мощность",
        r"[кК]?(?:Вт|W)",
        "",
    ),
    (
        "operating_pressure",
        r"рабочее давление|давление рабочее",
        r"(?:МПа|кПа|бар|Па)",
        "",
    ),
    ("fluid_temperature", r"температура(?: теплоносителя| среды)", r"°[CcСс]", ""),
    ("ambient_temperature", r"температура окружающей среды", r"°[CcСс]", ""),
    ("frequency", r"частота(?: питания)?", r"[кК]?[Гг][Цц]|[kK]?[Hh][Zz]", ""),
)


def specification_parameters(name: str) -> list[dict]:
    """Не выводит напряжение из модели, давление из Ру или миллиметры из обозначения Ду."""
    result = []
    for property_name, label, unit, role in _LABELLED:
        pattern = rf"(?:{label})\s*[-:=]?\s*({_VALUE})\s*({unit})(?![\w])"
        for match in re.finditer(pattern, name, re.I):
            result.append(
                {
                    "property_name": property_name,
                    "value_raw": match.group(1).strip(),
                    "unit_raw": match.group(2),
                    "role": role,
                    "current_type": "",
                    "mode": "",
                    "evidence_text": match.group(),
                    "evidence_status": "confirmed",
                }
            )
    for match in re.finditer(r"(?<!\w)IP\s*\d{2}(?!\w)", name, re.I):
        result.append(
            {
                "property_name": "protection_degree",
                "value_raw": re.sub(r"\s+", "", match.group()).upper(),
                "unit_raw": "",
                "role": "",
                "current_type": "",
                "mode": "",
                "evidence_text": match.group(),
                "evidence_status": "confirmed",
            }
        )
    return result
