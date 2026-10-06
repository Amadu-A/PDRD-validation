# tests/architecture/test_reviewed_pdf_boundaries.py

"""Границы утверждения Review, доверенных координат и отдельного рендеринга PDF."""

import ast
import inspect
import logging
from pathlib import Path
from uuid import UUID

import pytest
from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
)
from pdrd_api_gateway.application.ports.reviewed_pdf import (
    ReviewedPdfFinding,
    ReviewedPdfManifest,
)
from pdrd_api_gateway.application.use_cases.get_reviewed_pdf import GetReviewedPdf
from pdrd_api_gateway.application.use_cases.reviewed_pdf_payload import reviewed_payload
from pdrd_api_gateway.core.observability import configure_review_logging
from pdrd_api_gateway.transport.http.routers.review import (
    require_review_channel,
    router,
)
from pdrd_api_gateway.transport.http.schemas.review import StrictCommand
from pdrd_document_service.transport.http.schemas.pdf_annotation import (
    PdfAnnotatedDocumentRequest,
)
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = (
    ("api-gateway", "pdrd_api_gateway"),
    ("experience-service", "pdrd_experience_service"),
    ("document-service", "pdrd_document_service"),
)


def test_review_has_one_acceptance_action_without_area_dialogs():
    """Фронт принимает область через decide, отдельные подтверждения в UI не возвращаются."""
    feature = ROOT / "frontend/src/js/features/review"
    assert not (feature / "area-controls.js").exists()
    for file in feature.glob("*.js"):
        text = file.read_text(encoding="utf-8")
        assert "confirm_area" not in text, file
        assert "revoke_area" not in text, file
        assert "window.prompt" not in text, file


FRAMEWORKS = (
    "fastapi",
    "httpx",
    "pydantic",
    "sqlalchemy",
    "asyncpg",
    "fitz",
    "pymupdf",
)


def _imports(source: Path) -> set[str]:
    """Читает реальные AST-импорты, не принимая комментарии за зависимости."""
    names = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("service,package", PACKAGES)
@pytest.mark.parametrize("layer", ("domain", "application"))
def test_reviewed_pdf_keeps_domain_and_application_independent(service, package, layer):
    """Опыт, экспорт и PDF-контракты не зависят от HTTP, БД или PDF-библиотеки."""
    forbidden = (
        *FRAMEWORKS,
        f"{package}.infrastructure",
        f"{package}.transport",
        *((f"{package}.application",) if layer == "domain" else ()),
    )
    violations = []
    directory = ROOT / "services" / service / "src" / package / layer
    for source in directory.rglob("*.py"):
        for module in _imports(source):
            if any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in forbidden
            ):
                violations.append(f"{source.relative_to(ROOT)}: {module}")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("service,package", PACKAGES)
def test_reviewed_pdf_services_exchange_contracts_without_foreign_runtime(
    service, package
):
    """Сервис не импортирует чужие модели и адаптеры ради межсервисного обмена."""
    foreign = tuple(name for _, name in PACKAGES if name != package)
    violations = []
    for source in (ROOT / "services" / service / "src" / package).rglob("*.py"):
        for module in _imports(source):
            if any(module == name or module.startswith(name + ".") for name in foreign):
                violations.append(f"{source.relative_to(ROOT)}: {module}")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("service,package", PACKAGES[:2])
def test_pdf_mutation_remains_in_document_service(service, package):
    """Gateway и Experience не получают локальный PDF-рендерер."""
    violations = []
    for source in (ROOT / "services" / service / "src" / package).rglob("*.py"):
        if any(
            module.split(".")[0] in {"fitz", "pymupdf"} for module in _imports(source)
        ):
            violations.append(str(source.relative_to(ROOT)))
    assert not violations, "\n".join(violations)


def test_gateway_document_contract_preserves_gold_and_text_only_findings():
    """Обе области Gold сохраняют точность, а текст без области не создаёт аннотацию."""
    issue = AnalysisBoundingBox(10.125, 20.25, 200.5, 100.75)
    callout = AnalysisBoundingBox(400.125, 30.25, 900.5, 200.75)
    full_text = "Замечание инженера: полный текст; СП 485. " * 100
    manifest = ReviewedPdfManifest(
        job_id=UUID(int=1),
        document_id=UUID(int=2),
        source_filename="План.pdf",
        source_sha256="a" * 64,
        revision=8,
        digest="b" * 64,
        findings=(
            ReviewedPdfFinding(
                1,
                "manual:1",
                2,
                "manual",
                "gold",
                full_text,
                "СП 485",
                (issue,),
                callout,
            ),
            ReviewedPdfFinding(
                2, "vlm:1", 1, "vlm", "wise", "Принято без области", "", (), None
            ),
        ),
    )
    annotations, report = reviewed_payload(manifest)
    request = PdfAnnotatedDocumentRequest.model_validate(
        {
            "annotations": [annotation.as_dict() for annotation in annotations],
            "report": report.as_dict(),
        }
    )
    assert len(request.annotations) == 1
    annotation = request.annotations[0].to_domain()
    assert annotation.origin == "manual"
    assert annotation.number == 1
    assert annotation.page_number == 2
    assert annotation.regions[0].x_min == issue.x_min
    assert annotation.callout_box.x_max == callout.x_max
    assert full_text in annotation.content
    findings = request.report.to_domain().findings
    assert len(findings) == 2
    assert findings[0].origin == "manual"
    assert findings[0].fields[0].value == full_text
    assert findings[1].fields[0].value == "Принято без области"


def test_final_download_uses_closed_review_dependency_and_revision_only_request():
    """Экспорт закрыт тем же каналом, браузер передаёт только ожидаемую редакцию."""
    routes = [route for route in router.routes if route.path.endswith("/reviewed-pdf")]
    assert len(routes) == 1
    route = routes[0]
    assert route.methods == {"POST"}
    assert require_review_channel in {
        dependency.call for dependency in route.dependant.dependencies
    }
    assert set(StrictCommand.model_fields) == {"expected_revision"}
    assert set(inspect.signature(GetReviewedPdf.execute).parameters) == {
        "self",
        "job_id",
        "expected_revision",
        "actor",
    }
    with pytest.raises(ValidationError):
        StrictCommand.model_validate(
            {"expected_revision": 8, "actor": "browser:1", "regions": []}
        )


def test_export_timing_logger_is_enabled_once_and_does_not_propagate():
    """Timing экспорта виден в Uvicorn и не дублируется обработчиком root-логгера."""
    logger = logging.getLogger(GetReviewedPdf.__module__)
    related = logging.getLogger("pdrd_api_gateway.application.use_cases.manage_review")
    previous = {
        item: (item.handlers[:], item.level, item.propagate)
        for item in (logger, related)
    }
    try:
        configure_review_logging()
        configure_review_logging()
        assert (
            sum(
                bool(getattr(handler, "pdrd_review_timing", False))
                for handler in logger.handlers
            )
            == 1
        )
        assert logger.level == logging.INFO
        assert logger.propagate is False
        assert hasattr(GetReviewedPdf.execute, "__wrapped__")
        execute = ast.parse(inspect.getsource(GetReviewedPdf)).body[0]
        decorators = next(
            node.decorator_list
            for node in execute.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "execute"
        )
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "log_execution_time"
            and any(
                keyword.arg == "operation"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value == "reviewed_pdf_export"
                for keyword in node.keywords
            )
            for node in decorators
        )
    finally:
        for item, values in previous.items():
            item.handlers[:], item.level, item.propagate = values
