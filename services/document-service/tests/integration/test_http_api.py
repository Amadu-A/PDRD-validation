# services/document-service/tests/integration/test_http_api.py

"""HTTP integration tests Document Service."""

import base64
import io

import ezdxf
import fitz
from fastapi.testclient import TestClient
from pdrd_document_service.application.use_cases.cad import (
    ExtractCadDocument,
)
from pdrd_document_service.application.use_cases.combined import (
    ExtractCombinedDocument,
)
from pdrd_document_service.application.use_cases.extract import (
    ExtractPdfDocument,
)
from pdrd_document_service.core.container import (
    ApplicationContainer,
)
from pdrd_document_service.core.settings import (
    CadSettings,
    PdfSettings,
    Settings,
)
from pdrd_document_service.infrastructure.cad.converter import (
    LibreDwgNormalizer,
)
from pdrd_document_service.infrastructure.cad.parser import (
    EzdxfCadParser,
)
from pdrd_document_service.infrastructure.cad.processor import (
    EzdxfCadProcessor,
)
from pdrd_document_service.infrastructure.cad.renderer import (
    EzdxfCadRenderer,
)
from pdrd_document_service.infrastructure.image_composer import (
    PillowCombinedImageComposer,
)
from pdrd_document_service.infrastructure.pdf.pymupdf import (
    PyMuPdfReader,
)
from pdrd_document_service.main import create_app


def build_pdf() -> bytes:
    """Создаёт тестовый PDF в памяти."""
    document = fitz.open()

    first_page = document.new_page()

    first_page.insert_text(
        (
            72,
            72,
        ),
        "First page",
    )

    second_page = document.new_page()

    second_page.insert_text(
        (
            72,
            72,
        ),
        "Second page",
    )

    content = document.tobytes()

    document.close()

    return content


def build_dxf() -> bytes:
    """Создаёт тестовый DXF в памяти."""
    document = ezdxf.new(
        "R2018",
    )

    modelspace = document.modelspace()

    modelspace.add_line(
        (
            0,
            0,
        ),
        (
            50,
            0,
        ),
    )

    text = modelspace.add_text(
        "TEST-CAD",
    )

    text.dxf.insert = (
        10,
        10,
    )

    stream = io.StringIO()

    document.write(
        stream,
    )

    return stream.getvalue().encode(
        "utf-8",
    )


def build_client() -> TestClient:
    """Создаёт Document Service test client."""
    pdf_settings = PdfSettings(
        max_upload_mb=10,
        render_max_side=1000,
        max_analysis_pages=10,
        text_limit=12000,
    )

    cad_settings = CadSettings(
        max_upload_mb=10,
        dwg_converter_command=("missing-dwg2dxf-for-http-test"),
        dwg_converter_timeout_seconds=1,
        render_dpi=72,
        render_max_side=1000,
        machine_text_limit=5000,
        text_sample_limit=100,
        block_sample_limit=100,
        dangling_sample_limit=100,
        connectivity_tolerance=0.5,
        virtual_insert_depth=2,
    )

    settings = Settings(
        _env_file=None,
        service_name=("PDRD Document Service Test"),
        service_version="0.1.0-test",
        environment="test",
        pdf=pdf_settings,
        cad=cad_settings,
    )

    pdf_reader = PyMuPdfReader(
        render_max_side=(pdf_settings.render_max_side),
        text_limit=(pdf_settings.text_limit),
    )

    extract_pdf = ExtractPdfDocument(
        reader=pdf_reader,
        max_upload_bytes=(pdf_settings.max_upload_bytes),
        max_analysis_pages=(pdf_settings.max_analysis_pages),
    )

    cad_processor = EzdxfCadProcessor(
        normalizer=LibreDwgNormalizer(
            converter_command=(cad_settings.dwg_converter_command),
            converter_timeout_seconds=(cad_settings.dwg_converter_timeout_seconds),
        ),
        parser=EzdxfCadParser(
            text_sample_limit=(cad_settings.text_sample_limit),
            block_sample_limit=(cad_settings.block_sample_limit),
            dangling_sample_limit=(cad_settings.dangling_sample_limit),
            connectivity_tolerance=(cad_settings.connectivity_tolerance),
            virtual_insert_depth=(cad_settings.virtual_insert_depth),
        ),
        renderer=EzdxfCadRenderer(
            render_dpi=(cad_settings.render_dpi),
            render_max_side=(cad_settings.render_max_side),
        ),
        machine_text_limit=(cad_settings.machine_text_limit),
    )

    extract_cad = ExtractCadDocument(
        processor=cad_processor,
        max_upload_bytes=(cad_settings.max_upload_bytes),
    )

    extract_combined = ExtractCombinedDocument(
        extract_pdf=extract_pdf,
        extract_cad=extract_cad,
        image_composer=(
            PillowCombinedImageComposer(
                max_side=1000,
            )
        ),
    )

    container = ApplicationContainer(
        settings=settings,
        extract_pdf=extract_pdf,
        extract_cad=extract_cad,
        extract_combined=extract_combined,
    )

    return TestClient(
        create_app(
            container=container,
        )
    )


def test_health_endpoints() -> None:
    """Проверяет health contracts."""
    with build_client() as client:
        live_response = client.get(
            "/health/live",
        )

        ready_response = client.get(
            "/health/ready",
        )

    assert live_response.status_code == 200

    assert live_response.json() == {
        "status": "ok",
        "service": ("PDRD Document Service Test"),
        "version": "0.1.0-test",
    }

    assert ready_response.status_code == 200

    assert ready_response.json() == {
        "status": "ready",
        "service": ("PDRD Document Service Test"),
        "version": "0.1.0-test",
        "capabilities": {
            "pdf": True,
            "dxf": True,
            "dwg": False,
        },
    }


def test_extract_pdf_endpoint() -> None:
    """Проверяет полный HTTP PDF extraction contract."""
    with build_client() as client:
        response = client.post(
            "/internal/v1/pdf/extract",
            files={
                "file": (
                    "test.pdf",
                    build_pdf(),
                    "application/pdf",
                ),
            },
            data={
                "pages": "2",
            },
        )

    assert response.status_code == 200

    payload = response.json()

    assert payload["file_name"] == "test.pdf"

    assert payload["total_pages"] == 2

    assert payload["selected_pages"] == [
        2,
    ]

    assert (
        len(
            payload["pages"],
        )
        == 1
    )

    page = payload["pages"][0]

    assert page["page_number"] == 2

    assert "Second page" in page["text"]

    png = base64.b64decode(
        page["image_base64"],
    )

    assert png.startswith(
        b"\x89PNG\r\n\x1a\n",
    )


def test_extract_pdf_rejects_invalid_page() -> None:
    """Проверяет validation page selection через HTTP."""
    with build_client() as client:
        response = client.post(
            "/internal/v1/pdf/extract",
            files={
                "file": (
                    "test.pdf",
                    build_pdf(),
                    "application/pdf",
                ),
            },
            data={
                "pages": "100",
            },
        )

    assert response.status_code == 422


def test_extract_cad_endpoint() -> None:
    """Проверяет полный HTTP DXF extraction contract."""
    with build_client() as client:
        response = client.post(
            "/internal/v1/cad/extract",
            files={
                "file": (
                    "drawing.dxf",
                    build_dxf(),
                    "application/dxf",
                ),
            },
        )

    assert response.status_code == 200

    payload = response.json()

    assert payload["original_file_name"] == "drawing.dxf"

    assert payload["original_format"] == "dxf"

    assert payload["normalized_format"] == "dxf"

    assert payload["converted_from_dwg"] is False

    assert payload["machine_data"]["entity_counts"]["LINE"] == 1

    png = base64.b64decode(
        payload["image_base64"],
    )

    assert png.startswith(
        b"\x89PNG\r\n\x1a\n",
    )


def test_extract_cad_rejects_wrong_extension() -> None:
    """Проверяет отказ для неподдерживаемого CAD extension."""
    with build_client() as client:
        response = client.post(
            "/internal/v1/cad/extract",
            files={
                "file": (
                    "drawing.pdf",
                    b"not-cad",
                    "application/pdf",
                ),
            },
        )

    assert response.status_code == 422


def test_extract_combined_endpoint() -> None:
    """Проверяет полный HTTP PDF + CAD extraction contract."""
    with build_client() as client:
        response = client.post(
            "/internal/v1/combined/extract",
            files={
                "pdf": (
                    "test.pdf",
                    build_pdf(),
                    "application/pdf",
                ),
                "cad": (
                    "drawing.dxf",
                    build_dxf(),
                    "application/dxf",
                ),
            },
            data={
                "pages": "2",
            },
        )

    assert response.status_code == 200

    payload = response.json()

    assert payload["pdf_file_name"] == "test.pdf"

    assert payload["cad_file_name"] == "drawing.dxf"

    assert payload["total_pdf_pages"] == 2

    assert payload["selected_page"] == 2

    assert payload["pdf"]["page_number"] == 2

    assert "Second page" in payload["analysis_text"]

    assert "TEST-CAD" in payload["analysis_text"]

    assert payload["cad"]["original_format"] == "dxf"

    for image_field in (
        payload["pdf"]["image_base64"],
        payload["cad"]["image_base64"],
        payload["combined_image_base64"],
    ):
        png = base64.b64decode(
            image_field,
        )

        assert png.startswith(
            b"\x89PNG\r\n\x1a\n",
        )


def test_extract_combined_requires_one_pdf_page() -> None:
    """Проверяет требование одного PDF-листа для PDF + CAD."""
    with build_client() as client:
        response = client.post(
            "/internal/v1/combined/extract",
            files={
                "pdf": (
                    "test.pdf",
                    build_pdf(),
                    "application/pdf",
                ),
                "cad": (
                    "drawing.dxf",
                    build_dxf(),
                    "application/dxf",
                ),
            },
            data={
                "pages": "1-2",
            },
        )

    assert response.status_code == 422

    assert "ровно одну PDF-страницу" in response.json()["detail"]
