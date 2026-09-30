# ops/prepare-experience-index.py

"""Готовит read-only ключ E после quality gate, сохраняя выключенный рабочий поиск.

Другие настройки .env сохраняются. Ключ не печатается и не попадает в Git;
файл заменяется атомарно с исходными правами.
"""

import os
import re
import secrets
from pathlib import Path


def prepare(path: Path) -> None:
    """Сохраняет существующий ключ либо создаёт его без переопределения остальных секретов."""
    lines = path.read_text(encoding="utf-8").splitlines()
    keys = {}
    for index, line in enumerate(lines):
        name, separator, value = line.partition("=")
        name = name.strip()
        if separator and name in {
            "PDRD_EXPERIENCE_INDEX_KEY",
            "KNOWLEDGE_SERVICE_SEARCH__EXPERIENCE_ENABLED",
        }:
            if name in keys:
                raise ValueError("В .env дублируется настройка индекса E.")
            keys[name] = (index, value.strip().strip("\"'"))
    current = keys.get("PDRD_EXPERIENCE_INDEX_KEY", (None, ""))[1]
    if current and not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", current):
        raise ValueError(
            "Некорректный существующий ключ E; требуется от 32 до 256 символов."
        )
    values = {
        "PDRD_EXPERIENCE_INDEX_KEY": current or secrets.token_hex(32),
        "KNOWLEDGE_SERVICE_SEARCH__EXPERIENCE_ENABLED": "false",
    }
    for name, value in values.items():
        if name in keys:
            lines[keys[name][0]] = f"{name}={value}"
        else:
            lines.append(f"{name}={value}")
    temporary = path.with_name(path.name + ".experience-index.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write("\n".join(lines) + "\n")
        os.chmod(temporary, path.stat().st_mode & 0o777)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    print("Канал индекса E подготовлен. Рабочий поиск E выключен.")


if __name__ == "__main__":
    prepare(Path(__file__).resolve().parents[1] / ".env")
