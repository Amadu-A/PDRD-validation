# services/document-service/src/pdrd_document_service/core/container.py

"""Composition root Document Service."""

from dataclasses import dataclass

from pdrd_document_service.application.use_cases.cad import (
    ExtractCadDocument,
)
from pdrd_document_service.application.use_cases.extract import (
    ExtractPdfDocument,
)
from pdrd_document_service.core.settings import (
    Settings,
    get_settings,
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
from pdrd_document_service.infrastructure.pdf.pymupdf import (
    PyMuPdfReader,
)


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Хранит runtime dependencies Document Service."""

    settings: Settings

    extract_pdf: ExtractPdfDocument
    extract_cad: ExtractCadDocument


def build_container() -> ApplicationContainer:
    """Собирает concrete dependencies сервиса."""
    settings = get_settings()

    pdf_reader = PyMuPdfReader(
        render_max_side=(settings.pdf.render_max_side),
        text_limit=settings.pdf.text_limit,
    )

    extract_pdf = ExtractPdfDocument(
        reader=pdf_reader,
        max_upload_bytes=(settings.pdf.max_upload_bytes),
        max_analysis_pages=(settings.pdf.max_analysis_pages),
    )

    cad_normalizer = LibreDwgNormalizer(
        converter_command=(settings.cad.dwg_converter_command),
        converter_timeout_seconds=(settings.cad.dwg_converter_timeout_seconds),
    )

    cad_parser = EzdxfCadParser(
        text_sample_limit=(settings.cad.text_sample_limit),
        block_sample_limit=(settings.cad.block_sample_limit),
        dangling_sample_limit=(settings.cad.dangling_sample_limit),
        connectivity_tolerance=(settings.cad.connectivity_tolerance),
        virtual_insert_depth=(settings.cad.virtual_insert_depth),
    )

    cad_renderer = EzdxfCadRenderer(
        render_dpi=settings.cad.render_dpi,
        render_max_side=(settings.cad.render_max_side),
    )

    cad_processor = EzdxfCadProcessor(
        normalizer=cad_normalizer,
        parser=cad_parser,
        renderer=cad_renderer,
        machine_text_limit=(settings.cad.machine_text_limit),
    )

    extract_cad = ExtractCadDocument(
        processor=cad_processor,
        max_upload_bytes=(settings.cad.max_upload_bytes),
    )

    return ApplicationContainer(
        settings=settings,
        extract_pdf=extract_pdf,
        extract_cad=extract_cad,
    )
