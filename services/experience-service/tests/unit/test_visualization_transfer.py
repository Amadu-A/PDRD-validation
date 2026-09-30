# services/experience-service/tests/unit/test_visualization_transfer.py

"""Регрессия переноса серверных координат без автоматического подтверждения."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pdrd_experience_service.domain.experience_selection import (
    ConfirmedFindingArea,
    select_experience_candidates,
)
from pdrd_experience_service.domain.review import (
    Decision,
    ProposedRegion,
    Rectangle,
    ReviewError,
    ReviewSession,
)
from pdrd_experience_service.infrastructure.analysis.visualization import (
    completed_analysis_from_visualization,
)
from pdrd_experience_service.infrastructure.database.codec import (
    finding_from_json,
    finding_to_json,
)

JOB = UUID(int=401)
DOC = UUID(int=402)
NOW = datetime(2026, 9, 25, tzinfo=UTC)
BOX = Rectangle(100, 150, 300, 350)


def source_payload() -> tuple[dict[str, object], dict[str, object]]:
    """Создаёт результат и визуализацию доверенного backend-задания."""
    result: dict[str, object] = {
        "findings": [
            {
                "finding_id": "F-1",
                "page": 2,
                "comment": "Не показана маркировка двери.",
                "normative_basis": "СП 1, пункт 2",
            },
            {
                "finding_id": "F-2",
                "page": 2,
                "comment": "Нет определённой области.",
            },
            {
                "finding_id": "H-1",
                "page": 2,
                "comment": "Промежуточная гипотеза.",
                "status": "hypothesis",
            },
        ]
    }
    visualization: dict[str, object] = {
        "job_id": str(JOB),
        "document_id": str(DOC),
        "pages": [
            {
                "page_number": 2,
                "locations": [
                    {
                        "finding_id": "F-1",
                        "status": "located",
                        "method": "analysis_vlm",
                        "regions": [
                            {
                                "bbox": {
                                    "x_min": 100,
                                    "y_min": 150,
                                    "x_max": 300,
                                    "y_max": 350,
                                },
                                "source": "analysis_vlm",
                                "confidence": 0.8,
                            }
                        ],
                    },
                    {
                        "finding_id": "F-2",
                        "status": "unlocated",
                        "regions": [],
                    },
                ],
            }
        ],
    }
    return result, visualization


def mapped(
    result: dict[str, object],
    visualization: dict[str, object],
):
    """Передаёт в mapper только серверные данные и исходные PDF-байты."""
    return completed_analysis_from_visualization(
        job_id=JOB,
        document_id=DOC,
        source_filename="drawing.pdf",
        pdf_content=b"%PDF-1.7 test source",
        result=result,
        visualization=visualization,
    )


def approved_with_proposals() -> ReviewSession:
    """Утверждает текстовый review при наличии непроверенной VLM-рамки."""
    result, visualization = source_payload()
    source = mapped(result, visualization)
    session = ReviewSession.open(
        job_id=source.job_id,
        document_id=source.document_id,
        source_filename=source.source_filename,
        source_sha256=source.source_sha256,
        originals=source.findings,
        rendered_pages=source.rendered_pages,
        actor="engineer:1",
        at=NOW,
    )
    for finding in session.findings:
        session = session.decide(
            finding_id=finding.finding_id,
            decision=Decision.ACCEPTED,
            actor="engineer:1",
            at=NOW,
            expected_revision=session.revision,
        )
    return session.approve(
        actor="engineer:1",
        at=NOW,
        expected_revision=session.revision,
    )


def test_server_visualization_preserves_proposals_and_unlocated_text() -> None:
    """VLM-кандидаты передаются отдельно от подтверждённой issue_box."""
    result, visualization = source_payload()
    source = mapped(result, visualization)

    assert source.source_sha256 != "0" * 64
    assert source.rendered_pages == (2,)
    assert [item.finding_id for item in source.findings] == ["F-1", "F-2"]
    assert source.findings[0].proposed_regions == (
        ProposedRegion(BOX, "analysis_vlm", 0.8, "analysis_vlm"),
    )
    assert source.findings[1].proposed_regions == ()

    session = approved_with_proposals()
    assert session.findings[0].proposed_regions == source.findings[0].proposed_regions
    assert all(item.issue_box is None for item in session.findings)
    assert len(session.accepted_for_pdf().findings) == 2
    assert select_experience_candidates(session=session, confirmed_areas=()) == ()

    with pytest.raises(ReviewError):
        ReviewSession.open(
            job_id=JOB,
            document_id=DOC,
            source_filename="drawing.pdf",
            source_sha256=source.source_sha256,
            originals=source.findings,
            rendered_pages=(3,),
            actor="engineer:1",
            at=NOW,
        )


def test_only_separate_engineer_confirmation_creates_experience() -> None:
    """Автоматическая рамка не становится обучающим примером сама по себе."""
    session = approved_with_proposals()
    candidates = select_experience_candidates(
        session=session,
        confirmed_areas=(
            ConfirmedFindingArea(
                job_id=JOB,
                finding_id="F-1",
                page_number=2,
                regions=(BOX,),
                confirmed_by="engineer:2",
                confirmed_at=NOW,
            ),
        ),
    )
    assert len(candidates) == 1
    assert candidates[0].finding_id == "F-1"
    assert candidates[0].issue_regions == (BOX,)


def test_wrong_page_and_invalid_bbox_do_not_create_proposals() -> None:
    """Неверная локализация оставляет замечание текстовым."""
    result, visualization = source_payload()
    page = visualization["pages"][0]
    page["locations"][0]["regions"][0]["bbox"]["x_max"] = 100
    assert mapped(result, visualization).findings[0].proposed_regions == ()

    result, visualization = source_payload()
    visualization["pages"][0]["page_number"] = 3
    assert mapped(result, visualization).findings[0].proposed_regions == ()


def test_foreign_job_duplicate_page_and_duplicate_finding_fail_closed() -> None:
    """Не смешивает задания и не открывает неполный или неоднозначный Review."""
    result, visualization = source_payload()
    visualization["job_id"] = str(UUID(int=999))
    with pytest.raises(ReviewError):
        mapped(result, visualization)

    result, visualization = source_payload()
    visualization["pages"].append(visualization["pages"][0])
    with pytest.raises(ReviewError):
        mapped(result, visualization)

    result, visualization = source_payload()
    result["findings"].append(result["findings"][0])
    with pytest.raises(ReviewError):
        mapped(result, visualization)


def test_duplicate_location_and_non_numeric_coordinates_stay_unconfirmed() -> None:
    """При конфликте локализации нельзя выбирать произвольную рамку."""
    result, visualization = source_payload()
    location = visualization["pages"][0]["locations"][0]
    visualization["pages"][0]["locations"].append(location)
    assert mapped(result, visualization).findings[0].proposed_regions == ()

    result, visualization = source_payload()
    visualization["pages"][0]["locations"][0]["regions"][0]["bbox"]["x_min"] = True
    assert mapped(result, visualization).findings[0].proposed_regions == ()


def test_codec_retains_proposals_and_reads_legacy_snapshots() -> None:
    """JSONB roundtrip сохраняет provenance и старые сессии без нового поля."""
    finding = approved_with_proposals().findings[0]
    encoded = finding_to_json(finding)
    assert finding_from_json(encoded) == finding

    encoded.pop("proposed_regions")
    assert finding_from_json(encoded).proposed_regions == ()
