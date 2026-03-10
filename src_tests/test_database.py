import uuid

import pytest
from sqlalchemy import text

from src.config.settings import get_settings
from src.models.database import close_db, get_session_factory, init_db, ping_db, session_scope


@pytest.fixture(autouse=True)
async def _cleanup_singletons(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "test-db-encryption-key")
    get_settings.cache_clear()
    yield
    await close_db()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ping_db_returns_true_when_database_available():
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")
    assert await ping_db() is True


@pytest.mark.asyncio
async def test_session_scope_commit_and_rollback():
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")

    settings = get_settings()
    schema = settings.database_schema
    table_name = f"ut_tx_{uuid.uuid4().hex}"
    full_name = f'"{schema}"."{table_name}"'

    async with session_scope() as session:
        await session.exec(text(f"CREATE TABLE {full_name} (id INT PRIMARY KEY)"))
        await session.exec(text(f"INSERT INTO {full_name} (id) VALUES (1)"))

    session_factory = get_session_factory()
    async with session_factory() as session:
        count_result = await session.exec(text(f"SELECT COUNT(*) FROM {full_name}"))
        assert count_result.scalar_one() == 1

    with pytest.raises(RuntimeError):
        async with session_scope() as session:
            await session.exec(text(f"INSERT INTO {full_name} (id) VALUES (2)"))
            raise RuntimeError("force rollback")

    async with session_factory() as session:
        count_result = await session.exec(text(f"SELECT COUNT(*) FROM {full_name}"))
        assert count_result.scalar_one() == 1
        await session.exec(text(f"DROP TABLE {full_name}"))
        await session.commit()


@pytest.mark.asyncio
async def test_init_db_is_idempotent_and_extensions_exist():
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")

    settings = get_settings()
    await init_db()
    await init_db()

    session_factory = get_session_factory()
    async with session_factory() as session:
        schema_result = await session.exec(
            text(
                "SELECT schema_name "
                "FROM information_schema.schemata "
                "WHERE schema_name = :schema"
            ).bindparams(schema=settings.database_schema)
        )
        assert schema_result.scalar_one_or_none() == settings.database_schema

        ext_result = await session.exec(
            text(
                "SELECT extname "
                "FROM pg_extension "
                "WHERE extname IN ('pgcrypto', 'ltree')"
            )
        )
        ext_names = {row[0] for row in ext_result.fetchall()}
        assert {"pgcrypto", "ltree"}.issubset(ext_names)
