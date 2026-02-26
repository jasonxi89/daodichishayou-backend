from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import FoodTrend
from app.schemas import (
    CrawlResult,
    FoodTrendImport,
    FoodTrendOut,
    TrendingResponse,
)

router = APIRouter(prefix="/api/trending", tags=["trending"])


@router.get("", response_model=TrendingResponse)
def get_trending(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    source: str | None = Query(None),
    category: str | None = Query(None),
    db: Session = Depends(get_db),
):
    stmt = select(FoodTrend)
    count_stmt = select(func.count(FoodTrend.id))

    if source:
        stmt = stmt.where(FoodTrend.source == source)
        count_stmt = count_stmt.where(FoodTrend.source == source)
    if category:
        stmt = stmt.where(FoodTrend.category == category)
        count_stmt = count_stmt.where(FoodTrend.category == category)

    total = db.execute(count_stmt).scalar() or 0
    items = (
        db.execute(
            stmt.order_by(FoodTrend.heat_score.desc()).offset(offset).limit(limit)
        )
        .scalars()
        .all()
    )
    return TrendingResponse(total=total, items=items)


@router.get("/categories", response_model=list[str])
def get_categories(db: Session = Depends(get_db)):
    rows = (
        db.execute(
            select(FoodTrend.category)
            .where(FoodTrend.category.is_not(None))
            .distinct()
        )
        .scalars()
        .all()
    )
    return sorted(rows)


@router.get("/sources", response_model=list[str])
def get_sources(db: Session = Depends(get_db)):
    rows = db.execute(select(FoodTrend.source).distinct()).scalars().all()
    return sorted(rows)


@router.post("/crawl", response_model=list[CrawlResult])
def trigger_crawl(db: Session = Depends(get_db)):
    from app.crawler.scheduler import run_all_crawlers

    results = run_all_crawlers(db)
    return results


@router.post("/import", response_model=list[FoodTrendOut])
def import_data(
    items: list[FoodTrendImport],
    db: Session = Depends(get_db),
):
    created = []
    for item in items:
        existing = db.execute(
            select(FoodTrend).where(
                FoodTrend.food_name == item.food_name,
                FoodTrend.source == item.source,
            )
        ).scalar_one_or_none()

        if existing:
            existing.heat_score = item.heat_score
            existing.post_count = item.post_count
            existing.category = item.category
            existing.image_url = item.image_url
            existing.updated_at = datetime.now(timezone.utc)
            created.append(existing)
        else:
            record = FoodTrend(**item.model_dump())
            db.add(record)
            db.flush()
            created.append(record)

    db.commit()
    for r in created:
        db.refresh(r)
    return created
