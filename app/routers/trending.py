import asyncio
import json
import logging
from datetime import date, datetime, time, timezone

import openai
from openai import OpenAI
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import (
    LLM_TIMEOUT_SECONDS,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
)
from app.database import get_db
from app.models import CategoryNote, FoodDigest, FoodTrend, FoodTrendSnapshot
from app.routers.recommend import _strip_code_fence
from app.schemas import (
    AnnotatedCategoriesResponse,
    AnnotatedCategory,
    CrawlResult,
    FoodDigestOut,
    FoodTrendImport,
    FoodTrendOut,
    FoodTrendSnapshotOut,
    TrendHistoryResponse,
    TrendingResponse,
)

logger = logging.getLogger(__name__)

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


CATEGORY_NOTES_PROMPT = """你是一位中文文案高手。为每个美食分类写一句 4-6 个字的俏皮小注，用在菜单格子的分类名下方。
风格参考（已定稿，勿改动这些示例本身）：随便→大厨看着办、家常下饭→妈妈味道、嗦粉吃面→一碗入魂、火锅烫涮→咕嘟咕嘟、烧烤撸串→滋滋冒油、奶茶续命→快乐水源、深夜食堂→灯火可亲。
要求：每条 4-6 个中文字，贴合分类气质，不含 emoji、标点、英文。
返回格式（纯JSON，无markdown）：{"分类名1": "小注1", "分类名2": "小注2"}"""


def _extract_json_object(raw_text: str) -> str:
    """截取首个 { 到末个 } 的子串，容忍模型在 JSON 前后加说明文字。"""
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end <= start:
        return ""
    return raw_text[start:end + 1]


def generate_category_notes_via_llm(categories: list[str]) -> dict[str, str]:
    """Synchronously generate menu-cell notes for the given categories."""
    client = OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
        timeout=LLM_TIMEOUT_SECONDS,
    )
    categories_text = "、".join(f"「{category}」" for category in categories)
    message = client.chat.completions.create(
        model=OPENROUTER_MODEL,
        max_tokens=2048,
        messages=[
            {"role": "system", "content": CATEGORY_NOTES_PROMPT},
            {"role": "user", "content": f"请为这些分类各写一条小注：{categories_text}。"},
        ],
    )
    raw_text = _strip_code_fence((message.choices[0].message.content or "").strip())
    json_text = _extract_json_object(raw_text)
    if not json_text:
        logger.warning("分类小注响应中无 JSON 对象，响应前 200 字: %r", raw_text[:200])
        return {}
    data = json.loads(json_text)
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and v.strip()}


@router.get("/categories/annotated", response_model=AnnotatedCategoriesResponse)
async def get_categories_annotated(db: Session = Depends(get_db)):
    """分类列表 + 菜单格小注；缺失的小注用 LLM 一次性补齐入库，失败时 note 为 null。"""
    names = sorted(
        db.execute(
            select(FoodTrend.category)
            .where(FoodTrend.category.is_not(None))
            .distinct()
        )
        .scalars()
        .all()
    )
    notes: dict[str, str] = {
        row.category: row.note
        for row in db.query(CategoryNote).filter(CategoryNote.category.in_(names))
    }
    missing = [name for name in names if name not in notes]
    if missing and OPENROUTER_API_KEY:
        try:
            generated = await asyncio.to_thread(
                generate_category_notes_via_llm, missing
            )
            for category in missing:
                note = (generated.get(category) or "").strip()[:20]
                if note:
                    db.add(CategoryNote(category=category, note=note))
                    notes[category] = note
            db.commit()
        except (openai.OpenAIError, json.JSONDecodeError) as e:
            db.rollback()
            logger.warning("分类小注生成失败，本次返回 null: %s", e)
    return AnnotatedCategoriesResponse(
        categories=[
            AnnotatedCategory(name=name, note=notes.get(name)) for name in names
        ]
    )


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
