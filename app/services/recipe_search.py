"""Strict local recipe parsing and ranked search helpers."""

import json
from collections.abc import Iterable
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.models import Recipe
from app.schemas import RecommendedDish

_BATCH_SIZE = 50


def _parse_ingredients(raw_value: str | None) -> list[str]:
    if not raw_value:
        raise ValueError("Recipe is missing structured ingredients")
    data = json.loads(raw_value)
    if not isinstance(data, list) or not data:
        raise ValueError("Recipe ingredients must be a non-empty list")

    ingredients: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Recipe ingredients must be objects")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Recipe ingredient names must not be blank")
        amount = item.get("amount", "适量")
        if amount is None:
            amount = "适量"
        if not isinstance(amount, str):
            amount = str(amount)
        ingredients.append(f"{name.strip()} {amount.strip()}".strip())
    return ingredients


def _parse_steps(raw_value: str | None) -> list[str]:
    if not raw_value:
        raise ValueError("Recipe is missing structured steps")
    data = json.loads(raw_value)
    if not isinstance(data, list) or not data:
        raise ValueError("Recipe steps must be a non-empty list")

    steps: list[str] = []
    for item in data:
        text: Any = item.get("text") if isinstance(item, dict) else item
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Recipe step text must not be blank")
        steps.append(text.strip())
    return steps


def recipe_to_dish(recipe: Recipe) -> RecommendedDish:
    """Convert one semantically complete local recipe."""
    return RecommendedDish(
        name=recipe.name,
        summary="经典家常做法",
        ingredients=_parse_ingredients(recipe.ingredients_json),
        steps=_parse_steps(recipe.steps_json),
    )


def collect_valid_recipes(
    db: Session,
    statement: Select,
    count: int,
) -> list[RecommendedDish]:
    """Scan ranked rows in batches until enough valid recipes are found."""
    dishes: list[RecommendedDish] = []
    offset = 0
    while len(dishes) < count:
        recipes: Iterable[Recipe] = db.execute(
            statement.offset(offset).limit(_BATCH_SIZE)
        ).scalars().all()
        batch = list(recipes)
        if not batch:
            break
        offset += len(batch)
        for recipe in batch:
            try:
                dishes.append(recipe_to_dish(recipe))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            if len(dishes) >= count:
                break
        if len(batch) < _BATCH_SIZE:
            break
    return dishes


def search_local_recipes(
    db: Session,
    ingredients: list[str],
    count: int,
    exclude_dishes: list[str] | None = None,
) -> list[RecommendedDish]:
    """Search complete local recipes by ingredient overlap."""
    if not ingredients:
        return []
    match_cases = [
        func.iif(
            Recipe.ingredients_text.contains(ingredient, autoescape=True),
            1,
            0,
        )
        for ingredient in ingredients
    ]
    match_count = sum(match_cases)
    conditions = [
        Recipe.ingredients_text.contains(ingredient, autoescape=True)
        for ingredient in ingredients
    ]
    statement = select(Recipe).where(
        or_(*conditions),
        Recipe.ingredients_text.isnot(None),
        Recipe.ingredients_json.isnot(None),
        Recipe.steps_json.isnot(None),
    )
    if exclude_dishes:
        statement = statement.where(Recipe.name.notin_(exclude_dishes))

    statement = statement.order_by(
        match_count.desc(),
        func.coalesce(Recipe.rating, 0).desc(),
        Recipe.made_count.desc(),
        Recipe.id.asc(),
    )
    return collect_valid_recipes(db, statement, count)
