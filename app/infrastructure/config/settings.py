from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ``development`` relaja las exigencias de producción (secreto del proxy, modelo NER).
    environment: str = "production"
    database_url: str = "postgresql+psycopg://pqrs:pqrs@localhost:5432/pqrs"
    session_cookie_name: str = "pqrs_session"
    session_cookie_secure: bool = True
    session_idle_hours: int = 8
    # Vida máxima de una sesión desde el login, aunque tenga actividad reciente.
    session_max_hours: int = Field(default=24, ge=1)
    # Fallos permitidos por par (email, IP) y por IP sola, dentro de la misma ventana.
    login_max_attempts: int = Field(default=5, ge=1)
    login_max_attempts_per_ip: int = Field(default=20, ge=1)
    # Respaldo frente a ataques repartidos entre muchas IP contra un mismo correo.
    login_max_attempts_per_email: int = Field(default=50, ge=1)
    login_window_minutes: int = Field(default=15, ge=1)
    # Tope de claves del limitador en memoria (cada fallo usa tres: par, IP y correo).
    login_limiter_max_keys: int = Field(default=10_000, ge=3)
    log_level: str = "INFO"
    # Proxies de confianza que agregan una entrada a X-Forwarded-For entre el cliente y la API.
    # 0: se ignora la cabecera y se usa la IP de la conexión.
    trusted_proxy_hops: int = Field(default=0, ge=0)

    # Secreto compartido con el proxy del dashboard (cabecera X-PQRS-Proxy-Secret). Obligatorio
    # fuera de ``development``; sin él la API no arranca (ver ``require_runtime_config``).
    proxy_shared_secret: SecretStr | None = None

    # Solicitudes por minuto a /classify, /classify/batch y /models: por API key o por usuario,
    # nunca por IP. En memoria (una sola réplica).
    rate_limit_key_per_minute: int = Field(default=60, ge=1)
    rate_limit_user_per_minute: int = Field(default=120, ge=1)
    # Tamaño máximo del cuerpo de una petición, en bytes.
    max_body_bytes: int = Field(default=1_048_576, ge=1)

    # Directorio con model.joblib y metadata.json del motor local (pqrs_classifier).
    model_dir: Path = Path("/opt/model")
    # Clasificaciones simultáneas del motor local (acota la memoria).
    classifier_max_concurrency: int = Field(default=2, ge=1)

    # Motor LLM: se pasan tal cual a ``LLMEngine.from_env``. Sin clave ni modelo queda
    # ``not_configured``. ``.env`` no llega a ``os.environ``, por eso se declaran aquí.
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_temperature: float | None = None
    llm_timeout: float | None = None
    llm_max_retries: int | None = None
    llm_workers: int | None = None
    # Caracteres de cada texto que ve el LLM (el paquete trunca; el motor local ve el texto entero).
    max_caracteres_texto: int | None = None
    # F1 macro del LLM mostrado en /models y /metrics/summary (validación cruzada del paquete).
    llm_eval_f1_macro: float = Field(default=0.8329, ge=0, le=1)

    # Circuito de protección del LLM (en memoria; asume una sola réplica).
    llm_circuit_failures: int = Field(default=3, ge=1)
    llm_circuit_open_seconds: int = Field(default=60, ge=1)

    @property
    def is_development(self) -> bool:
        return self.environment.strip().lower() == "development"

    def require_runtime_config(self) -> None:
        """Falla si falta una configuración obligatoria para servir tráfico.

        Se llama al arrancar (``get_app``), no al construir ``Settings``: la CLI, Alembic y los
        scripts también leen la configuración y no necesitan el secreto del proxy.
        """
        secret = self.proxy_shared_secret
        if not self.is_development and (secret is None or not secret.get_secret_value()):
            raise RuntimeError(
                "PROXY_SHARED_SECRET es obligatorio fuera de ENVIRONMENT=development: sin él, "
                "cualquiera podría falsificar la IP del navegador llamando directo a la API."
            )

    def llm_env(self) -> dict[str, str]:
        """Variables ``LLM_*`` en el formato que espera ``LLMEngine.from_env``."""
        values: dict[str, object | None] = {
            "LLM_API_KEY": self.llm_api_key.get_secret_value() if self.llm_api_key else None,
            "LLM_BASE_URL": self.llm_base_url,
            "LLM_MODEL": self.llm_model,
            "LLM_TEMPERATURE": self.llm_temperature,
            "LLM_TIMEOUT": self.llm_timeout,
            "LLM_MAX_RETRIES": self.llm_max_retries,
            "LLM_WORKERS": self.llm_workers,
            "MAX_CARACTERES_TEXTO": self.max_caracteres_texto,
        }
        return {k: str(v) for k, v in values.items() if v is not None}

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
