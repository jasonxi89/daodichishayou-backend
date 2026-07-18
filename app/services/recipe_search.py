"""Local recipe search and API-shape conversion."""

import json

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Recipe
from app.schemas import RecommendedDish


def recipe_to_dish(recipe: Recipe) -> RecommendedDish:
    """Convert a recipe with complete steps to a recommendation."""
    ingredients_data = json.loads(recipe.ingredients_json)
    steps_data = json.loads(recipe.steps_json)
    return RecommendedDish(
        name=recipe.name,
        summary="经典家常做法",
        ingredients=[
            f"{item['name']} {item.get('amount', '适量')}"
            for item in ingredients_data
        ],
        steps=[
            step["text"] if isinstance(step, dict) else str(step)
            for step in steps_data
        ],
    )


def search_local_recipes(
    db: Session,
    ingredients: list[str],
    count: int,
    exclude_dishes: list[str] | None = None,
) -> list[RecommendedDish]:
    """Search complete local recipes by ingredient overlap."""
    match_cases = [
        func.iif(Recipe.ingredients_text.like(f"%{ingredient}%"), 1, 0)
        for ingredient in ingredients
    ]
    match_count = sum(match_cases)
    conditions = [
        Recipe.ingredients_text.like(f"%{ingredient}%")
        for ingredient in ingredients
    ]
    stmt = select(Recipe, match_count.label("match_count")).where(
        or_(*conditions),
        Recipe.ingredients_text.isnot(None),
        Recipe.steps_json.isnot(None),
    )
    if exclude_dishes:
        stmt = stmt.where(Recipe.name.notin_(exclude_dishes))

    stmt = stmt.order_by(
        match_count.desc(),
        func.coalesce(Recipe.rating, 0).desc(),
        Recipe.made_count.desc(),
    ).limit(count)
    return [recipe_to_dish(row[0]) for row in db.execute(stmt).all()]
