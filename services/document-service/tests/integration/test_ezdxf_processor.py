# services/document-service/tests/integration/test_ezdxf_processor.py

"""Integration-тест полного DXF pipeline."""

import io

import ezdxf
from pdrd_document_service.domain.cad import CadFormat
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


def build_dxf() -> bytes:
    """Создаёт DXF с линиями и текстом в памяти."""
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
            100,
            0,
        ),
    )

    modelspace.add_line(
        (
            100,
            0,
        ),
        (
            100,
            100,
        ),
    )

    text = modelspace.add_text(
        "CAB-01",
    )

    text.dxf.insert = (
        20,
        20,
    )

    stream = io.StringIO()

    document.write(
        stream,
    )

    return stream.getvalue().encode(
        "utf-8",
    )


def build_processor() -> EzdxfCadProcessor:
    """Создаёт processor для DXF integration test."""
    return EzdxfCadProcessor(
        normalizer=LibreDwgNormalizer(
            converter_command=("missing-dwg2dxf-for-test"),
            converter_timeout_seconds=1,
        ),
        parser=EzdxfCadParser(
            text_sample_limit=100,
            block_sample_limit=100,
            dangling_sample_limit=100,
            connectivity_tolerance=0.5,
            virtual_insert_depth=2,
        ),
        renderer=EzdxfCadRenderer(
            render_dpi=72,
            render_max_side=1000,
        ),
        machine_text_limit=14000,
    )


def test_processor_extracts_geometry_text_and_png() -> None:
    """Проверяет полный pipeline DXF."""
    processor = build_processor()

    result = processor.process(
        build_dxf(),
        filename="drawing.dxf",
    )

    assert result.original_format is CadFormat.DXF
    assert result.normalized_format is CadFormat.DXF

    assert result.converted_from_dwg is False

    assert result.selected_layout.lower() == "model"

    assert result.machine_data["entity_counts"]["LINE"] == 2

    assert result.machine_data["entity_counts"]["TEXT"] == 1

    texts = result.machine_data["texts"]

    assert any(text["text"] == "CAB-01" for text in texts)

    geometry = result.machine_data["geometry"]

    assert geometry["segment_count"] == 2

    assert geometry["endpoint_node_count"] == 3

    assert geometry["dangling_endpoint_count"] == 2

    assert result.rendered_png.startswith(
        b"\x89PNG\r\n\x1a\n",
    )

    assert result.machine_context
