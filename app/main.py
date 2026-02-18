import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CRAWL_INTERVAL_HOURS
from app.crawler.scheduler import scheduled_crawl, seed_data
from app.database import Base, engine
from app.routers import trending, recommend
from app.schemas import HealthResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    Base.metadata.create_all(bind=engine)
    seed_data()
    scheduler.add_job(
        scheduled_crawl,
        "interval",
        hours=CRAWL_INTERVAL_HOURS,
        id="food_crawl",
        replace_existing=True,
    )
    scheduler.start()
    yield
    # Shutdown
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="到底吃啥哟 - 美食热度API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(trending.router)
app.include_router(recommend.router)


@app.get("/api/health", response_model=HealthResponse)
def health_check():
    return HealthResponse(status="ok", version="0.1.0")
