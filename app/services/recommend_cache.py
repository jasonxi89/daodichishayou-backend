"""Persistence helpers for full recommendation payloads."""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import RecommendCache
from app.schemas import IngredientRecommendResponse

logger = logging.getLogger(__name__)

RECOMMEND_CACHE_TTL = timedelta(days=7)


def make_cache_key(ingredients: list[str], count: int) -> str:
    """Return a stable key for an ingredient set and requested dish count."""
    normalized = sorted(
        "".join(ingredient.lower().split())
        for ingredient in ingredients
        if ingredient and ingredient.strip()
    )
    return f"{'|'.join(normalized)}#c{count}"


def get_cached_recommendation(
    db: Session,
    cache_key: str,
    now: datetime,
) -> IngredientRecommendResponse | None:
    cached = (
        db.query(RecommendCache)
        .filter(
            RecommendCache.cache_key == cache_key,
            RecommendCache.expires_at > now,
        )
        .first()
    )
    if not cached:
        return None

    try:
        return IngredientRecommendResponse.model_validate_json(cached.payload)
    except ValueError:
        logger.warning("Ignoring invalid recommend cache payload: %s", cache_key)
        return None


def store_recommendation(
    db: Session,
    cache_key: str,
    response: IngredientRecommendResponse,
    model: str,
    now: datetime,
) -> None:
    payload = response.model_dump_json()
    cached = (
        db.query(RecommendCache)
        .filter(RecommendCache.cache_key == cache_key)
        .first()
    )
    if cached:
        cached.payload = payload
        cached.model = model
        cached.created_at = now
        cached.expires_at = now + RECOMMEND_CACHE_TTL
    else:
        db.add(
            RecommendCache(
                cache_key=cache_key,
                payload=payload,
                model=model,
                created_at=now,
                expires_at=now + RECOMMEND_CACHE_TTL,
            )
        )
    db.commit()
