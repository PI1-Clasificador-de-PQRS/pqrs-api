from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://pqrs:pqrs@localhost:5432/pqrs"
    session_cookie_name: str = "pqrs_session"
    session_cookie_secure: bool = True
    session_idle_hours: int = 8
    # Vida máxima de una sesión desde el login, aunque tenga actividad reciente.
    session_max_hours: int = Field(default=24, ge=1)
    # Fallos permitidos por par (email, IP) y por IP sola, dentro de la misma ventana.
    login_max_attempts: int = Field(default=5, ge=1)
    login_max_attempts_per_ip: int = Field(default=20, ge=1)
    login_window_minutes: int = Field(default=15, ge=1)
    # Tope de claves del limitador en memoria (cada fallo usa dos: el par y la IP).
    login_limiter_max_keys: int = Field(default=10_000, ge=2)
    log_level: str = "INFO"
    # Proxies de confianza que agregan una entrada a X-Forwarded-For entre el cliente y la API.
    # 0: se ignora la cabecera y se usa la IP de la conexión.
    trusted_proxy_hops: int = Field(default=0, ge=0)

    @field_validator("database_url")
    @classmethod
    def _use_psycopg3_driver(cls, value: str) -> str:
        for prefix in ("postgresql://", "postgres://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix) :]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
