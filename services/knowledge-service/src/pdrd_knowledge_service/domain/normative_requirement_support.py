# services/knowledge-service/src/pdrd_knowledge_service/domain/normative_requirement_support.py

"""Консервативная оценка исполнимой формы; применимость к проекту оценивает Analysis Service."""

import re

_SIMPLE_BOUND = re.compile(
    r"(?:расход(?:\s+воды)?|напор|давление|диаметр|скорость|объ[её]м|количество|температура)"
    r"\s*(?:(?:должен|должна|должно)\s+(?:быть|составлять)\s+)?"
    r"(?:не менее|не более|>=|<=|≥|≤)\s*\d+(?:[.,]\d+)?\s*"
    r"(?:м[³3]/сут|м[³3]/ч|л/с|м/с|м[³3]|кПа|МПа|мм|шт\.?|°С|м)(?!\w)",
    re.I,
)


def native_rule_supported(requirement):
    """Нижняя граница поддержки: простая числовая норма без неразрешённых условий и ссылок.

    Это не actually_checkable: без фактов проекта и правового свидетельства конкретный
    пункт ещё нельзя объявлять применимым или подтверждённым нарушением.
    """
    return bool(
        requirement.extraction_status == "extracted"
        and requirement.verification_type == "numeric_limit"
        and not requirement.limitations
        and not requirement.applicability_conditions
        and not requirement.exceptions
        and not requirement.normative_dependencies
        and re.search(
            r"гидрант|резервуар|трубопровод|водопровод|труб[аы]|котельн",
            requirement.text,
            re.I,
        )
        and len(_SIMPLE_BOUND.findall(requirement.text)) == 1
    )


def interpretation_blocked(requirement):
    """Повторяет только закрытый guard допустимости атома, не выдумывая поддержку остальных форм.

    Нераспознанное действие с корректным извлечением ещё может быть unsupported
    конкретного evaluator. Оно остаётся unassessed до настоящего Analysis.
    """
    return bool(
        requirement.extraction_status not in {"extracted", "needs_review"}
        or requirement.normative_dependencies
        or not set(requirement.limitations)
        <= {
            "structured_interpretation_required",
            "compound_prescription_requires_review",
        }
        or (
            requirement.extraction_status != "extracted"
            and requirement.verification_type
            not in {
                "conditional",
                "table",
                "formula",
                "system_composition",
                "classification",
                "calculation_required",
            }
        )
    )
