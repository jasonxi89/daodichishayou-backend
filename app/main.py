import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import (
    CRAWL_INTERVAL_HOURS,
    CRAWL_SCHEDULE_HOURS,
    CRAWL_USE_SMART_SCHEDULE,
    RECIPE_SCRAPE_INTERVAL_DAYS,
)
from app.crawler.scheduler import scheduled_crawl, scheduled_recipe_scrape, seed_data
from app.database import Base, engine
from app.routers import trending, recommend, recipe
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

    if CRAWL_USE_SMART_SCHEDULE:
        # 智能调度：在指定时间点执行（默认饭点前 + 早晚）
        for time_str in CRAWL_SCHEDULE_HOURS.split(","):
            time_str = time_str.strip()
            hour, minute = time_str.split(":")
            scheduler.add_job(
                scheduled_crawl,
                "cron",
                hour=int(hour),
                minute=int(minute),
                timezone="Asia/Shanghai",
                id=f"food_crawl_{time_str}",
                replace_existing=True,
            )
        logging.getLogger(__name__).info(
            "智能调度已启用: %s (CST)", CRAWL_SCHEDULE_HOURS
        )
    else:
        # 传统固定间隔模式
        scheduler.add_job(
            scheduled_crawl,
            "interval",
            hours=CRAWL_INTERVAL_HOURS,
            id="food_crawl",
            replace_existing=True,
        )

    scheduler.add_job(
        scheduled_recipe_scrape,
        "interval",
        days=RECIPE_SCRAPE_INTERVAL_DAYS,
        id="recipe_scrape",
        replace_existing=True,
    )
    scheduler.start()
    yield
    # Shutdown
    scheduler.shutdown(wait=False)


from app.config import APP_VERSION

app = FastAPI(
    title="到底吃啥哟 - 美食热度API",
    version=APP_VERSION,
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
app.include_router(recipe.router)


@app.get("/api/health", response_model=HealthResponse)
def health_check():
    return HealthResponse(status="ok", version=APP_VERSION)
