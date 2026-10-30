"""Configurações locais, lidas do ambiente e do arquivo .env."""

from pathlib import Path
import re
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = Field(default="Genius Agenda AI", min_length=1)
    app_env: Literal["development", "test", "production"] = "development"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_auth_token: SecretStr | None = Field(default=None, repr=False, exclude=True)
    d360_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    llm_provider: Literal["openai", "gemini"] = "openai"
    gemini_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    gemini_model: str = "gemini-3.8-flash"
    gemini_max_output_tokens: int = Field(default=2048, ge=128, le=4096, strict=True)
    openai_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    llm_model: str = "gpt-5-mini"
    llm_timeout_seconds: float = Field(default=30, ge=5, le=60, allow_inf_nan=False)
    llm_max_output_tokens: int = Field(default=600, ge=128, le=2048, strict=True)
    llm_max_input_chars: int = Field(default=4000, ge=1, le=4000, strict=True)
    log_level: Literal["debug", "info", "warning", "error", "critical"] = "info"
    database_path: Path = Path("data/agenda.db")
    reminder_database_path: Path = Path("data/reminders.sqlite3")
    panel_enabled: bool = True
    panel_database_path: Path = Path("data/panel-access.sqlite3")
    panel_operations_database_path: Path = Path("data/panel-operations.sqlite3")
    reminder_cards_enabled: bool = False
    whatsapp_agent_enabled: bool = False
    whatsapp_agent_database_path: Path = Path("data/whatsapp-agent.sqlite3")
    whatsapp_agent_outbox_path: Path = Path("data/whatsapp-agent-outbox.sqlite3")
    agent_deletion_database_path: Path = Path("data/agent-deletions.sqlite3")
    agent_update_database_path: Path = Path("data/agent-updates.sqlite3")
    agent_creation_database_path: Path = Path("data/agent-creations.sqlite3")
    conversation_database_path: Path = Path("data/agent-conversations.sqlite3")
    conversation_ttl_seconds: int = Field(default=1800, ge=60, le=86400, strict=True)
    google_client_file: Path = Path("secrets/google-calendar-client.json")
    google_token_file: Path = Path("secrets/google-calendar-token.json")
    google_calendar_name: str = "Genius Testes"
    google_calendar_id: str = ""

    @field_validator("api_auth_token", "d360_api_key", "openai_api_key", "gemini_api_key", mode="before")
    @classmethod
    def empty_api_token_is_unconfigured(cls, value):
        return None if value == "" else value

    @field_validator("api_auth_token")
    @classmethod
    def validate_api_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not re.fullmatch(
            r"[A-Za-z0-9_-]{32,128}", value.get_secret_value(), flags=re.ASCII,
        ):
            raise ValueError("A chave da API deve ter de 32 a 128 caracteres ASCII URL-safe.")
        return value


    @field_validator("d360_api_key")
    @classmethod
    def validate_sandbox_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and re.fullmatch(
            r"[A-Za-z0-9_-]{16,256}", value.get_secret_value(), flags=re.ASCII,
        ) is None:
            raise ValueError("A chave do sandbox possui formato invalido.")
        return value

    @field_validator("openai_api_key", "gemini_api_key")
    @classmethod
    def validate_model_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and re.fullmatch(r"[!-~]{1,512}", value.get_secret_value(), flags=re.ASCII) is None:
            raise ValueError("A chave do modelo deve ser ASCII visível sem espaços.")
        return value

    @field_validator("llm_model", "gemini_model")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value, flags=re.ASCII) is None:
            raise ValueError("Informe um identificador de modelo válido.")
        return value

    @field_validator("llm_max_output_tokens", "llm_max_input_chars", "gemini_max_output_tokens", "conversation_ttl_seconds", mode="before")
    @classmethod
    def load_integer_limits_from_environment(cls, value):
        # Variáveis de ambiente são textos; normalizar somente dígitos ASCII
        # antes da validação inteira estrita, preservando os limites do campo.
        if isinstance(value, str):
            if re.fullmatch(r"[0-9]{1,8}", value, flags=re.ASCII) is None:
                raise ValueError("Informe um limite inteiro válido para a configuração.")
            return int(value)
        return value


settings = Settings()
