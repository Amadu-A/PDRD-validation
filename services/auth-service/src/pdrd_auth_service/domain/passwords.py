# services/auth-service/src/pdrd_auth_service/domain/passwords.py

"""Медленное хеширование паролей внешних аккаунтов стандартным scrypt."""

import base64
import binascii
import hashlib
import hmac
import secrets

_N = 2**15
_R = 8
_P = 3
_MAXMEM = 128 * 1024 * 1024
_DUMMY_SALT = b"pdrd-dummy-salt!"


def validate_password(password: str) -> None:
    """Ограничивает размер ввода до выделения памяти для scrypt."""
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError("Пароль должен содержать от 12 до 1024 символов")
    if len(password.encode("utf-8")) > 4096:
        raise ValueError("Пароль слишком длинный")


def _derive(password: str, salt: bytes) -> bytes:
    """Вычисляет одинаковую стоимость для регистрации и проверки."""
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_N,
        r=_R,
        p=_P,
        maxmem=_MAXMEM,
        dklen=32,
    )


def hash_password(password: str) -> str:
    """Сохраняет версию алгоритма, параметры, случайную соль и результат."""
    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt)
    return (
        f"scrypt${_N}${_R}${_P}$"
        f"{base64.urlsafe_b64encode(salt).decode('ascii')}$"
        f"{base64.urlsafe_b64encode(digest).decode('ascii')}"
    )


def verify_password(password: str, encoded: str | None) -> bool:
    """Сравнивает хеш без короткого пути для несуществующего email."""
    try:
        validate_password(password)
    except (TypeError, ValueError):
        return False
    if encoded is None:
        _derive(password, _DUMMY_SALT)
        return False
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$")
        if (algorithm, int(n), int(r), int(p)) != ("scrypt", _N, _R, _P):
            return False
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(digest_text)
        if len(salt) != 16 or len(expected) != 32:
            return False
    except (AttributeError, ValueError, binascii.Error):
        return False
    return hmac.compare_digest(_derive(password, salt), expected)
