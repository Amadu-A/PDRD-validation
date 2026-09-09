# tests/architecture/test_technical_assignment_upload.py

"""Architecture guards upload/lifecycle boundary ТЗ."""

from pathlib import Path

REPOSITORY_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

API_ROOT = REPOSITORY_ROOT / "services" / "api-gateway" / "src" / "pdrd_api_gateway"

FRONTEND_ROOT = REPOSITORY_ROOT / "frontend" / "src"


def test_technical_assignment_is_separate_from_n_u_ids() -> None:
    """ТЗ хранится отдельным snapshot, а не document_ids."""
    snapshot = (API_ROOT / "domain" / "normative_snapshot.py").read_text(
        encoding="utf-8",
    )

    assert "technical_assignment" in snapshot

    assert "TechnicalAssignmentSnapshot" in snapshot

    assert '"technical_assignment"' in snapshot


def test_technical_assignment_upload_is_size_bounded() -> None:
    """Gateway имеет отдельный upload limit ТЗ."""
    settings = (API_ROOT / "core" / "settings.py").read_text(
        encoding="utf-8",
    )

    router = (API_ROOT / "transport" / "http" / "routers" / "analyses.py").read_text(
        encoding="utf-8",
    )

    assert "class TechnicalAssignmentSettings" in settings

    assert "default=100" in settings

    assert ".technical_assignment" in router

    assert "max_upload_bytes" in router


def test_technical_assignment_is_stored_with_analysis_artifacts() -> None:
    """T-file не попадает в permanent normative volume."""
    storage = (API_ROOT / "infrastructure" / "storage" / "filesystem.py").read_text(
        encoding="utf-8",
    )

    assert '"technical_assignment.bin"' in storage

    assert "save_technical_assignment" in storage

    assert "load_technical_assignment" in storage

    assert "/data/normative" not in storage


def test_frontend_serializes_optional_technical_assignment() -> None:
    """Sidebar T-file отправляется отдельным multipart field."""
    form = (FRONTEND_ROOT / "js" / "features" / "analysis" / "form.js").read_text(
        encoding="utf-8",
    )

    picker = (
        FRONTEND_ROOT / "js" / "features" / "technical_assignment" / "file.js"
    ).read_text(
        encoding="utf-8",
    )

    assert '"technical_assignment"' in form

    assert "technicalAssignmentInput" in form

    assert "PDF, DOC или DOCX" in form

    assert "input.disabled = false" in picker

    assert "section.removeAttribute(" in picker
