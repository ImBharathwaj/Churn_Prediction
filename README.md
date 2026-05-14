# Business Scraper

A small FastAPI web app that scrapes business listings from Google Maps and yellow page sites, stores the results in PostgreSQL, and lets you search/filter them in a clean UI.

You enter:

- **Business type** (e.g. `Restaurant`)
- **Business category** (optional, e.g. `Italian`)
- **Business area** (e.g. `Brooklyn NY` or `Bangalore`)

The app dispatches the configured scrapers in parallel as a background job, persists deduplicated results into Postgres, and renders them on the results page.

## Quick start

### 1. Start PostgreSQL

```bash
docker compose up -d
```

This launches Postgres 16 on `localhost:5433` (host port `5433` is used to avoid clashing with any system-installed Postgres on `5432`) with user/password/db all set to `scraper`.

### 2. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

The last command installs the Chromium binary used by the Google Maps scraper.

### 3. Configure environment

```bash
cp .env.example .env
# edit .env if you want different settings
```

Key variables:

| Variable | Description |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy async URL (default uses the docker-compose Postgres) |
| `SCRAPER_SOURCES` | Comma-separated sources: `google_maps`, `google_places`, `yellowpages` |
| `GOOGLE_PLACES_API_KEY` | **Recommended.** Enables the ToS-compliant Google Places source |
| `PLAYWRIGHT_HEADLESS` | `false` to watch the browser drive Google Maps |
| `MAX_RESULTS_PER_SOURCE` | Cap on how many results each source returns per job |

### 4. Run the app

```bash
uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000>.

## How it works

```
┌─────────────┐    POST /scrape    ┌──────────────────┐    parallel    ┌──────────────────┐
│  Search UI  ├───────────────────▶│  FastAPI routes  ├───────────────▶│  Scraper plugins │
└─────────────┘                    └────────┬─────────┘                └────────┬─────────┘
                                            │                                    │
                                            ▼                                    ▼
                                  ┌─────────────────────────┐         ┌──────────────────────┐
                                  │  ScrapeJob row created  │         │ Google Maps          │
                                  │  Status polling page    │         │ Google Places (API)  │
                                  └────────────┬────────────┘         │ YellowPages.com      │
                                               │                       └──────────┬───────────┘
                                               ▼                                  │
                                  ┌─────────────────────────┐                     │
                                  │  Business rows upserted │◀────────────────────┘
                                  │  in PostgreSQL          │
                                  └─────────────────────────┘
```

- **`app/main.py`** — FastAPI routes, server-rendered Jinja2 templates.
- **`app/models.py`** — `ScrapeJob` and `Business` SQLAlchemy models.
- **`app/scrapers/`** — pluggable scrapers, each implementing `BaseScraper`.
- **`app/services/scraper_service.py`** — orchestrates sources in parallel, dedupes by `(source, source_id)`, and upserts via Postgres `ON CONFLICT`.

### Adding a new source

1. Create `app/scrapers/myscraper.py` with a class extending `BaseScraper`.
2. Register it in `app/scrapers/__init__.py` (`get_scraper` factory and `AVAILABLE_SOURCES`).
3. Add it to `AVAILABLE_SOURCES` in `app/main.py`.

## Important: legal and ethical notes

- **Google Maps scraping violates Google's Terms of Service.** The `google_maps` source is provided for educational/local-research use and may break at any time. For production, set `GOOGLE_PLACES_API_KEY` and use the `google_places` source instead — it returns the same kinds of data via Google's official API.
- **Respect robots.txt and rate limits.** The yellow pages scraper sleeps between requests; don't crank `MAX_RESULTS_PER_SOURCE` to abusive levels.
- **Personal data.** Business listings can contain personal phone/email of sole proprietors. Comply with applicable laws (GDPR, CCPA, etc.).

## API reference

- `GET /` — search form + recent jobs.
- `POST /scrape` — start a new scrape job (form-encoded). Redirects to `/jobs/{id}`.
- `GET /jobs/{id}` — job detail page with results and filter.
- `GET /jobs/{id}/status` — JSON job status (used by the page to live-refresh).
- `GET /businesses` — searchable index of every business across all jobs.
- `GET /healthz` — health check.

## Schema

The `businesses` table is keyed by `(source, source_id)` so re-running a query updates existing rows rather than duplicating them. Each row links back to the `scrape_jobs` row that produced it (`job_id`).

## Development

```bash
# run server with reload
uvicorn app.main:app --reload

# tail Postgres
docker compose logs -f postgres

# psql shell
docker compose exec postgres psql -U scraper -d scraper
```

## Troubleshooting

- **`playwright._impl._errors.Error: Executable doesn't exist`** — you forgot `python -m playwright install chromium`.
- **Google Maps returns 0 results** — Google may be presenting a consent/captcha page. Try `PLAYWRIGHT_HEADLESS=false` to see what's happening, or switch to `google_places`.
- **YellowPages returns 0 results** — they sometimes serve a CAPTCHA page from datacenter IPs. Try from a residential connection or use a different user agent.
- **`asyncpg` connection errors** — confirm Postgres is up (`docker compose ps`) and `DATABASE_URL` matches.
