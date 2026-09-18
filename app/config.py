"""Environment-only service configuration."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_model: str | None = None
    gemini_api_key_1: SecretStr | None = None
    gemini_api_key_2: SecretStr | None = None
    gemini_api_key_3: SecretStr | None = None
    gemini_api_key_4: SecretStr | None = None
    gemini_api_key_5: SecretStr | None = None
    gemini_api_key_6: SecretStr | None = None
    gemini_api_key_7: SecretStr | None = None
    gemini_api_key_8: SecretStr | None = None

    groq_model: str | None = None
    groq_api_key_1: SecretStr | None = None
    groq_api_key_2: SecretStr | None = None
    groq_api_key_3: SecretStr | None = None
    groq_api_key_4: SecretStr | None = None
    groq_api_key_5: SecretStr | None = None
    groq_api_key_6: SecretStr | None = None
    groq_api_key_7: SecretStr | None = None
    groq_api_key_8: SecretStr | None = None
    groq_api_key_9: SecretStr | None = None
    groq_api_key_10: SecretStr | None = None
    groq_api_key_11: SecretStr | None = None
    groq_api_key_12: SecretStr | None = None

    llm_timeout_seconds: float = Field(default=4.0, gt=0)
    llm_hard_request_deadline_seconds: float = Field(default=9.0, gt=0, le=29)
    llm_key_cooldown_seconds: float = Field(default=60.0, ge=0)
    total_request_deadline_seconds: float = Field(default=29.0, gt=2.5, le=29)
    max_request_body_bytes: int = Field(default=262_144, ge=16_384, le=1_048_576)
    max_concurrent_optimizations: int = Field(default=16, ge=1, le=128)
    max_queued_optimizations: int = Field(default=64, ge=0, le=1024)
    port: int = 8000
    log_level: str = "INFO"

    def _keys(self, provider: str, count: int) -> dict[int, str]:
        values: dict[int, str] = {}
        for number in range(1, count + 1):
            secret = getattr(self, f"{provider}_api_key_{number}")
            if secret is not None and secret.get_secret_value().strip():
                values[number] = secret.get_secret_value().strip()
        return values

    @property
    def gemini_keys(self) -> dict[int, str]:
        return self._keys("gemini", 8)

    @property
    def groq_keys(self) -> dict[int, str]:
        return self._keys("groq", 12)

    def validate_provider_configuration(self) -> None:
        if not self.gemini_model or not self.gemini_model.strip() or not self.gemini_keys:
            raise ValueError("GEMINI_MODEL and at least one Gemini credential are required")
        has_groq_model = bool(self.groq_model and self.groq_model.strip())
        has_groq_keys = bool(self.groq_keys)
        if has_groq_model != has_groq_keys:
            raise ValueError("Groq backup requires both GROQ_MODEL and at least one credential")


@lru_cache
def get_settings() -> Settings:
    return Settings()
