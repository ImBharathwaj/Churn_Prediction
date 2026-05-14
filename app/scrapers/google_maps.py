"""Playwright-based Google Maps scraper.

WARNING: Scraping Google Maps violates Google's Terms of Service. Google
actively detects and blocks scrapers. Prefer the Google Places API
(`google_places` source) for production use.

This scraper exists for educational/local-use purposes and may break at any
time as Google updates the UI. It uses the same DOM selectors that have been
stable for a while but treat them as best-effort.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote_plus

from playwright.async_api import Page, async_playwright

from app.config import get_settings
from app.scrapers.base import BaseScraper, ScrapedBusiness, ScrapeQuery

logger = logging.getLogger(__name__)


class GoogleMapsScraper(BaseScraper):
    source_name = "google_maps"

    def __init__(self) -> None:
        self.settings = get_settings()

    async def scrape(self, query: ScrapeQuery) -> list[ScrapedBusiness]:
        url = (
            "https://www.google.com/maps/search/"
            f"{quote_plus(query.full_query())}/?hl=en"
        )

        results: list[ScrapedBusiness] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.settings.playwright_headless)
            try:
                context = await browser.new_context(
                    user_agent=self.settings.user_agent,
                    viewport={"width": 1366, "height": 900},
                    locale="en-US",
                )
                page = await context.new_page()
                page.set_default_timeout(self.settings.request_timeout * 1000)

                try:
                    await page.goto(url, wait_until="domcontentloaded")
                except Exception as e:
                    logger.error("Failed to load Google Maps: %s", e)
                    return []

                await self._maybe_accept_consent(page)

                try:
                    await page.wait_for_selector('a[href*="/maps/place/"]', timeout=15000)
                except Exception:
                    logger.warning("No Google Maps results found for %r", query.full_query())
                    return []

                await self._scroll_results(page, query.max_results)

                cards = await page.locator('a[href*="/maps/place/"]').all()
                seen: set[str] = set()

                for card in cards:
                    if len(results) >= query.max_results:
                        break
                    try:
                        href = await card.get_attribute("href")
                        if not href or href in seen:
                            continue
                        seen.add(href)

                        await card.click()
                        await page.wait_for_timeout(1500)
                        biz = await self._extract_details(page, href, query)
                        if biz:
                            results.append(biz)
                    except Exception as e:  # noqa: BLE001
                        logger.debug("Skipping a card due to error: %s", e)
                        continue
            finally:
                await browser.close()

        logger.info("Google Maps returned %d results for %r", len(results), query.full_query())
        return results

    async def _maybe_accept_consent(self, page: Page) -> None:
        for selector in [
            'button:has-text("Accept all")',
            'button:has-text("I agree")',
            'button[aria-label*="Accept"]',
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    await page.wait_for_timeout(800)
                    return
            except Exception:
                continue

    async def _scroll_results(self, page: Page, target_count: int) -> None:
        feed = page.locator('div[role="feed"]').first
        try:
            await feed.wait_for(timeout=5000)
        except Exception:
            return

        last_count = 0
        stable_passes = 0
        for _ in range(40):
            count = await page.locator('a[href*="/maps/place/"]').count()
            if count >= target_count:
                return
            if count == last_count:
                stable_passes += 1
                if stable_passes >= 3:
                    return
            else:
                stable_passes = 0
                last_count = count

            try:
                await feed.evaluate("(el) => el.scrollBy(0, el.scrollHeight)")
            except Exception:
                break
            await asyncio.sleep(1.2)

    async def _extract_details(
        self, page: Page, href: str, query: ScrapeQuery
    ) -> ScrapedBusiness | None:
        try:
            await page.wait_for_selector('h1', timeout=8000)
        except Exception:
            return None

        name = (await self._safe_text(page, 'h1')) or "Unknown"

        rating = reviews = None
        try:
            rating_text = await self._safe_text(page, 'div.F7nice span[aria-hidden="true"]')
            if rating_text:
                rating = float(rating_text.replace(",", "."))
        except Exception:
            pass
        try:
            reviews_text = await self._safe_text(page, 'div.F7nice span[aria-label*="reviews"]')
            if reviews_text:
                m = re.search(r"[\d,]+", reviews_text)
                if m:
                    reviews = int(m.group(0).replace(",", ""))
        except Exception:
            pass

        category = await self._safe_text(page, 'button[jsaction*="category"]')
        address = await self._get_info_value(page, 'button[data-item-id="address"]')
        website = await self._get_info_value(page, 'a[data-item-id="authority"]', attr="href")
        phone = await self._get_phone(page)

        lat, lng = self._extract_latlng(href)

        source_id = self._extract_place_id(href) or href

        return ScrapedBusiness(
            source=self.source_name,
            source_id=source_id,
            name=name,
            category=category,
            address=address,
            phone=phone,
            website=website,
            rating=rating,
            reviews_count=reviews,
            latitude=lat,
            longitude=lng,
            source_url=f"https://www.google.com{href}" if href.startswith("/") else href,
            raw_query=query.full_query(),
        )

    async def _safe_text(self, page: Page, selector: str) -> str | None:
        try:
            loc = page.locator(selector).first
            if await loc.count() == 0:
                return None
            text = await loc.inner_text(timeout=2000)
            return text.strip() or None
        except Exception:
            return None

    async def _get_info_value(
        self, page: Page, selector: str, attr: str | None = None
    ) -> str | None:
        try:
            loc = page.locator(selector).first
            if await loc.count() == 0:
                return None
            if attr:
                val = await loc.get_attribute(attr)
                return val.strip() if val else None
            aria = await loc.get_attribute("aria-label")
            if aria:
                for prefix in ("Address: ", "Website: ", "Phone: "):
                    if aria.startswith(prefix):
                        return aria[len(prefix):].strip()
                return aria.strip()
            text = await loc.inner_text()
            return text.strip() or None
        except Exception:
            return None

    async def _get_phone(self, page: Page) -> str | None:
        for selector in [
            'button[data-item-id^="phone:tel:"]',
            'button[aria-label*="Phone"]',
        ]:
            val = await self._get_info_value(page, selector)
            if val:
                if val.lower().startswith("phone:"):
                    val = val.split(":", 1)[1].strip()
                return val
        return None

    @staticmethod
    def _extract_latlng(href: str) -> tuple[float | None, float | None]:
        m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", href)
        if m:
            return float(m.group(1)), float(m.group(2))
        m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", href)
        if m:
            return float(m.group(1)), float(m.group(2))
        return None, None

    @staticmethod
    def _extract_place_id(href: str) -> str | None:
        m = re.search(r"!1s([^!]+)", href)
        if m:
            return m.group(1)
        m = re.search(r"/place/([^/]+)", href)
        if m:
            return m.group(1)
        return None
