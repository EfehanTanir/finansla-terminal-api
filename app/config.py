"""Central configuration. All secrets come from environment variables.

On Render, set these under your service's "Environment" tab.
Locally, copy .env.example to .env and fill it in.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Fonoloji data source ---
    # Your key (starts with "fon_"). Set it in Render's Environment tab.
    fonoloji_api_key: str = ""
    # Base URL is already the real one; live mode turns on as soon as a key is set.
    fonoloji_base_url: str = "https://fonoloji.com/v1"
    # Fonoloji authenticates with the raw key in an X-API-Key header (no scheme).
    fonoloji_auth_header: str = "X-API-Key"
    fonoloji_auth_scheme: str = ""

    # --- Branding ---
    brand_name: str = "Finansla"

    # --- Behaviour ---
    # Seconds to cache upstream responses in memory. Protects your monthly quota.
    cache_ttl: int = 60
    # If a key is set but you still want mock data (e.g. for a demo), set to true.
    force_mock: bool = False
    # CORS: which origins may call this API. Add your frontend domains.
    allowed_origins: str = "https://terminal.finansla.net,https://finansla.net,http://localhost:8000"

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def use_live_data(self) -> bool:
        return bool(self.fonoloji_base_url and self.fonoloji_api_key and not self.force_mock)


settings = Settings()