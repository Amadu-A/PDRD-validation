# tests/functional/test_equipment_snapshot_flow.py

"""Проверяет путь от текста PDF производителя до EQ-кандидата проекта."""

from hashlib import sha256

import fitz
from pdrd_analysis_service.application.equipment_pipeline import compare_equipment_stage
from pdrd_document_service.infrastructure.equipment_document import (
    PyMuPdfEquipmentDocumentReader,
)
from pdrd_knowledge_service.application.equipment_facts import (
    extract_equipment_facts,
)


def _manufacturer_pdf(line: str) -> bytes:
    """Создаёт реальный текстовый PDF с точной моделью и характеристикой."""
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 60), "MEAN WELL")
    page.insert_text((40, 90), line)
    content = document.tobytes()
    document.close()
    return content


def _project_page(voltage: str) -> list[dict]:
    """Описывает подтверждённую маркировку и проектное напряжение."""
    return [
        {
            "page_number": 3,
            "page_type": "schematic",
            "facts": {
                "equipment_identities": [
                    {
                        "manufacturer": "MEAN WELL",
                        "model": "DRC-100B",
                        "variant": "",
                        "status": "resolved",
                        "confidence": 0.96,
                        "object_ref": "PS1",
                        "evidence_text": "PS1 DRC-100B",
                        "visual_regions": [
                            {"x_min": 100, "y_min": 100, "x_max": 200, "y_max": 200}
                        ],
                        "parameters": [
                            {
                                "property_name": "output_voltage",
                                "value_raw": voltage,
                                "unit_raw": "V",
                                "role": "output",
                                "current_type": "DC",
                                "mode": "",
                                "evidence_text": f"PS1 {voltage} V DC",
                            }
                        ],
                    }
                ]
            },
        }
    ]


def _compare(line: str, project_voltage: str) -> dict:
    """Извлекает PDF, EQ Fact и связывает факт с неизменяемым snapshot."""
    content = _manufacturer_pdf(line)
    extracted = PyMuPdfEquipmentDocumentReader().extract(
        content, "application/pdf", max_pages=3
    )
    source_id = "EQ-" + sha256(content).hexdigest()[:32]
    facts = extract_equipment_facts(
        source_id=source_id,
        model="DRC-100B",
        variant="",
        pages=[{"page": page.page, "text": page.text} for page in extracted.pages],
    )
    search = {
        "status": "completed",
        "results": [
            {
                "identity": {
                    "manufacturer": "MEAN WELL",
                    "model": "DRC-100B",
                    "variant": "",
                },
                "document": {
                    "source_id": source_id,
                    "sha256": sha256(content).hexdigest(),
                    "revision": "B",
                    "final_url": "https://www.meanwell.com/drc-100b.pdf",
                    "trust_status": "trusted",
                },
                "facts": facts,
            }
        ],
    }
    return compare_equipment_stage(_project_page(project_voltage), search)


def test_text_pdf_creates_evidenced_mismatch_candidate() -> None:
    """Неподходящее значение привязано к странице и snapshot производителя."""
    result = _compare("DRC-100B Output voltage 21...29 V DC", "30")
    candidate = result["candidates_by_page"]["3"][0]
    assert candidate["status"] == "confirmed"
    assert candidate["equipment_documentation_source_ids"][0].startswith("EQ-")
    assert candidate["equipment_documentation_basis_sources"][0]["page"] == 1
    assert candidate["equipment_details"]["project_value"] == "30"


def test_text_pdf_compatible_value_creates_no_finding() -> None:
    """Совместимое проектное значение не порождает замечания."""
    result = _compare("DRC-100B Output voltage 21...29 V DC", "24")
    assert result["candidates_by_page"] == {}
    assert result["comparisons"][0]["status"] == "matched"


def test_other_model_pdf_cannot_create_evidence() -> None:
    """Характеристика соседней модели не становится фактом искомой модели."""
    result = _compare("DRC-100A Output voltage 10...14 V DC", "30")
    assert result["candidates_by_page"] == {}
    assert result["status"] == "incomplete"


def test_html_table_keeps_exact_model_column() -> None:
    """HTML-таблица одной модели проходит через безопасный text-only reader."""
    html = (
        b"<html><script>ignore</script><table>"
        b"<tr><th>Parameter</th><th>DRC-100B</th></tr>"
        b"<tr><td>Output voltage</td><td>21...29 V DC</td></tr>"
        b"</table></html>"
    )
    extracted = PyMuPdfEquipmentDocumentReader().extract(html, "text/html", max_pages=1)
    facts = extract_equipment_facts(
        source_id="EQ-html",
        model="DRC-100B",
        variant="",
        pages=[{"page": 1, "text": extracted.pages[0].text}],
    )
    assert len(facts) == 1
    assert facts[0]["table_title"] == "Parameter | DRC-100B"
    assert facts[0]["value_raw"] == "21...29"


def test_visual_eq_facts_create_review_candidate_with_snapshot() -> None:
    """Визуальная строка проходит в общий pipeline с сохранённым evidence."""
    from pdrd_knowledge_service.application.equipment_vision_facts import (
        normalize_equipment_vision_facts,
    )

    facts = normalize_equipment_vision_facts(
        source_id="EQ-scan",
        model="DRC-100B",
        variant="",
        facts=(
            {
                "page": 2,
                "property_name": "Output voltage",
                "value_raw": "21...29",
                "unit_raw": "V",
                "current_type": "DC",
                "snippet": "DRC-100B Output voltage 21...29 V DC",
            },
        ),
    )
    result = compare_equipment_stage(
        _project_page("30"),
        {
            "status": "completed",
            "results": [
                {
                    "identity": {"manufacturer": "MEAN WELL", "model": "DRC-100B"},
                    "document": {
                        "source_id": "EQ-scan",
                        "revision": "B",
                        "trust_status": "trusted",
                    },
                    "facts": facts,
                }
            ],
        },
    )
    candidate = result["candidates_by_page"]["3"][0]
    assert candidate["status"] == "needs_review"
    assert candidate["equipment_documentation_source_ids"] == ["EQ-scan"]
    assert candidate["equipment_documentation_basis_sources"][0]["page"] == 2


def test_pdf_table_geometry_keeps_neighbor_models_separate() -> None:
    """Реальная PDF таблица извлекается по колонкам без VLM и соседних значений."""
    document = fitz.open()
    page = document.new_page()
    rows = [
        ("Parameter", "DRC-100A", "DRC-100B"),
        ("Output voltage", "10...14 V DC", "21...29 V DC"),
    ]
    for y in (70, 100, 130):
        page.draw_line((40, y), (490, y))
    for x in (40, 190, 340, 490):
        page.draw_line((x, 70), (x, 130))
    for index, row in enumerate(rows):
        for column, cell in enumerate(row):
            page.insert_text((45 + 150 * column, 89 + index * 30), cell, fontsize=9)
    content = document.tobytes()
    document.close()
    extracted = PyMuPdfEquipmentDocumentReader().extract(content, "application/pdf", 2)
    facts = extract_equipment_facts(
        source_id="EQ-table",
        model="DRC-100B",
        variant="",
        pages=[{"page": item.page, "text": item.text} for item in extracted.pages],
    )
    assert len(facts) == 1
    assert facts[0]["value_raw"] == "21...29"
    assert facts[0]["applicability"] == "exact_model_column"
