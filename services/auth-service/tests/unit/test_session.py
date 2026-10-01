# services/auth-service/tests/unit/test_session.py

"""Проверяет хронологию, сроки и ограничения серверной сессии."""

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pdrd_auth_service.domain.session import AuthSession, SessionPolicy

NOW = datetime(2026, 9, 30, 9, tzinfo=UTC)


def session() -> AuthSession:
    """Создаёт обычную сессию с двухчасовым сроком простоя."""
    return AuthSession(
        session_id=UUID("9503bb4c-e7e4-44b4-b946-d281010386b0"),
        user_id=UUID("9114be7f-220d-46a1-ab8d-09f632e0340a"),
        token_hash="a" * 64,
        authorization_version=2,
        created_at=NOW,
        last_seen_at=NOW,
        idle_expires_at=NOW + timedelta(hours=2),
        absolute_expires_at=NOW + timedelta(hours=8),
    )


def test_normal_session_expires_on_idle_and_absolute_boundaries() -> None:
    """Оба срока имеют строгую границу и не зависят от браузера."""
    value = session()
    assert value.is_active(NOW + timedelta(hours=1))
    assert not value.is_active(NOW + timedelta(hours=2))
    assert not value.is_active(NOW + timedelta(hours=8))


def test_renewal_never_moves_absolute_expiry() -> None:
    """Активность продлевает простой, но не превращается в бесконечную сессию."""
    value = session().renewed(NOW + timedelta(hours=1), timedelta(hours=2))
    assert value.idle_expires_at == NOW + timedelta(hours=3)
    assert value.absolute_expires_at == NOW + timedelta(hours=8)

    near_end = value.renewed(NOW + timedelta(hours=2), timedelta(days=1))
    assert near_end.idle_expires_at == near_end.absolute_expires_at


def test_renewal_does_not_shorten_idle_expiry_on_clock_skew() -> None:
    """Откат часов одного процесса не уменьшает уже сохранённый срок."""
    value = session().renewed(NOW + timedelta(minutes=1), timedelta(minutes=30))
    assert value.idle_expires_at == NOW + timedelta(hours=2)


def test_expired_or_revoked_session_cannot_be_renewed() -> None:
    """Продление требует действующую запись."""
    value = session()
    with pytest.raises(ValueError, match="нельзя продлить"):
        value.renewed(NOW + timedelta(hours=2), timedelta(hours=1))


@pytest.mark.parametrize(
    ("idle", "absolute"),
    [
        (timedelta(0), timedelta(hours=8)),
        (timedelta(days=2), timedelta(days=3)),
        (timedelta(hours=9), timedelta(hours=8)),
        (timedelta(hours=2), timedelta(days=1, seconds=1)),
    ],
)
def test_policy_rejects_unbounded_or_invalid_timeouts(
    idle: timedelta, absolute: timedelta
) -> None:
    """Обычная сессия не может длиться более суток."""
    with pytest.raises(ValueError):
        SessionPolicy(idle_timeout=idle, absolute_timeout=absolute)


def test_session_contains_digest_instead_of_browser_token() -> None:
    """Домен не принимает сырой секрет браузера вместо SHA-256."""
    assert "token" not in {field.name for field in fields(AuthSession)}
    with pytest.raises(ValueError, match="SHA-256"):
        AuthSession(
            session_id=session().session_id,
            user_id=session().user_id,
            token_hash="browser-secret",
            authorization_version=1,
            created_at=NOW,
            last_seen_at=NOW,
            idle_expires_at=NOW + timedelta(hours=1),
            absolute_expires_at=NOW + timedelta(hours=8),
        )


def test_session_record_rejects_absolute_expiry_beyond_one_day() -> None:
    """Даже создание доменной записи в обход политики не даст долгую сессию."""
    with pytest.raises(ValueError, match="хронология"):
        AuthSession(
            session_id=session().session_id,
            user_id=session().user_id,
            token_hash="a" * 64,
            authorization_version=1,
            created_at=NOW,
            last_seen_at=NOW,
            idle_expires_at=NOW + timedelta(hours=2),
            absolute_expires_at=NOW + timedelta(days=2),
        )
