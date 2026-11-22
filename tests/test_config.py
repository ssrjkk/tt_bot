import pytest

from src.config import load_settings


def test_load_settings_requires_token(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="BOT_TOKEN is required"):
        load_settings(require_token=True)


def test_load_settings_defaults_without_require(monkeypatch):
    for var in (
        "BOT_TOKEN",
        "DB_PATH",
        "WORKERS_COUNT",
        "PROBE_TIMEOUT_SEC",
        "SCHEDULER_TICK_SEC",
        "ALLOW_PRIVATE_ENDPOINTS",
        "LOG_RETENTION_DAYS",
        "THROTTLE_RATE_SEC",
        "LOG_FORMAT",
    ):
        monkeypatch.delenv(var, raising=False)

    cfg = load_settings(require_token=False)
    assert cfg.bot_token == ""
    assert cfg.db_path == "data/tt_bot.db"
    assert cfg.workers_count == 3
    assert cfg.probe_timeout_sec == 10.0
    assert cfg.scheduler_tick_sec == 15.0
    assert cfg.allow_private_endpoints is False
    assert cfg.log_retention_days == 30
    assert cfg.throttle_rate_sec == 0.4
    assert cfg.log_format == "text"


def test_load_settings_custom_env_and_clamping(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "  123456:TEST_TOKEN  ")
    monkeypatch.setenv("DB_PATH", "/tmp/custom.db")
    monkeypatch.setenv("WORKERS_COUNT", "0")
    monkeypatch.setenv("PROBE_TIMEOUT_SEC", "0.2")
    monkeypatch.setenv("SCHEDULER_TICK_SEC", "1")
    monkeypatch.setenv("ALLOW_PRIVATE_ENDPOINTS", "YES")
    monkeypatch.setenv("LOG_RETENTION_DAYS", "-5")
    monkeypatch.setenv("THROTTLE_RATE_SEC", "-1")
    monkeypatch.setenv("LOG_FORMAT", "JSON")

    cfg = load_settings(require_token=True)
    assert cfg.bot_token == "123456:TEST_TOKEN"
    assert cfg.workers_count == 1
    assert cfg.probe_timeout_sec == 1.0
    assert cfg.scheduler_tick_sec == 5.0
    assert cfg.allow_private_endpoints is True
    assert cfg.log_retention_days == 1
    assert cfg.throttle_rate_sec == 0.0
    assert cfg.log_format == "json"

    monkeypatch.setenv("LOG_FORMAT", "xml")
    assert load_settings(require_token=True).log_format == "text"
