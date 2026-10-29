import os
from dataclasses import dataclass


@dataclass
class Settings:
    bot_token: str
    db_path: str
    workers_count: int


def load_settings() -> Settings:
    return Settings(
        bot_token=os.getenv("BOT_TOKEN", "000000000:EXAMPLE_TOKEN_FOR_TESTS"),
        db_path=os.getenv("DB_PATH", "coursework_var18.db"),
        workers_count=int(os.getenv("WORKERS_COUNT", "3")),
    )
