"""YellowPages.com scraper (static HTML, BeautifulSoup-based).

YellowPages.com returns server-rendered HTML for search results, which makes
it scrape-friendly. Like all scraping, respect robots.txt and rate limits.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote_plus, urljoin

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.scrapers.base import BaseScraper, ScrapedBusiness, ScrapeQuery

logger = logging.getLogger(__name__)

BASE_URL = "https://www.yellowpages.com"


class YellowPagesScraper(BaseScraper):
    source_name = "yellowpages"

    def __init__(self) -> None:
        self.settings = get_settings()

    async def scrape(self, query: ScrapeQuery) -> list[ScrapedBusiness]:
        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        results: list[ScrapedBusiness] = []
        page_num = 1
        max_pages = max(1, (query.max_results // 30) + 1)

        async with httpx.AsyncClient(
            headers=headers,
            timeout=self.settings.request_timeout,
            follow_redirects=True,
        ) as client:
            while page_num <= max_pages and len(results) < query.max_results:
                try:
                    html = await self._fetch_page(client, query, page_num)
                except Exception as e:  # noqa: BLE001
                    logger.error("YellowPages fetch failed on page %d: %s", page_num, e)
                    break

                page_results = self._parse_listings(html, query)
                if not page_results:
                    break

                for biz in page_results:
                    if len(results) >= query.max_results:
                        break
                    results.append(biz)

                page_num += 1
                await asyncio.sleep(1.0)

        logger.info("YellowPages returned %d results for %r", len(results), query.full_query())
        return results

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    async def _fetch_page(self, client: httpx.AsyncClient, query: ScrapeQuery, page: int) -> str:
        search_term = query.search_term()
        location = query.business_area
        url = (
            f"{BASE_URL}/search?search_terms={quote_plus(search_term)}"
            f"&geo_location_terms={quote_plus(location)}&page={page}"
        )
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

    def _parse_listings(self, html: str, query: ScrapeQuery) -> list[ScrapedBusiness]:
        soup = BeautifulSoup(html, "lxml")
        results: list[ScrapedBusiness] = []

        for card in soup.select("div.result, div.organic div.v-card, div.search-results div.v-card"):
            biz = self._parse_card(card, query)
            if biz:
                results.append(biz)

        if not results:
            for card in soup.select("div.v-card"):
                biz = self._parse_card(card, query)
                if biz:
                    results.append(biz)

        return results

    def _parse_card(self, card, query: ScrapeQuery) -> ScrapedBusiness | None:
        name_el = card.select_one("a.business-name, h2 a, a.track-visit-website")
        name = None
        link = None
        if name_el:
            name = name_el.get_text(strip=True)
            href = name_el.get("href")
            if href:
                link = urljoin(BASE_URL, href)

        if not name:
            return None

        category_el = card.select_one("div.categories")
        category = category_el.get_text(" ", strip=True) if category_el else None

        phone_el = card.select_one("div.phones, div.phone")
        phone = phone_el.get_text(strip=True) if phone_el else None

        street_el = card.select_one("div.street-address, span.street-address")
        locality_el = card.select_one("div.locality, span.locality")
        street = street_el.get_text(strip=True) if street_el else None
        locality_text = locality_el.get_text(" ", strip=True) if locality_el else None

        city, state, postal = self._parse_locality(locality_text)
        address_parts = [p for p in (street, locality_text) if p]
        address = ", ".join(address_parts) if address_parts else None

        website_el = card.select_one("a.track-visit-website")
        website = website_el.get("href") if website_el else None

        rating = None
        rating_el = card.select_one("div.result-rating, span.bbb-rating")
        if rating_el:
            classes = " ".join(rating_el.get("class") or [])
            m = re.search(r"(\d+)(?:\s+half)?", classes)
            if m:
                try:
                    rating = float(m.group(1))
                except ValueError:
                    rating = None

        reviews = None
        reviews_el = card.select_one("span.count")
        if reviews_el:
            m = re.search(r"\d+", reviews_el.get_text())
            if m:
                reviews = int(m.group(0))

        source_id = link or f"{name}|{address or ''}"

        return ScrapedBusiness(
            source=self.source_name,
            source_id=source_id,
            name=name,
            category=category,
            address=address,
            city=city,
            state=state,
            postal_code=postal,
            country="US",
            phone=phone,
            website=website,
            rating=rating,
            reviews_count=reviews,
            source_url=link,
            raw_query=query.full_query(),
        )

    @staticmethod
    def _parse_locality(text: str | None) -> tuple[str | None, str | None, str | None]:
        if not text:
            return None, None, None
        cleaned = text.replace(",", " ").strip()
        m = re.match(r"^(.*?)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$", cleaned)
        if m:
            return m.group(1).strip(), m.group(2), m.group(3)
        m = re.match(r"^(.*?)\s+([A-Z]{2})$", cleaned)
        if m:
            return m.group(1).strip(), m.group(2), None
        return cleaned or None, None, None
