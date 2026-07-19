"""Best-effort recommendation fallbacks used when the LLM is unavailable."""

import json
from collections.abc import Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Recipe, RecommendCache
from app.schemas import IngredientRecommendResponse, RecommendedDish


def _normalized_ingredient(value: str) -> str:
    return "".join(value.lower().split())


def _unique_dishes(
    dishes: Iterable[RecommendedDish],
    exclude_dishes: set[str],
    count: int,
) -> list[RecommendedDish]:
    selected: list[RecommendedDish] = []
    seen = set(exclude_dishes)
    for dish in dishes:
        if not dish.name or dish.name in seen:
            continue
        selected.append(dish)
        seen.add(dish.name)
        if len(selected) >= count:
            break
    return selected


def _old_cache_dishes(
    db: Session,
    first_ingredient: str,
) -> list[RecommendedDish]:
    rows = (
        db.query(RecommendCache)
        .filter(
            RecommendCache.cache_key.like(f"%{first_ingredient}%")
        )
        .order_by(RecommendCache.created_at.desc())
        .all()
    )
    dishes: list[RecommendedDish] = []
    for row in rows:
        try:
            response = IngredientRecommendResponse.model_validate_json(
                row.payload
            )
        except ValueError:
            continue
        dishes.extend(response.dishes)
    return dishes


def _safe_json_list(raw_value: str | None) -> list:
    if not raw_value:
        return []
    try:
        value = json.loads(raw_value)
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


def fallback_recipe_to_dish(recipe: Recipe) -> RecommendedDish:
    """Convert even an incomplete local recipe into a safe card payload."""
    ingredients_data = _safe_json_list(recipe.ingredients_json)
    steps_data = _safe_json_list(recipe.steps_json)
    ingredients = [
        (
            f"{item.get('name', '')} {item.get('amount', '适量')}".strip()
            if isinstance(item, dict)
            else str(item)
        )
        for item in ingredients_data
    ]
    if not ingredients and recipe.ingredients_text:
        ingredients = recipe.ingredients_text.split()

    steps = [
        step.get("text", "") if isinstance(step, dict) else str(step)
        for step in steps_data
    ]
    return RecommendedDish(
        name=recipe.name,
        summary="点开看详细做法",
        ingredients=[value for value in ingredients if value],
        steps=[value for value in steps if value],
    )


def _local_fallback_dishes(
    db: Session,
    ingredients: list[str],
    exclude_dishes: set[str],
    count: int,
) -> list[RecommendedDish]:
    conditions = [
        Recipe.ingredients_text.like(f"%{ingredient}%")
        for ingredient in ingredients
    ]
    if not conditions:
        return []

    stmt = select(Recipe).where(
        or_(*conditions),
        Recipe.ingredients_text.isnot(None),
    )
    if exclude_dishes:
        stmt = stmt.where(Recipe.name.notin_(exclude_dishes))
    stmt = stmt.order_by(
        func.coalesce(Recipe.rating, 0).desc(),
        Recipe.made_count.desc(),
    ).limit(count)
    return [
        fallback_recipe_to_dish(recipe)
        for recipe in db.execute(stmt).scalars().all()
    ]


def get_fallback_recommendation(
    db: Session,
    ingredients: list[str],
    count: int,
    exclude_dishes: list[str] | None = None,
) -> IngredientRecommendResponse | None:
    """Try old full caches, then incomplete local recipes."""
    if not ingredients:
        return None

    excluded = set(exclude_dishes or [])
    first_ingredient = _normalized_ingredient(ingredients[0])
    cached = _unique_dishes(
        _old_cache_dishes(db, first_ingredient),
        excluded,
        count,
    )
    if cached:
        return IngredientRecommendResponse(
            dishes=cached,
            input_ingredients=ingredients,
        )

    local = _unique_dishes(
        _local_fallback_dishes(db, ingredients, excluded, count),
        excluded,
        count,
    )
    if local:
        return IngredientRecommendResponse(
            dishes=local,
            input_ingredients=ingredients,
        )
    return None
