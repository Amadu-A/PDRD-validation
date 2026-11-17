# services/document-service/src/pdrd_document_service/core/container.py

"""Composition root Document Service."""

from dataclasses import dataclass

from pdrd_document_service.application.use_cases.annotate import (
    BuildAnnotatedPdf,
)
from pdrd_document_service.application.use_cases.cad import (
    ExtractCadDocument,
)
from pdrd_document_service.application.use_cases.combined import (
    ExtractCombinedDocument,
)
from pdrd_document_service.application.use_cases.crop_pdf import CropPdf
from pdrd_document_service.application.use_cases.enrich_document import EnrichDocument
from pdrd_document_service.application.use_cases.enrich_specification import (
    EnrichSpecification,
)
from pdrd_document_service.application.use_cases.equipment_document import (
    ExtractEquipmentDocument,
)
from pdrd_document_service.application.use_cases.equipment_report import (
    ExportEquipmentReport,
)
from pdrd_document_service.application.use_cases.extract import (
    ExtractPdfDocument,
)
from pdrd_document_service.application.use_cases.project_context import (
    ExtractPdfProjectContext,
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
from pdrd_document_service.infrastructure.docling import HttpDoclingConverter
from pdrd_document_service.infrastructure.equipment_document import (
    PyMuPdfEquipmentDocumentReader,
)
from pdrd_document_service.infrastructure.extraction_artifacts import (
    FilesystemExtractionArtifacts,
)
from pdrd_document_service.infrastructure.image_composer import (
    PillowCombinedImageComposer,
)
from pdrd_document_service.infrastructure.pdf.annotator import (
    PyMuPdfAnnotationWriter,
)
from pdrd_document_service.infrastructure.pdf.crop import PyMuPdfCropRenderer
from pdrd_document_service.infrastructure.pdf.equipment_report import (
    PyMuPdfEquipmentReportWriter,
)
from pdrd_document_service.infrastructure.pdf.pymupdf import (
    PyMuPdfReader,
)
from pdrd_document_service.infrastructure.specification_extraction import (
    HttpSpecificationExtractor,
)


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Хранит runtime dependencies Document Service."""

    settings: Settings

    extract_pdf: ExtractPdfDocument
    extract_cad: ExtractCadDocument
    extract_combined: ExtractCombinedDocument

    extract_pdf_project_context: ExtractPdfProjectContext | None = None

    build_annotated_pdf: BuildAnnotatedPdf | None = None
    enrich_specification: EnrichSpecification | None = None
    enrich_document: EnrichDocument | None = None
    crop_pdf: CropPdf | None = None
    extract_equipment_document: ExtractEquipmentDocument | None = None
    export_equipment_report: ExportEquipmentReport | None = None


def build_container() -> ApplicationContainer:
    """Собирает concrete dependencies сервиса."""
    settings = get_settings()

    pdf_reader = PyMuPdfReader(
        render_max_side=(settings.pdf.render_max_side),
        text_limit=(settings.pdf.text_limit),
    )

    extract_pdf = ExtractPdfDocument(
        reader=pdf_reader,
        max_upload_bytes=(settings.pdf.max_upload_bytes),
        max_analysis_pages=(settings.pdf.max_analysis_pages),
    )

    extract_pdf_project_context = ExtractPdfProjectContext(
        reader=pdf_reader,
        max_upload_bytes=(settings.pdf.max_upload_bytes),
        max_context_pages=(settings.pdf.max_context_pages),
    )

    build_annotated_pdf = BuildAnnotatedPdf(
        writer=PyMuPdfAnnotationWriter(),
        max_upload_bytes=(settings.pdf.max_upload_bytes),
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
        render_dpi=(settings.cad.render_dpi),
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

    image_composer = PillowCombinedImageComposer(
        max_side=max(
            settings.pdf.render_max_side,
            settings.cad.render_max_side,
        ),
    )

    extract_combined = ExtractCombinedDocument(
        extract_pdf=extract_pdf,
        extract_cad=extract_cad,
        image_composer=image_composer,
    )

    return ApplicationContainer(
        settings=settings,
        extract_pdf=extract_pdf,
        extract_cad=extract_cad,
        extract_combined=extract_combined,
        extract_pdf_project_context=(extract_pdf_project_context),
        build_annotated_pdf=(build_annotated_pdf),
        enrich_specification=EnrichSpecification(
            HttpSpecificationExtractor(
                settings=settings.specification_service,
                artifacts=FilesystemExtractionArtifacts(
                    settings.specification_service.artifacts_path
                ),
            ),
            allow_native_fallback=settings.specification_service.allow_native_fallback,
            artifacts=FilesystemExtractionArtifacts(
                settings.specification_service.artifacts_path
            ),
        )
        if settings.specification_service.enabled
        else None,
        enrich_document=EnrichDocument(
            HttpDoclingConverter(
                settings=settings.docling,
                artifacts=FilesystemExtractionArtifacts(
                    settings.docling.artifacts_path
                ),
            ),
            concurrency=settings.docling.concurrency,
        )
        if settings.docling.enabled
        else None,
        crop_pdf=CropPdf(PyMuPdfCropRenderer(), settings.pdf.max_upload_bytes),
        export_equipment_report=ExportEquipmentReport(PyMuPdfEquipmentReportWriter()),
        extract_equipment_document=ExtractEquipmentDocument(
            PyMuPdfEquipmentDocumentReader(),
        ),
    )
