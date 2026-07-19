import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import (
    CRAWL_INTERVAL_HOURS,
    CRAWL_SCHEDULE_HOURS,
    CRAWL_USE_SMART_SCHEDULE,
    PREGEN_ENABLED,
    RECIPE_SCRAPE_ENABLED,
    RECIPE_SCRAPE_INTERVAL_DAYS,
)
from app.crawler.scheduler import (
    scheduled_crawl,
    scheduled_pregeneration,
    scheduled_recipe_scrape,
    seed_data,
)
from app.database import Base, engine
from app.routers import admin, recipe, recommend, recommend_progressive, trending
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
    try:
        from app.migrations.backfill_v1_9_0 import migrate_v1_9_0
        migrate_v1_9_0(engine)
    except Exception:
        logging.getLogger(__name__).warning(
            "v1.9.0 迁移失败，跳过继续启动", exc_info=True
        )
    try:
        from app.migrations.add_steps_source import migrate_steps_source
        migrate_steps_source(engine)
    except Exception:
        logging.getLogger(__name__).warning(
            "steps_source 迁移失败，跳过继续启动", exc_info=True
        )
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

    if RECIPE_SCRAPE_ENABLED:
        scheduler.add_job(
            scheduled_recipe_scrape,
            "interval",
            days=RECIPE_SCRAPE_INTERVAL_DAYS,
            id="recipe_scrape",
            replace_existing=True,
        )
    if PREGEN_ENABLED:
        scheduler.add_job(
            scheduled_pregeneration,
            "cron",
            hour=3,
            minute=30,
            timezone="Asia/Shanghai",
            id="recommend_pregen",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
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
app.include_router(recommend_progressive.router)
app.include_router(recipe.router)
app.include_router(admin.router)


@app.get("/api/health", response_model=HealthResponse)
def health_check():
    return HealthResponse(status="ok", version=APP_VERSION)
