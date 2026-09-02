# services/document-service/tests/unit/test_cad_domain.py

"""Unit-тесты CAD domain rules."""

import pytest
from pdrd_document_service.domain.cad import (
    CadFormat,
    InvalidCadFilenameError,
    detect_cad_format,
)


@pytest.mark.parametrize(
    (
        "filename",
        "expected",
    ),
    [
        (
            "drawing.dxf",
            CadFormat.DXF,
        ),
        (
            "drawing.DXF",
            CadFormat.DXF,
        ),
        (
            "drawing.dwg",
            CadFormat.DWG,
        ),
        (
            "drawing.DWG",
            CadFormat.DWG,
        ),
    ],
)
def test_detect_cad_format(
    filename: str,
    expected: CadFormat,
) -> None:
    """Проверяет определение DWG/DXF."""
    assert (
        detect_cad_format(
            filename,
        )
        is expected
    )


@pytest.mark.parametrize(
    "filename",
    [
        None,
        "",
        "drawing.pdf",
        "drawing.txt",
        "drawing",
    ],
)
def test_detect_cad_format_rejects_invalid_file(
    filename: str | None,
) -> None:
    """Проверяет отказ для неподдерживаемого filename."""
    with pytest.raises(
        InvalidCadFilenameError,
    ):
        detect_cad_format(
            filename,
        )
