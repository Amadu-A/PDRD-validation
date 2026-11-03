# services/document-service/src/pdrd_document_service/application/use_cases/equipment_report.py

"""Самостоятельный отчёт EQ по закреплённой версии, без исходного проектного PDF."""

from dataclasses import dataclass
from typing import Protocol

from pdrd_document_service.application.ports.pdf_annotation import (
    PdfReportField,
    PdfReportFinding,
    PdfTextReport,
)

_LABELS = {
    "serial_equipment": "Серийное оборудование",
    "standard_product": "Типовое изделие",
    "material": "Материал",
    "prefabricated_structure": "Сборная конструкция",
    "designed_structure": "Проектируемая конструкция",
    "unidentified": "Нужна идентификация",
    "not_searched": "Поиск не выполнялся",
    "search_running": "Идёт поиск",
    "search_temporarily_unavailable": "Поиск временно недоступен",
    "candidates_found": "Кандидаты найдены",
    "document_not_found": "Документ не найден",
    "download_failed": "Ошибка загрузки",
    "document_rejected": "Кандидаты отклонены",
    "document_found": "Документ найден",
    "document_verified": "Применимость проверена",
    "facts_extracted": "Характеристики извлечены",
    "comparison_completed": "Сравнение выполнено",
    "comparison_unavailable": "Недостаточно данных для сравнения",
    "budget_exhausted": "Бюджет исчерпан",
    "exact_match": "Точная модель и исполнение",
    "family_match": "Семейство моделей",
    "technical_reference": "Справочный документ",
    "partial_match": "Частичное совпадение",
    "wrong_model": "Другая модель",
    "wrong_variant": "Другое исполнение",
    "wrong_manufacturer": "Другой изготовитель",
    "insufficient_evidence": "Недостаточно доказательств",
    "unreadable_document": "Документ не прочитан",
    "trusted": "Сайт разрешён в реестре",
    "unverified_user_override": "Неподтверждённый сайт разрешён пользователем только в этом задании",
    "pending": "Доверие не установлено",
    "outer_diameter": "Наружный диаметр",
    "nominal_diameter": "Условный диаметр",
    "wall_thickness": "Толщина стенки",
    "rated_pressure": "Номинальное давление",
    "working_pressure": "Рабочее давление",
    "rated_flow_rate": "Номинальный расход",
    "max_flow_rate": "Максимальный расход",
    "volume": "Объём",
    "input_voltage": "Входное напряжение",
    "output_voltage": "Выходное напряжение",
}


def _label(value: str) -> str:
    """Переводит стабильные коды контракта в пользовательский текст PDF."""
    return _LABELS.get(value, value)


class EquipmentReportWriter(Protocol):
    """Порт создания самостоятельного PDF в существующем Document Service."""

    def build(self, report: PdfTextReport) -> bytes:
        """Возвращает PDF без изменения аннотаций проекта."""


@dataclass(frozen=True, slots=True)
class ExportEquipmentReport:
    """Передаёт все позиции и доказательства существующему текстовому PDF writer."""

    writer: EquipmentReportWriter

    def execute(self, *, job_id: str, revision: int, rows: list[dict]) -> bytes:
        """Фиксирует применимость, источник, страницы и пределы выполненного сравнения."""
        findings = []
        for index, row in enumerate(rows, 1):
            doc = row.get("document") or {}
            fields = [
                PdfReportField(
                    "Страницы проекта", ", ".join(map(str, row.get("pages", [])))
                ),
                PdfReportField(
                    "Категория", _label(row.get("item_type", "serial_equipment"))
                ),
                PdfReportField(
                    "Состояние поиска", _label(row.get("stage_status", "not_searched"))
                ),
                PdfReportField(
                    "Результат",
                    row.get("reason", "Нет доказанных данных для сравнения."),
                ),
            ]
            if doc:
                fields.extend(
                    (
                        PdfReportField(
                            "Применимость",
                            _label(doc.get("applicability", "insufficient_evidence")),
                        ),
                        PdfReportField(
                            "Доверие источнику",
                            _label(doc.get("trust_status", "pending")),
                        ),
                        PdfReportField("Источник", doc.get("source_id", "")),
                        PdfReportField("SHA-256", doc.get("sha256", "")),
                        PdfReportField("Прочитанный URL", doc.get("final_url", "")),
                        PdfReportField("Редакция", doc.get("revision") or "Не указана"),
                    )
                )
            for comparison in row.get("comparisons", []):
                fields.append(
                    PdfReportField(
                        "Сравнение",
                        f"{_label(comparison.get('property_name', ''))}: проект {comparison.get('project_value')} "
                        f"{comparison.get('project_unit', '')}; документ {comparison.get('manufacturer_value')} "
                        f"{comparison.get('manufacturer_unit', '')}, стр. {comparison.get('source_page')}. "
                        f"{comparison.get('explanation', '')}",
                    )
                )
            for fact in row.get("facts", []):
                fields.append(
                    PdfReportField(
                        "Извлечённый факт",
                        f"{fact.get('property_name')}: {fact.get('value_raw')} {fact.get('unit_raw', '')}, "
                        f"стр. {fact.get('page')}. {fact.get('snippet', '')}",
                    )
                )
            for candidate in row.get("candidates", []):
                fields.append(
                    PdfReportField(
                        "Кандидат",
                        f"{candidate.get('candidate_id')}: {candidate.get('document_url')}; "
                        f"{candidate.get('applicability')}; {', '.join(candidate.get('reason_codes', []))}",
                    )
                )
            name = row.get("specification_caption") or " ".join(
                filter(
                    None,
                    (row.get("manufacturer"), row.get("model"), row.get("variant")),
                )
            )
            findings.append(PdfReportFinding(f"{index}. {name}", tuple(fields)))
        return self.writer.build(
            PdfTextReport(
                "Проверка оборудования и материалов — EQ",
                (
                    PdfReportField("Задание", job_id),
                    PdfReportField("Версия EQ", str(revision)),
                ),
                f"Проверено позиций: {len(rows)}. "
                "Наличие ссылки и разрешение сайта не доказывают применимость документа.",
                tuple(findings),
                (
                    "Справочник не подтверждает конкретную поставку. Отсутствие данных не является соответствием.",
                ),
                findings_label="Позиции спецификации",
            )
        )
