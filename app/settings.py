"""Configurações locais, lidas do ambiente e do arquivo .env."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="Genius Agenda AI", min_length=1)
    app_env: Literal["development", "test", "production"] = "development"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["debug", "info", "warning", "error", "critical"] = "info"
    database_path: Path = Path("data/agenda.db")
    google_client_file: Path = Path("secrets/google-calendar-client.json")
    google_token_file: Path = Path("secrets/google-calendar-token.json")
    google_calendar_name: str = "Genius Testes"
    google_calendar_id: str = ""


settings = Settings()
