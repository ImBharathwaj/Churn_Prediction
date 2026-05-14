from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session, init_db
from app.models import Business, JobStatus, ScrapeJob
from app.services.scraper_service import run_scrape_job

settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scraper")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up; initializing database schema.")
    await init_db()
    yield
    logger.info("Shutting down.")


app = FastAPI(title="Business Scraper", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


AVAILABLE_SOURCES = ["google_maps", "google_places", "yellowpages"]


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, session: Annotated[AsyncSession, Depends(get_session)]):
    recent = await session.execute(
        select(ScrapeJob).order_by(ScrapeJob.created_at.desc()).limit(10)
    )
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "available_sources": AVAILABLE_SOURCES,
            "default_sources": settings.enabled_sources,
            "recent_jobs": list(recent.scalars().all()),
            "has_places_key": bool(settings.google_places_api_key),
        },
    )


@app.post("/scrape")
async def start_scrape(
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
    business_type: str = Form(...),
    business_category: str = Form(""),
    business_area: str = Form(...),
    sources: list[str] | None = Form(default=None),
):
    business_type = business_type.strip()
    business_area = business_area.strip()
    if not business_type or not business_area:
        raise HTTPException(status_code=400, detail="business_type and business_area are required")

    if not sources:
        sources = settings.enabled_sources

    sources = [s for s in sources if s in AVAILABLE_SOURCES]
    if not sources:
        raise HTTPException(status_code=400, detail="At least one valid source must be selected")

    job = ScrapeJob(
        business_type=business_type,
        business_category=business_category.strip() or None,
        business_area=business_area,
        sources=",".join(sources),
        status=JobStatus.pending,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    background_tasks.add_task(run_scrape_job, job.id)

    return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(
    job_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = Query(default=None),
):
    job = await session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    stmt = select(Business).where(Business.job_id == job.id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Business.name).like(like),
                func.lower(Business.category).like(like),
                func.lower(Business.address).like(like),
            )
        )
    stmt = stmt.order_by(Business.rating.desc().nulls_last(), Business.name.asc())

    res = await session.execute(stmt)
    businesses = list(res.scalars().all())

    count_res = await session.execute(
        select(func.count()).select_from(Business).where(Business.job_id == job.id)
    )
    total = count_res.scalar_one()

    return templates.TemplateResponse(
        "job_detail.html",
        {
            "request": request,
            "job": job,
            "businesses": businesses,
            "total": total,
            "q": q or "",
        },
    )


@app.get("/jobs/{job_id}/status")
async def job_status(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    job = await session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(
        {
            "id": str(job.id),
            "status": job.status.value,
            "total_results": job.total_results,
            "error_message": job.error_message,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }
    )


@app.get("/businesses", response_class=HTMLResponse)
async def businesses_index(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = Query(default=None),
    source: str | None = Query(default=None),
    city: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    stmt = select(Business)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Business.name).like(like),
                func.lower(Business.category).like(like),
                func.lower(Business.address).like(like),
            )
        )
    if source:
        stmt = stmt.where(Business.source == source)
    if city:
        stmt = stmt.where(func.lower(Business.city) == city.lower())

    stmt = stmt.order_by(Business.created_at.desc()).limit(limit)
    res = await session.execute(stmt)
    businesses = list(res.scalars().all())

    return templates.TemplateResponse(
        "businesses.html",
        {
            "request": request,
            "businesses": businesses,
            "q": q or "",
            "source": source or "",
            "city": city or "",
            "available_sources": AVAILABLE_SOURCES,
        },
    )


@app.get("/healthz")
async def healthz():
    return {"ok": True}
