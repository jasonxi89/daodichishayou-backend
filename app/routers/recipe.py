import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.crawler.scheduler import run_recipe_scrapers
from app.database import get_db
from app.models import Recipe
from app.schemas import CrawlResult, RecipeOut, RecipeSearchResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


@router.get("/search", response_model=RecipeSearchResponse)
def search_recipes(
    ingredients: str = Query(..., description="逗号分隔的食材列表"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """按食材搜索菜谱，按匹配数+评分+做过数排序。"""
    ingredient_list = [
        ing.strip() for ing in ingredients.split(",") if ing.strip()
    ]
    if not ingredient_list:
        return RecipeSearchResponse(total=0, items=[])

    # Build match count expression: count how many ingredients match
    match_cases = [
        func.iif(
            Recipe.ingredients_text.like(f"%{ing}%"), 1, 0
        )
        for ing in ingredient_list
    ]
    match_count = sum(match_cases)

    # Filter: at least one ingredient matches
    conditions = [
        Recipe.ingredients_text.like(f"%{ing}%")
        for ing in ingredient_list
    ]
    from sqlalchemy import or_
    stmt = (
        select(Recipe, match_count.label("match_count"))
        .where(or_(*conditions))
        .order_by(
            match_count.desc(),
            func.coalesce(Recipe.rating, 0).desc(),
            Recipe.made_count.desc(),
        )
        .limit(limit)
    )

    rows = db.execute(stmt).all()
    total = len(rows)
    items = [RecipeOut.model_validate(row[0]) for row in rows]

    return RecipeSearchResponse(total=total, items=items)


@router.get("", response_model=RecipeSearchResponse)
def list_recipes(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    category: str | None = Query(None),
    min_rating: float | None = Query(None, ge=0, le=10),
    db: Session = Depends(get_db),
):
    """浏览/筛选菜谱列表。"""
    stmt = select(Recipe)

    if category:
        stmt = stmt.where(Recipe.category == category)
    if min_rating is not None:
        stmt = stmt.where(Recipe.rating >= min_rating)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar() or 0

    stmt = stmt.order_by(
        func.coalesce(Recipe.rating, 0).desc(),
        Recipe.made_count.desc(),
    ).offset(offset).limit(limit)

    recipes = db.execute(stmt).scalars().all()
    items = [RecipeOut.model_validate(r) for r in recipes]

    return RecipeSearchResponse(total=total, items=items)


@router.post("/scrape", response_model=list[CrawlResult])
def trigger_recipe_scrape(db: Session = Depends(get_db)):
    """手动触发菜谱爬取。"""
    return run_recipe_scrapers(db)
