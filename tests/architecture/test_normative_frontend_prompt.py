# tests/architecture/test_normative_frontend_prompt.py

"""Architecture tests prompt editor, Word upload и job-id UX."""

from pathlib import Path

REPOSITORY_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

FRONTEND = REPOSITORY_ROOT / "frontend" / "src"

INDEX_HTML = FRONTEND / "index.html"

FORM_JS = FRONTEND / "js" / "features" / "analysis" / "form.js"

REPORT_JS = FRONTEND / "js" / "features" / "analysis" / "report.js"

CATALOG_JS = FRONTEND / "js" / "features" / "normative" / "catalog.js"

PROMPT_JS = FRONTEND / "js" / "features" / "normative" / "prompt.js"

MODAL_JS = FRONTEND / "js" / "components" / "modal.js"

KNOWLEDGE_DOCKERFILE = REPOSITORY_ROOT / "services" / "knowledge-service" / "Dockerfile"


def test_prompt_editor_hooks_exist() -> None:
    """HTML содержит stable prompt editor hooks."""
    content = INDEX_HTML.read_text(
        encoding="utf-8",
    )

    required = (
        "data-normative-prompt",
        "data-normative-prompt-save",
        "data-normative-prompt-restore",
        "data-normative-prompt-status",
    )

    missing = [hook for hook in required if hook not in content]

    assert not missing, "\n".join(
        missing,
    )


def test_exact_working_prompt_goes_to_analysis_form() -> None:
    """Working prompt входит в immutable snapshot multipart."""
    prompt_content = PROMPT_JS.read_text(
        encoding="utf-8",
    )

    form_content = FORM_JS.read_text(
        encoding="utf-8",
    )

    assert "promptOverrideEnabled: true" in prompt_content

    assert '"normative_prompt_override_enabled"' in form_content

    assert '"normative_prompt_override"' in form_content


def test_normative_upload_accepts_pdf_doc_and_docx() -> None:
    """Frontend разрешает три формата нормативной базы."""
    html = INDEX_HTML.read_text(
        encoding="utf-8",
    )

    catalog = CATALOG_JS.read_text(
        encoding="utf-8",
    )

    for extension in (
        ".pdf",
        ".doc",
        ".docx",
    ):
        assert extension in html
        assert extension in catalog


def test_category_has_explicit_upload_action() -> None:
    """Папка имеет явную загрузку, а не только implicit drop."""
    content = CATALOG_JS.read_text(
        encoding="utf-8",
    )

    assert "data.normativeCategoryUpload" not in content

    assert "normativeCategoryUpload" in content

    assert "openCategoryFilePicker" in content

    assert "Добавить PDF/DOC/DOCX в эту папку" in content


def test_job_id_is_visible_and_copyable() -> None:
    """Modal содержит persistent job-id UX."""
    html = INDEX_HTML.read_text(
        encoding="utf-8",
    )

    modal = MODAL_JS.read_text(
        encoding="utf-8",
    )

    required_hooks = (
        "data-analysis-modal-job",
        "data-analysis-modal-job-id",
        "data-analysis-modal-copy",
    )

    for hook in required_hooks:
        assert hook in html

    assert "setJobId" in modal

    assert "copyText" in modal


def test_final_report_preserves_job_id() -> None:
    """Финальный результат не теряет номер задания."""
    content = REPORT_JS.read_text(
        encoding="utf-8",
    )

    assert "jobId = null" in content

    assert "`Задание: ${jobId}`" in content


def test_knowledge_image_contains_libreoffice_writer() -> None:
    """Knowledge runtime имеет Word → PDF converter."""
    content = KNOWLEDGE_DOCKERFILE.read_text(
        encoding="utf-8",
    )

    assert "libreoffice-writer" in content
