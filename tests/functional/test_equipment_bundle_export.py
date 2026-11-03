# tests/functional/test_equipment_bundle_export.py

"""Самостоятельный PDF и ZIP используют новую EQ-версию и точные сохранённые байты."""

import json
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

import fitz
import pytest
from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.application.use_cases.equipment_export import (
    ExportEquipmentBundle,
)
from pdrd_document_service.application.use_cases.equipment_report import (
    ExportEquipmentReport,
)
from pdrd_document_service.infrastructure.pdf.equipment_report import (
    PyMuPdfEquipmentReportWriter,
)


class Documents:
    """Подменяет транспорт, сохраняя настоящий Unicode PDF renderer."""

    def __init__(self, content=b"%PDF-1.7 manufacturer"):
        """Запоминает immutable snapshot и обращения по source ID."""
        self.content = content
        self.calls = []

    async def document(self, source_id):
        """Отдаёт только фактически использованный документ."""
        self.calls.append(source_id)
        return self.content, "application/pdf"

    async def report_pdf(self, job_id, revision, rows):
        """Создаёт реальный PDF без исходного проектного PDF и устаревших замечаний."""
        return ExportEquipmentReport(PyMuPdfEquipmentReportWriter()).execute(
            job_id=job_id, revision=revision, rows=rows
        )


def proposal():
    """Две позиции используют один snapshot с неизменяемым SHA."""
    row = {
        "specification_caption": "Счётчик воды ВСКМ 90-20",
        "pages": [26],
        "item_type": "serial_equipment",
        "stage_status": "comparison_completed",
        "reason": "Сравнение выполнено.",
        "document": {
            "source_id": "EQ-used",
            "sha256": sha256(b"%PDF-1.7 manufacturer").hexdigest(),
            "applicability": "exact_match",
            "trust_status": "unverified_user_override",
            "final_url": "https://maker.example/doc",
        },
        "comparisons": [
            {
                "property_name": "flow_rate",
                "project_value": "2",
                "project_unit": "м3/ч",
                "manufacturer_value": "2",
                "manufacturer_unit": "м3/ч",
                "source_page": 3,
                "explanation": "Условия требуют проверки.",
            }
        ],
    }
    return {
        "job_id": "eq-job",
        "revision": 8,
        "search": {"status": "completed"},
        "report": {"positions": [row, dict(row)]},
    }


async def test_pdf_and_bundle_pin_revision_and_deduplicate_saved_bytes(tmp_path):
    """Все позиции остаются в отчёте, общий документ кладётся один раз, digest проверен."""
    documents = Documents()
    content = await ExportEquipmentBundle(documents).execute(proposal(), bundle=True)
    with ZipFile(BytesIO(content)) as archive:
        assert set(archive.namelist()) == {
            "eq-report.pdf",
            "manifest.json",
            "documents/EQ-used.pdf",
        }
        manifest = json.loads(archive.read("manifest.json"))
        pdf = archive.read("eq-report.pdf")
        assert manifest["revision"] == 8 and len(manifest["positions"]) == 2
        assert sha256(pdf).hexdigest() == manifest["report_sha256"]
        assert archive.read("documents/EQ-used.pdf") == documents.content
    with fitz.open(stream=pdf, filetype="pdf") as rendered:
        text = "".join(page.get_text() for page in rendered)
        assert "Счётчик воды" in text and "Версия EQ: 8" in text
        assert "26" in text and "2 м3/ч" in text
    assert documents.calls == ["EQ-used"]


@pytest.mark.parametrize("case", ["running", "wrong_sha", "unsafe_id", "too_large"])
async def test_bundle_never_silently_loses_or_substitutes_documents(case):
    """Отказ вместо выдачи неполного архива, чужих байтов или незавершённого отчёта."""
    value = proposal()
    if case == "running":
        value["search"]["status"] = "running"
    if case == "wrong_sha":
        value["report"]["positions"][0]["document"]["sha256"] = "0" * 64
    if case == "unsafe_id":
        value["report"]["positions"][0]["document"]["source_id"] = "EQ-../../secret"
    with pytest.raises(ReviewRequestError):
        await ExportEquipmentBundle(
            Documents(), max_bytes=1 if case == "too_large" else 100_000_000
        ).execute(value, bundle=True)
