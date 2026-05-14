from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.database import session_scope
from app.models import Business, JobStatus, ScrapeJob
from app.scrapers import BaseScraper, ScrapedBusiness, ScrapeQuery, get_scraper

logger = logging.getLogger(__name__)


async def run_scrape_job(job_id: uuid.UUID) -> None:
    """Run a scrape job end-to-end: dispatch sources in parallel, persist results."""
    settings = get_settings()

    async with session_scope() as session:
        job = await session.get(ScrapeJob, job_id)
        if not job:
            logger.error("Job %s not found", job_id)
            return

        job.status = JobStatus.running
        job.started_at = datetime.now(timezone.utc)
        await session.flush()

        sources = [s.strip() for s in job.sources.split(",") if s.strip()]
        query = ScrapeQuery(
            business_type=job.business_type,
            business_category=job.business_category,
            business_area=job.business_area,
            max_results=settings.max_results_per_source,
        )

    scrapers: list[BaseScraper] = []
    for source in sources:
        try:
            scrapers.append(get_scraper(source))
        except ValueError as e:
            logger.warning("Skipping unknown source %r: %s", source, e)

    if not scrapers:
        await _finalize_job(job_id, status=JobStatus.failed, error="No valid sources configured")
        return

    async def _run_one(scraper: BaseScraper) -> list[ScrapedBusiness]:
        try:
            return await scraper.scrape(query)
        except Exception as e:  # noqa: BLE001
            logger.exception("Scraper %s failed: %s", scraper.source_name, e)
            return []

    try:
        results_by_source = await asyncio.gather(*[_run_one(s) for s in scrapers])
    except Exception as e:  # noqa: BLE001
        await _finalize_job(job_id, status=JobStatus.failed, error=str(e))
        return

    all_results: list[ScrapedBusiness] = [b for batch in results_by_source for b in batch]
    deduped = _dedupe(all_results)

    inserted = await _persist(job_id, deduped)
    await _finalize_job(job_id, status=JobStatus.completed, total=inserted)


def _dedupe(items: list[ScrapedBusiness]) -> list[ScrapedBusiness]:
    """Dedupe within the same source by source_id."""
    seen: set[tuple[str, str]] = set()
    out: list[ScrapedBusiness] = []
    for item in items:
        key = (item.source, item.source_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


async def _persist(job_id: uuid.UUID, items: list[ScrapedBusiness]) -> int:
    if not items:
        return 0

    rows = [
        {
            "job_id": job_id,
            "name": b.name,
            "category": b.category,
            "address": b.address,
            "city": b.city,
            "state": b.state,
            "postal_code": b.postal_code,
            "country": b.country,
            "phone": b.phone,
            "website": b.website,
            "email": b.email,
            "rating": b.rating,
            "reviews_count": b.reviews_count,
            "latitude": b.latitude,
            "longitude": b.longitude,
            "source": b.source,
            "source_id": b.source_id,
            "source_url": b.source_url,
            "raw_query": b.raw_query,
            "extra": json.dumps(b.extra) if b.extra else None,
        }
        for b in items
    ]

    async with session_scope() as session:
        stmt = pg_insert(Business).values(rows)
        update_cols = {
            c.name: stmt.excluded[c.name]
            for c in Business.__table__.columns
            if c.name not in ("id", "created_at", "source", "source_id")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "source_id"],
            set_=update_cols,
        )
        await session.execute(stmt)

    return len(rows)


async def _finalize_job(
    job_id: uuid.UUID,
    *,
    status: JobStatus,
    total: int = 0,
    error: str | None = None,
) -> None:
    async with session_scope() as session:
        job = await session.get(ScrapeJob, job_id)
        if not job:
            return
        job.status = status
        job.finished_at = datetime.now(timezone.utc)
        if status == JobStatus.completed:
            job.total_results = total
        if error:
            job.error_message = error[:2000]


async def list_recent_jobs(limit: int = 20) -> list[ScrapeJob]:
    async with session_scope() as session:
        result = await session.execute(
            select(ScrapeJob).order_by(ScrapeJob.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())
