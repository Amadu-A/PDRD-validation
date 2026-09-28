# ops/configure-review.py

"""Явно включает закрытый Review в приватном .env без вывода ключей.

Назначение: подготовить проверку этапа 5 через localhost/SSH. Сохраняет
существующие настройки и ключи, создаёт два разных случайных служебных ключа
только при их отсутствии. Не запускает контейнеры, миграции или обучение.
"""

import argparse
import os
import re
import secrets
import tempfile
from pathlib import Path

KEY_PATTERN = r"[A-Za-z0-9_-]{32,256}"


def values(text: str) -> dict[str, str]:
    """Читает нужные простые значения .env, оставляя исходный файл нетронутым."""
    result = {}
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        result[name.strip()] = value.split(" #", 1)[0].strip().strip("\"'")
    return result


def configure(root: Path, actor: str | None = None) -> None:
    """Атомарно меняет только настройки закрытого Review существующего проекта."""
    root = root.resolve()
    target = root / ".env"
    if target.is_symlink() or target.resolve().parent != root:
        raise ValueError(".env должен быть обычным файлом в корне проекта.")
    original = target.read_text(encoding="utf-8")
    current = values(original)
    if current.get("API_GATEWAY_ENVIRONMENT", "local") == "prod":
        raise ValueError("Закрытый режим этапа 5 не заменяет production-авторизацию.")
    ui = current.get("API_GATEWAY_REVIEW__UI_KEY") or secrets.token_hex(32)
    internal = current.get("API_GATEWAY_REVIEW__INTERNAL_KEY") or secrets.token_hex(32)
    if ui == internal or any(
        not re.fullmatch(KEY_PATTERN, key) for key in (ui, internal)
    ):
        raise ValueError(
            "Нужны два разных служебных ключа из букв, цифр, _ или -, длиной 32..256."
        )
    actual_actor = (
        actor
        or current.get("API_GATEWAY_REVIEW__ACTOR")
        or "controlled-review-operator"
    )
    if not re.fullmatch(r"[A-Za-z0-9:@._-]{1,128}", actual_actor):
        raise ValueError(
            "Укажите серверный идентификатор инженера из букв, цифр, :, @, ., _ или -."
        )
    updates = {
        "API_GATEWAY_REVIEW__ENABLED": "true",
        "API_GATEWAY_REVIEW__CONTROLLED_ACCESS": "true",
        "API_GATEWAY_REVIEW__ACTOR": actual_actor,
        "API_GATEWAY_REVIEW__UI_KEY": ui,
        "API_GATEWAY_REVIEW__INTERNAL_KEY": internal,
    }
    lines = [
        line
        for line in original.splitlines()
        if line.split("=", 1)[0].strip() not in updates
    ]
    lines.extend(f"{name}={value}" for name, value in updates.items())
    descriptor, filename = tempfile.mkstemp(dir=root, prefix=".env.review-")
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write("\n".join(lines) + "\n")
        temporary.chmod(0o600)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    """Подготавливает только .env проекта, в котором расположен этот файл."""
    parser = argparse.ArgumentParser(
        description="Настройка закрытого Human Review без выдачи ключей браузеру."
    )
    parser.add_argument(
        "--actor", help="Серверный идентификатор инженера закрытого окружения."
    )
    options = parser.parse_args()
    configure(Path(__file__).resolve().parents[1], options.actor)
    print("Закрытый Review настроен. Ключи сохранены в .env и не выводятся.")


if __name__ == "__main__":
    main()
