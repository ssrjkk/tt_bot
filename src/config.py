import os
from dataclasses import dataclass


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    return max(minimum, value)


def _env_float(name: str, default: float, minimum: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    return max(minimum, value)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    db_path: str = "data/tt_bot.db"
    workers_count: int = 3
    probe_timeout_sec: float = 10.0
    scheduler_tick_sec: float = 15.0
    allow_private_endpoints: bool = False
    log_retention_days: int = 30
    throttle_rate_sec: float = 0.4
    log_format: str = "text"


def load_settings(*, require_token: bool = True) -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if require_token and not token:
        raise RuntimeError(
            "BOT_TOKEN is required. Export it in the shell, or provide it "
            "through docker compose env_file / systemd EnvironmentFile."
        )

    workers_count = _env_int("WORKERS_COUNT", 3, 1)
    probe_timeout = _env_float("PROBE_TIMEOUT_SEC", 10.0, 1.0)
    scheduler_tick = _env_float("SCHEDULER_TICK_SEC", 15.0, 5.0)
    retention_days = _env_int("LOG_RETENTION_DAYS", 30, 1)
    throttle_rate = _env_float("THROTTLE_RATE_SEC", 0.4, 0.0)
    log_fmt = os.getenv("LOG_FORMAT", "text").strip().lower()
    if log_fmt not in {"text", "json"}:
        log_fmt = "text"

    return Settings(
        bot_token=token,
        db_path=os.getenv("DB_PATH", "data/tt_bot.db"),
        workers_count=workers_count,
        probe_timeout_sec=probe_timeout,
        scheduler_tick_sec=scheduler_tick,
        allow_private_endpoints=_parse_bool(
            os.getenv("ALLOW_PRIVATE_ENDPOINTS", "false")
        ),
        log_retention_days=retention_days,
        throttle_rate_sec=throttle_rate,
        log_format=log_fmt,
    )
