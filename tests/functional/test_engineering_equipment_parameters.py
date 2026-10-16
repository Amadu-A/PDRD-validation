# tests/functional/test_engineering_equipment_parameters.py

"""Паспортный текст → EQ Facts → точная модификация → проверенная финализация без VLM."""

import pytest
from pdrd_analysis_service.application.equipment_pipeline import compare_equipment_stage
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.transport.http.schemas import FindingDraftPayload
from pdrd_knowledge_service.application.equipment_facts import extract_equipment_facts


class NoVision:
    """Ни извлечение новых паспортных характеристик, ни сравнение не требуют модели."""

    async def generate_json(self, **request):
        """Запрещает модель на всём пути паспортного сравнения."""
        raise AssertionError("Дополнительный VLM-вызов запрещён")


@pytest.mark.parametrize(
    "kind,label,maker,project,unit,status",
    [
        ("sensor_type", "Sensor type", "Pt100;Pt1000", "Ni100", "", "confirmed"),
        ("rtd_wires", "RTD wires", "3", "2", "", "needs_review"),
        (
            "accessory_model",
            "Compatible accessory",
            "BASE-A;BASE-B",
            "BASE-X",
            "",
            "confirmed",
        ),
        ("mounting_width", "Mounting width", "0...400 mm", "450", "mm", "confirmed"),
        (
            "measurement_error",
            "Measurement error",
            "0...0.5 °C",
            "0.7",
            "°C",
            "confirmed",
        ),
    ],
)
async def test_confirmed_manual_parameters_are_compared_through_existing_services(
    kind, label, maker, project, unit, status
):
    """Соседняя модель и неясные подключения не превращаются в доказанный дефект."""
    text = f"CTRL-7 | {label} | {maker}\nCTRL-8 | {label} | {maker}"
    facts = extract_equipment_facts(
        source_id="EQ-manual",
        model="CTRL-7",
        variant="",
        pages=[{"page": 2, "text": text}],
    )
    assert len(facts) == 1 and facts[0]["property_type"] == kind
    pages = [
        {
            "page_number": 1,
            "page_type": "schematic",
            "facts": {
                "equipment_identities": [
                    {
                        "manufacturer": "ACME",
                        "model": "CTRL-7",
                        "variant": "",
                        "status": "resolved",
                        "confidence": 1,
                        "object_ref": "A/K1",
                        "evidence_text": f"A/K1 CTRL-7 {project} {unit}",
                        "visual_regions": [],
                        "parameters": [
                            {
                                "property_name": kind,
                                "value_raw": project,
                                "unit_raw": unit,
                                "evidence_text": f"A/K1 {label} {project} {unit}",
                            }
                        ],
                    }
                ]
            },
        }
    ]
    search = {
        "status": "completed",
        "results": [
            {
                "identity": {"manufacturer": "ACME", "model": "CTRL-7", "variant": ""},
                "status": "found",
                "document": {
                    "source_id": "EQ-manual",
                    "model": "CTRL-7",
                    "manufacturer": "ACME",
                    "sha256": "a" * 64,
                    "trust_status": "trusted",
                    "revision": "R2",
                    "final_url": "https://manufacturer.example/manual.pdf",
                },
                "facts": facts,
            }
        ],
    }
    raw = compare_equipment_stage(pages, search)["candidates_by_page"]["1"][0]
    draft = FindingDraftPayload.model_validate(raw).to_domain()
    assert draft.status == status
    _, final, metrics = await FinalizeFindings(NoVision(), 2000, 8, 1000, 0.5).execute(
        findings=(draft,), experience_by_finding={}
    )
    assert final[0].status == status and metrics["vlm_call_count"] == 0
    assert final[0].equipment_documentation_basis_sources[0].document_revision == "R2"


def test_added_characteristics_do_not_truncate_existing_passport_records():
    """Новые поля не вытесняют старые сведения из прежнего лимита 100 фактов."""
    text = "\n".join(
        f"CTRL-7 Output voltage {index}...{index + 1} V DC" for index in range(110)
    )
    facts = extract_equipment_facts(
        source_id="EQ-many",
        model="CTRL-7",
        variant="",
        pages=[{"page": 1, "text": text}],
    )
    assert len(facts) == 110
