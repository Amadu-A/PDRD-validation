"""Проверяет атомарную регистрацию и общий лимитер на изолированном PostgreSQL."""

import asyncio
import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pdrd_auth_service.domain.external_credential import ExternalCredential
from pdrd_auth_service.infrastructure.database.engine import build_session_factory
from pdrd_auth_service.infrastructure.database.external_credentials import (
    SqlAlchemyExternalCredentialStore,
)
from pdrd_auth_service.infrastructure.database.models import ExternalCredentialModel
from pdrd_auth_service.infrastructure.database.rate_limits import (
    SqlAlchemyAttemptLimiter,
)
from sqlalchemy import delete
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.database


def isolated_url(raw_url: str):
    """Не позволяет интеграционным тестам обратиться к рабочему PostgreSQL."""
    url = make_url(raw_url)
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host != "auth-test-postgres"
        or url.database != "pdrd_auth_test"
        or url.username != "auth_test"
        or not url.password
    ):
        raise ValueError("Требуется изолированный PostgreSQL Auth Service")
    return url


@pytest.mark.asyncio
async def test_external_credential_verification_is_one_time() -> None:
    """База не сохраняет исходный код и не даёт погасить его второй раз."""
    raw_url = os.environ.get("AUTH_SERVICE_TEST_DATABASE_URL")
    if os.environ.get("PDRD_RUN_DATABASE_TESTS") != "1" or not raw_url:
        pytest.skip("Требуется изолированный PostgreSQL Auth Service")
    engine = create_async_engine(isolated_url(raw_url))
    subject = uuid4()
    now = datetime.now(UTC)
    token = "v" * 43
    digest = hashlib.sha256(token.encode()).hexdigest()
    store = SqlAlchemyExternalCredentialStore(build_session_factory(engine))
    try:
        credential = ExternalCredential(
            subject=subject,
            user_id=None,
            email=f"{subject}@example.org",
            password_hash="scrypt$test-value",
            created_at=now,
        )
        assert await store.create(credential) is True
        assert await store.create(credential) is False
        await store.attach_user(subject, uuid4())
        await store.set_verification(subject, digest, now + timedelta(hours=1))
        assert (await store.get_by_token_hash(digest)).subject == subject
        assert await store.consume_verification(subject, digest, now) is True
        assert await store.consume_verification(subject, digest, now) is False
        assert await store.get_by_token_hash(digest) is None
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                delete(ExternalCredentialModel).where(
                    ExternalCredentialModel.subject == subject
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_rate_limiter_counts_across_adapter_instances() -> None:
    """Два экземпляра сервиса видят один предел попыток из PostgreSQL."""
    raw_url = os.environ.get("AUTH_SERVICE_TEST_DATABASE_URL")
    if os.environ.get("PDRD_RUN_DATABASE_TESTS") != "1" or not raw_url:
        pytest.skip("Требуется изолированный PostgreSQL Auth Service")
    engine = create_async_engine(isolated_url(raw_url))
    factory = build_session_factory(engine)
    first = SqlAlchemyAttemptLimiter(factory, "test-secret")
    second = SqlAlchemyAttemptLimiter(factory, "test-secret")
    subject = str(uuid4())
    now = datetime.now(UTC)
    try:
        options = {
            "namespace": "integration",
            "subject": subject,
            "limit": 2,
            "window": timedelta(hours=1),
            "now": now,
        }
        assert await first.allow(**options) is True
        assert await second.allow(**options) is True
        assert await first.allow(**options) is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rate_limiter_keeps_limit_under_parallel_load() -> None:
    """Одновременные попытки на нескольких соединениях не обходят порог."""
    raw_url = os.environ.get("AUTH_SERVICE_TEST_DATABASE_URL")
    if os.environ.get("PDRD_RUN_DATABASE_TESTS") != "1" or not raw_url:
        pytest.skip("Требуется изолированный PostgreSQL Auth Service")
    engine = create_async_engine(isolated_url(raw_url))
    limiter = SqlAlchemyAttemptLimiter(
        build_session_factory(engine), "load-test-secret"
    )
    subject = str(uuid4())
    try:
        results = await asyncio.gather(
            *[
                limiter.allow(
                    namespace="parallel-load",
                    subject=subject,
                    limit=5,
                    window=timedelta(hours=1),
                    now=datetime.now(UTC),
                )
                for _ in range(25)
            ]
        )
        assert sum(results) == 5
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_local_credentials_unique_username_and_immutable_profile() -> None:
    """PostgreSQL не создаёт дубли локального логина и не меняет связанный UUID."""
    from pdrd_auth_service.domain.local_credential import LocalCredential
    from pdrd_auth_service.infrastructure.database.local_credentials import (
        SqlAlchemyLocalCredentialStore,
    )
    from pdrd_auth_service.infrastructure.database.models import LocalCredentialModel

    raw_url = os.environ.get("AUTH_SERVICE_TEST_DATABASE_URL")
    if os.environ.get("PDRD_RUN_DATABASE_TESTS") != "1" or not raw_url:
        pytest.skip("Требуется изолированный PostgreSQL Auth Service")
    engine = create_async_engine(isolated_url(raw_url))
    store = SqlAlchemyLocalCredentialStore(build_session_factory(engine))
    subject, user_id = uuid4(), uuid4()
    username = f"test-{subject.hex}"
    try:
        credential = LocalCredential(
            subject, username, "scrypt$test-only", datetime.now(UTC)
        )
        assert await store.create(credential)
        assert not await store.create(credential)
        assert (await store.get_by_username(username)).user_id is None
        await store.attach_user(subject, user_id)
        await store.attach_user(subject, user_id)
        with pytest.raises(ValueError):
            await store.attach_user(subject, uuid4())
        assert (await store.get_by_username(username)).user_id == user_id
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                delete(LocalCredentialModel).where(
                    LocalCredentialModel.subject == subject
                )
            )
        await engine.dispose()
