"""Google Places API (New) scraper.

This is the ToS-compliant way to get Google Maps business data.
Get a key: https://developers.google.com/maps/documentation/places/web-service/get-api-key
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.scrapers.base import BaseScraper, ScrapedBusiness, ScrapeQuery

logger = logging.getLogger(__name__)

PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.addressComponents",
        "places.location",
        "places.rating",
        "places.userRatingCount",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.websiteUri",
        "places.googleMapsUri",
        "places.primaryType",
        "places.types",
    ]
)


class GooglePlacesScraper(BaseScraper):
    source_name = "google_places"

    def __init__(self) -> None:
        self.settings = get_settings()

    async def scrape(self, query: ScrapeQuery) -> list[ScrapedBusiness]:
        if not self.settings.google_places_api_key:
            logger.warning("GOOGLE_PLACES_API_KEY not configured; skipping google_places source.")
            return []

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.settings.google_places_api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        }
        body: dict[str, Any] = {
            "textQuery": query.full_query(),
            "pageSize": min(query.max_results, 20),  # API max per page = 20
        }

        results: list[ScrapedBusiness] = []
        next_token: str | None = None

        async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
            while len(results) < query.max_results:
                if next_token:
                    body["pageToken"] = next_token

                try:
                    resp = await client.post(PLACES_TEXT_SEARCH_URL, headers=headers, json=body)
                    resp.raise_for_status()
                except httpx.HTTPError as e:
                    logger.error("Google Places API error: %s", e)
                    break

                data = resp.json()
                for place in data.get("places", []):
                    results.append(self._to_business(place, query))
                    if len(results) >= query.max_results:
                        break

                next_token = data.get("nextPageToken")
                if not next_token:
                    break

        logger.info("Google Places returned %d results for %r", len(results), query.full_query())
        return results

    def _to_business(self, place: dict[str, Any], query: ScrapeQuery) -> ScrapedBusiness:
        loc = place.get("location", {}) or {}
        name = (place.get("displayName") or {}).get("text") or "Unknown"

        city = state = postal = country = None
        for comp in place.get("addressComponents", []) or []:
            types = set(comp.get("types", []))
            if "locality" in types:
                city = comp.get("longText")
            elif "administrative_area_level_1" in types:
                state = comp.get("longText")
            elif "postal_code" in types:
                postal = comp.get("longText")
            elif "country" in types:
                country = comp.get("longText")

        return ScrapedBusiness(
            source=self.source_name,
            source_id=place.get("id") or place.get("googleMapsUri") or name,
            name=name,
            category=place.get("primaryType"),
            address=place.get("formattedAddress"),
            city=city,
            state=state,
            postal_code=postal,
            country=country,
            phone=place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber"),
            website=place.get("websiteUri"),
            rating=place.get("rating"),
            reviews_count=place.get("userRatingCount"),
            latitude=loc.get("latitude"),
            longitude=loc.get("longitude"),
            source_url=place.get("googleMapsUri"),
            raw_query=query.full_query(),
            extra={"types": place.get("types", [])},
        )
