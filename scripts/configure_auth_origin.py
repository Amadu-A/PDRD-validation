# scripts/configure_auth_origin.py

"""Настраивает один origin браузера в закрытом .env без вывода его секретов.

Принимается только HTTPS. Скрипт согласует адреса Auth/Gateway/email
и включает Secure cookie; контейнеры затем пересоздаются.
"""

import argparse
import os
import re
import shlex
import stat
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


def origin_settings(value: str) -> dict[str, str]:
    """Проверяет HTTPS-адрес без пути, встроенных паролей и управляющих символов."""
    if value != value.strip() or any(ord(char) < 32 for char in value):
        raise ValueError("Некорректный origin")
    parsed = urlsplit(value)
    try:
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        valid_port = False
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not valid_port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Укажите полный HTTPS origin без пути и параметров")
    origin = value.rstrip("/")
    return {
        "AUTH_SERVICE_HTTP__PUBLIC_ORIGIN": origin,
        "AUTH_SERVICE_EMAIL__PUBLIC_BASE_URL": origin,
        "API_GATEWAY_IDENTITY_PROXY__PUBLIC_ORIGIN": origin,
        "AUTH_SERVICE_HTTP__COOKIE_SECURE": "true",
    }


def configure_env(path: Path, values: dict[str, str]) -> None:
    """Атомарно меняет только настройки origin/cookie, сохраняя остальные строки."""
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    remaining = dict(values)
    updated = []
    pattern = re.compile(r"^(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=")
    for line in lines:
        match = pattern.match(line)
        key = match.group(1) if match else None
        if key in values:
            if key in remaining:
                updated.append(f"{key}={shlex.quote(remaining.pop(key))}")
        else:
            updated.append(line)
    updated.extend(f"{key}={shlex.quote(value)}" for key, value in remaining.items())
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=".env.origin-",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write("\n".join(updated) + "\n")
        temporary.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    """Изменяет закрытый .env и выводит только выбранный публичный адрес."""
    parser = argparse.ArgumentParser(
        description="Настроить адрес браузерного входа PDRD"
    )
    parser.add_argument("origin")
    args = parser.parse_args()
    try:
        values = origin_settings(args.origin)
        configure_env(Path(__file__).resolve().parents[1] / ".env", values)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Настройка не выполнена: {error}\n")
    print(f"Origin настроен: {values['AUTH_SERVICE_HTTP__PUBLIC_ORIGIN']}")
    print("Теперь пересоздайте api-gateway, auth-service, admin-service и frontend.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
