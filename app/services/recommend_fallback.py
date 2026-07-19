"""Best-effort recommendation fallbacks used when the LLM is unavailable."""

from collections.abc import Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Recipe, RecommendCache
from app.schemas import IngredientRecommendResponse, RecommendedDish
from app.services.recipe_search import collect_valid_recipes


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
        normalized_inputs = {
            _normalized_ingredient(value)
            for value in response.input_ingredients
        }
        if first_ingredient in normalized_inputs:
            dishes.extend(response.dishes)
    return dishes


def _local_fallback_dishes(
    db: Session,
    ingredients: list[str],
    exclude_dishes: set[str],
    count: int,
    require_complete: bool,
) -> list[RecommendedDish]:
    conditions = [
        Recipe.ingredients_text.contains(ingredient, autoescape=True)
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
    stmt = stmt.where(
        Recipe.ingredients_json.isnot(None),
        Recipe.steps_json.isnot(None),
    ).order_by(
        func.coalesce(Recipe.rating, 0).desc(),
        Recipe.made_count.desc(),
        Recipe.id.asc(),
    )
    return collect_valid_recipes(db, stmt, count)


def get_fallback_recommendation(
    db: Session,
    ingredients: list[str],
    count: int,
    exclude_dishes: list[str] | None = None,
    *,
    require_complete: bool = False,
) -> IngredientRecommendResponse | None:
    """Try old full caches, then incomplete local recipes."""
    if not ingredients:
        return None

    excluded = set(exclude_dishes or [])
    first_ingredient = _normalized_ingredient(ingredients[0])
    cached_candidates = _old_cache_dishes(db, first_ingredient)
    if require_complete:
        cached_candidates = [
            dish
            for dish in cached_candidates
            if dish.ingredients and dish.steps
        ]
    cached = _unique_dishes(
        cached_candidates,
        excluded,
        count,
    )
    if cached:
        return IngredientRecommendResponse(
            dishes=cached,
            input_ingredients=ingredients,
        )

    local = _unique_dishes(
        _local_fallback_dishes(
            db,
            ingredients,
            excluded,
            count,
            require_complete,
        ),
        excluded,
        count,
    )
    if local:
        return IngredientRecommendResponse(
            dishes=local,
            input_ingredients=ingredients,
        )
    return None
