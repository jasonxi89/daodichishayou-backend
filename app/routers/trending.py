import json
from datetime import date, datetime, time, timezone

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
    aggregate: bool = Query(True, description="按 canonical_name 聚合去重"),
    db: Session = Depends(get_db),
):
    if aggregate:
        return _get_trending_aggregated(db, limit, offset, source, category)
    return _get_trending_raw(db, limit, offset, source, category)


def _get_trending_raw(
    db: Session,
    limit: int,
    offset: int,
    source: str | None,
    category: str | None,
) -> TrendingResponse:
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


def _aggregate_group_key():
    """聚合分组键：canonical_name 为空(NULL/空串)时回退 food_name。"""
    return func.coalesce(
        func.nullif(FoodTrend.canonical_name, ""), FoodTrend.food_name
    )


def _get_trending_aggregated(
    db: Session,
    limit: int,
    offset: int,
    source: str | None,
    category: str | None,
) -> TrendingResponse:
    """SQL 层聚合：group by 规范名，按组内最高热度排序后在 SQL 层分页。

    排序语义与旧内存实现一致：max(heat_score) 降序，
    平分时按组首次出现顺序（即组内最小 id）升序。
    """
    group_key = _aggregate_group_key()
    max_heat = func.max(FoodTrend.heat_score).label("max_heat")
    first_id = func.min(FoodTrend.id).label("first_id")

    agg_stmt = select(group_key.label("canonical"), max_heat, first_id).group_by(
        group_key
    )
    if source:
        agg_stmt = agg_stmt.where(FoodTrend.source == source)
    if category:
        agg_stmt = agg_stmt.where(FoodTrend.category == category)

    total = db.execute(
        select(func.count()).select_from(agg_stmt.subquery())
    ).scalar() or 0

    page_keys = [
        row.canonical
        for row in db.execute(
            agg_stmt.order_by(max_heat.desc(), first_id.asc())
            .offset(offset)
            .limit(limit)
        )
    ]
    if not page_keys:
        return TrendingResponse(total=total, items=[])

    # 补充查询：只取当前页各组的明细行，用于别名/来源/代表行等展示字段
    detail_stmt = (
        select(FoodTrend).where(group_key.in_(page_keys)).order_by(FoodTrend.id)
    )
    if source:
        detail_stmt = detail_stmt.where(FoodTrend.source == source)
    if category:
        detail_stmt = detail_stmt.where(FoodTrend.category == category)

    groups: dict[str, list[FoodTrend]] = {key: [] for key in page_keys}
    for row in db.execute(detail_stmt).scalars():
        groups[row.canonical_name or row.food_name].append(row)

    items = [_build_aggregated_item(key, groups[key]) for key in page_keys]
    return TrendingResponse(total=total, items=items)


def _build_aggregated_item(canonical: str, rows: list[FoodTrend]) -> FoodTrendOut:
    """把同一规范名的明细行合成一条聚合结果（代表行取组内最高热度）。"""
    top = max(rows, key=lambda r: r.heat_score)
    return FoodTrendOut(
        id=top.id,
        food_name=top.food_name,
        source=top.source,
        heat_score=top.heat_score,
        post_count=sum(r.post_count for r in rows),
        category=top.category,
        image_url=top.image_url,
        updated_at=top.updated_at,
        canonical_name=canonical,
        aliases=sorted({r.food_name for r in rows}),
        sources=sorted({r.source for r in rows}),
        trend_type=next((r.trend_type for r in rows if r.trend_type), None),
        trend_context=next((r.trend_context for r in rows if r.trend_context), None),
    )


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
    """获取美食趋势快报。无 date 参数时 fallback 到最新一条。"""
    if target_date is None:
        digest = db.execute(
            select(FoodDigest).order_by(FoodDigest.digest_date.desc()).limit(1)
        ).scalar_one_or_none()
    else:
        target_dt = datetime.combine(target_date, time.min)
        digest = db.execute(
            select(FoodDigest).where(FoodDigest.digest_date == target_dt)
        ).scalar_one_or_none()

    if not digest:
        return None

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
