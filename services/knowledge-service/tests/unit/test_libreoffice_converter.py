# services/knowledge-service/tests/unit/test_libreoffice_converter.py

"""Unit tests LibreOffice Word normalization."""

import subprocess
from pathlib import Path
from typing import Any

import pytest
from pdrd_knowledge_service.application.ports.office_conversion import (
    NormativeOfficeConversionError,
)
from pdrd_knowledge_service.infrastructure.office.libreoffice import (
    LibreOfficeNormativeOfficeToPdfConverter,
)


class TimeoutProcess:
    """Fake Popen, который зависает на первом communicate."""

    pid = 12345

    returncode: int | None = None

    terminated = False

    def __init__(
        self,
    ) -> None:
        """Инициализирует communicate counter."""
        self._communicate_calls = 0

    def communicate(
        self,
        timeout: float | None = None,
    ) -> tuple[str, str]:
        """Первый вызов timeout, второй возвращает diagnostics."""
        del timeout

        self._communicate_calls += 1

        if self._communicate_calls == 1:
            raise subprocess.TimeoutExpired(
                cmd="soffice",
                timeout=1,
            )

        return (
            "writer8 started",
            "Warning: failed to launch javaldx",
        )

    def poll(
        self,
    ) -> int | None:
        """Возвращает текущий process status."""
        return self.returncode

    def wait(
        self,
        timeout: float | None = None,
    ) -> int:
        """Совместимый wait."""
        del timeout

        self.returncode = 255

        return self.returncode

    def terminate(
        self,
    ) -> None:
        """Совместимый terminate."""
        self.terminated = True

    def kill(
        self,
    ) -> None:
        """Совместимый kill."""
        self.terminated = True


def test_word_conversion_uses_odt_intermediate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DOCX сначала нормализуется в ODT, затем экспортируется в PDF."""
    stages: list[
        tuple[
            str,
            str,
            str,
        ]
    ] = []

    def fake_run_conversion(
        self: LibreOfficeNormativeOfficeToPdfConverter,
        *,
        source_path: Path,
        output_directory: Path,
        profile_directory: Path,
        home_directory: Path,
        convert_to: str,
        expected_output_path: Path,
        stage: str,
    ) -> None:
        del (
            self,
            output_directory,
            profile_directory,
            home_directory,
        )

        stages.append(
            (
                source_path.suffix.lower(),
                convert_to,
                stage,
            )
        )

        if expected_output_path.suffix == ".odt":
            expected_output_path.write_bytes(
                b"odt",
            )
            return

        expected_output_path.write_bytes(
            b"%PDF-1.6\nmock",
        )

    monkeypatch.setattr(
        LibreOfficeNormativeOfficeToPdfConverter,
        "_run_conversion",
        fake_run_conversion,
    )

    converter = LibreOfficeNormativeOfficeToPdfConverter()

    result = converter._convert_sync(
        b"docx-content",
        "technical-assignment.docx",
    )

    assert result.startswith(
        b"%PDF-",
    )

    assert stages == [
        (
            ".docx",
            "odt",
            "Word→ODT",
        ),
        (
            ".odt",
            "pdf:writer_pdf_Export",
            "ODT→PDF",
        ),
    ]


def test_conversion_timeout_terminates_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Timeout завершает process и возвращает stage diagnostics."""
    converter = LibreOfficeNormativeOfficeToPdfConverter(
        timeout_seconds=1,
    )

    source_path = tmp_path / "source.docx"

    source_path.write_bytes(
        b"docx",
    )

    output_directory = tmp_path / "output"
    profile_directory = tmp_path / "profile"
    home_directory = tmp_path / "home"

    for directory in (
        output_directory,
        profile_directory,
        home_directory,
    ):
        directory.mkdir()

    expected_output_path = output_directory / "source.odt"

    process = TimeoutProcess()

    popen_calls: list[
        dict[
            str,
            Any,
        ]
    ] = []

    def fake_popen(
        command: list[str],
        **kwargs: Any,
    ) -> TimeoutProcess:
        popen_calls.append(
            {
                "command": command,
                **kwargs,
            }
        )

        return process

    terminate_calls: list[TimeoutProcess] = []

    def fake_terminate_process(
        target: TimeoutProcess,
    ) -> None:
        terminate_calls.append(
            target,
        )

        target.terminated = True

    monkeypatch.setattr(
        "pdrd_knowledge_service.infrastructure.office.libreoffice.subprocess.Popen",
        fake_popen,
    )

    monkeypatch.setattr(
        converter,
        "_terminate_process",
        fake_terminate_process,
    )

    with pytest.raises(
        NormativeOfficeConversionError,
        match="этапе Word→ODT",
    ) as error_info:
        converter._run_conversion(
            source_path=source_path,
            output_directory=output_directory,
            profile_directory=profile_directory,
            home_directory=home_directory,
            convert_to="odt",
            expected_output_path=expected_output_path,
            stage="Word→ODT",
        )

    assert process.terminated is True

    assert terminate_calls == [
        process,
    ]

    assert "writer8 started" in caplog.text
    assert "javaldx" in caplog.text

    assert "writer8 started" not in str(
        error_info.value,
    )

    assert (
        len(
            popen_calls,
        )
        == 1
    )

    assert popen_calls[0]["command"][-1] == str(
        source_path,
    )
