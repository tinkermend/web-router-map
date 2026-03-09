from src.config.settings import get_settings


def _clear_database_env(monkeypatch) -> None:
    for key in [
        "DATABASE_URL",
        "DATABASE_SCHEMA",
        "DATABASE_POOL_SIZE",
        "DATABASE_MAX_OVERFLOW",
        "DATABASE_POOL_TIMEOUT",
        "DATABASE_POOL_RECYCLE",
        "DEBUG",
    ]:
        monkeypatch.delenv(key, raising=False)


def test_settings_defaults(monkeypatch) -> None:
    _clear_database_env(monkeypatch)
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.database_url == "postgresql+asyncpg://aiops:AIOps!1234@127.0.0.1:5432/navai"
    assert settings.database_schema == "navai"
    assert settings.database_pool_size == 10
    assert settings.database_max_overflow == 20
    assert settings.database_pool_timeout == 30
    assert settings.database_pool_recycle == 1800
    assert settings.debug is False


def test_settings_env_override(monkeypatch) -> None:
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://foo:bar@localhost:5432/demo")
    monkeypatch.setenv("DATABASE_SCHEMA", "demo")
    monkeypatch.setenv("DATABASE_POOL_SIZE", "5")
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "7")
    monkeypatch.setenv("DATABASE_POOL_TIMEOUT", "12")
    monkeypatch.setenv("DATABASE_POOL_RECYCLE", "99")
    monkeypatch.setenv("DEBUG", "true")
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.database_url == "postgresql+asyncpg://foo:bar@localhost:5432/demo"
    assert settings.database_schema == "demo"
    assert settings.database_pool_size == 5
    assert settings.database_max_overflow == 7
    assert settings.database_pool_timeout == 12
    assert settings.database_pool_recycle == 99
    assert settings.debug is True

