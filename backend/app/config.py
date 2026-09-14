from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    clerk_jwks_url: str
    clerk_issuer: str
    clerk_webhook_secret: str
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_price_id: str
    frontend_url: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
