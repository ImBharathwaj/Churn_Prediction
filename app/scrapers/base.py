from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScrapeQuery:
    business_type: str
    business_category: str | None
    business_area: str
    max_results: int = 30

    def search_term(self) -> str:
        parts = [p for p in (self.business_type, self.business_category) if p]
        return " ".join(parts).strip()

    def full_query(self) -> str:
        term = self.search_term()
        return f"{term} in {self.business_area}".strip()


@dataclass
class ScrapedBusiness:
    source: str
    source_id: str
    name: str
    category: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    phone: str | None = None
    website: str | None = None
    email: str | None = None
    rating: float | None = None
    reviews_count: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    source_url: str | None = None
    raw_query: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class BaseScraper(abc.ABC):
    source_name: str = "base"

    @abc.abstractmethod
    async def scrape(self, query: ScrapeQuery) -> list[ScrapedBusiness]:
        raise NotImplementedError
