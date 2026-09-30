# services/auth-service/tests/unit/test_identity.py

"""Регрессия устойчивого ключа AD-пользователя без хранения пароля."""

from dataclasses import fields
from uuid import UUID

import pytest
from pdrd_auth_service.domain.identity import CorporateIdentity


def identity(**changes: str) -> CorporateIdentity:
    """Создаёт тестовую личность с каноническим objectGUID."""
    values = {
        "provider_id": "active_directory",
        "namespace": "itcneoterm.local",
        "subject": str(UUID("579a4378-732e-4b46-823f-2b79d49d0a2a")),
        "login": "i.mein",
        "display_name": "Иван Мейн",
        "email": "ivan@example.test",
    }
    values.update(changes)
    return CorporateIdentity(**values)


def test_stable_key_ignores_mutable_profile_fields() -> None:
    """Смена имени и логина в AD не должна создавать новый профиль PDRD."""
    original = identity()
    renamed = identity(login="ivan.mein", display_name="Иван Иванов", email=None)

    assert original.stable_key == renamed.stable_key
    assert original.stable_key[0] == "active_directory"
    assert "password" not in {field.name for field in fields(CorporateIdentity)}


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("provider_id", "local_email"),
        ("namespace", "ITCNEOTERM.LOCAL"),
        ("subject", "not-an-object-guid"),
        ("subject", "579A4378-732E-4B46-823F-2B79D49D0A2A"),
        ("login", " "),
        ("display_name", ""),
        ("email", ""),
    ],
)
def test_incomplete_or_noncanonical_identity_is_rejected(
    change: str, value: str
) -> None:
    """Отвергает неполную AD-личность до вызова user-service."""
    with pytest.raises(ValueError):
        identity(**{change: value})
