from __future__ import annotations

from app.scrapers.base import BaseScraper, ScrapedBusiness, ScrapeQuery
from app.scrapers.google_maps import GoogleMapsScraper
from app.scrapers.google_places import GooglePlacesScraper
from app.scrapers.yellowpages import YellowPagesScraper

__all__ = [
    "BaseScraper",
    "ScrapedBusiness",
    "ScrapeQuery",
    "GoogleMapsScraper",
    "GooglePlacesScraper",
    "YellowPagesScraper",
    "get_scraper",
]


def get_scraper(source: str) -> BaseScraper:
    source = source.lower().strip()
    if source == "google_maps":
        return GoogleMapsScraper()
    if source == "google_places":
        return GooglePlacesScraper()
    if source == "yellowpages":
        return YellowPagesScraper()
    raise ValueError(f"Unknown scraper source: {source}")
