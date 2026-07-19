"""Persistence helpers for full recommendation payloads."""

import hashlib
import json
import logging
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.models import RecommendCache
from app.schemas import IngredientRecommendResponse

logger = logging.getLogger(__name__)

RECOMMEND_CACHE_TTL = timedelta(days=7)


def normalize_ingredients(ingredients: list[str]) -> list[str]:
    """Normalize an ingredient set without destroying meaningful spaces."""
    return sorted({
        " ".join(
            unicodedata.normalize("NFKC", ingredient).strip().casefold().split()
        )
        for ingredient in ingredients
        if ingredient and ingredient.strip()
    })


def make_cache_key(ingredients: list[str], count: int) -> str:
    """Return a collision-resistant key for ingredients and dish count."""
    normalized = normalize_ingredients(ingredients)
    canonical = json.dumps(
        {
            "version": 2,
            "ingredients": normalized,
            "count": count,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"recommend:v2:{digest}"


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


def store_recommendation_on_bind(
    bind: Engine | Connection,
    cache_key: str,
    response: IngredientRecommendResponse,
    model: str,
    now: datetime,
) -> None:
    """Persist through a worker-owned Session bound to the existing engine."""
    with Session(bind=bind) as session:
        store_recommendation(
            session,
            cache_key,
            response,
            model,
            now,
        )


def store_recommendation(
    db: Session,
    cache_key: str,
    response: IngredientRecommendResponse,
    model: str,
    now: datetime,
) -> None:
    payload = response.model_dump_json()
    expires_at = now + RECOMMEND_CACHE_TTL
    statement = insert(RecommendCache).values(
        cache_key=cache_key,
        payload=payload,
        model=model,
        created_at=now,
        expires_at=expires_at,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[RecommendCache.cache_key],
        set_={
            "payload": payload,
            "model": model,
            "created_at": now,
            "expires_at": expires_at,
        },
    )
    db.execute(statement)
    db.commit()
