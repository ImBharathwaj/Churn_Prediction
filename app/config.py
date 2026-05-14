from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://scraper:scraper@localhost:5433/scraper"

    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    scraper_sources: str = "google_maps,yellowpages"
    max_results_per_source: int = 30
    request_timeout: int = 30

    playwright_headless: bool = True

    google_places_api_key: str | None = None

    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    @property
    def enabled_sources(self) -> list[str]:
        return [s.strip().lower() for s in self.scraper_sources.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
