import json
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import FoodDigest, FoodTrend, FoodTrendSnapshot
from app.schemas import (
    CrawlResult,
    FoodDigestOut,
    FoodTrendImport,
    FoodTrendOut,
    FoodTrendSnapshotOut,
    TrendHistoryResponse,
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


@router.get("/digest", response_model=FoodDigestOut | None)
def get_digest(
    target_date: date | None = Query(None, alias="date"),
    db: Session = Depends(get_db),
):
    """获取指定日期的美食趋势快报，默认今日。"""
    target = target_date or date.today()
    digest = db.execute(
        select(FoodDigest).where(FoodDigest.digest_date == target)
    ).scalar_one_or_none()

    if not digest:
        return None

    # 反序列化 top_foods 供 response_model 使用
    digest._top_foods_list = json.loads(digest.top_foods)
    return FoodDigestOut(
        id=digest.id,
        digest_date=digest.digest_date,
        summary=digest.summary,
        top_foods=json.loads(digest.top_foods),
        recommendation=digest.recommendation,
        updated_at=digest.updated_at,
    )


@router.get("/history/{food_name}", response_model=TrendHistoryResponse)
def get_food_history(
    food_name: str,
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """查询某食物最近 N 天的热度历史。"""
    snapshots = (
        db.execute(
            select(FoodTrendSnapshot)
            .where(FoodTrendSnapshot.food_name == food_name)
            .order_by(FoodTrendSnapshot.snapshot_date.desc())
            .limit(days)
        )
        .scalars()
        .all()
    )
    return TrendHistoryResponse(food_name=food_name, history=snapshots)
