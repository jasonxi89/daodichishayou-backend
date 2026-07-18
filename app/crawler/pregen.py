"""Pre-generate full recommendations for the frontend's preset ingredients."""

import itertools
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Iterator

from sqlalchemy.orm import Session

from app.config import OPENROUTER_MODEL, PREGEN_DAILY_BUDGET
from app.models import RecommendCache
from app.routers.recommend import generate_dishes_via_llm, make_cache_key
from app.schemas import IngredientRecommendResponse

logger = logging.getLogger(__name__)

# Keep this list manually synchronized with the frontend COMMON_INGREDIENTS.
# Both repositories have pin tests; changing one side requires changing both.
PRESET_INGREDIENTS = [
    "番茄",
    "土豆",
    "白菜",
    "青椒",
    "黄瓜",
    "茄子",
    "西兰花",
    "胡萝卜",
    "菠菜",
    "洋葱",
    "蘑菇",
    "豆芽",
    "鸡胸肉",
    "猪肉",
    "牛肉",
    "排骨",
    "五花肉",
    "鸡翅",
    "鸡腿",
    "肉末",
    "虾",
    "鱼",
    "豆腐",
    "鸡蛋",
    "牛奶",
    "米饭",
    "面条",
    "馒头",
    "饺子皮",
    "面粉",
]

_CACHE_COUNT = 3
_CACHE_TTL = timedelta(days=7)
_MAX_TTL_JITTER_SECONDS = 24 * 60 * 60


def iter_preset_combos() -> Iterator[list[str]]:
    """Yield all preset singles, followed by every unique pair."""
    yield from ([ingredient] for ingredient in PRESET_INGREDIENTS)
    yield from (
        [first, second]
        for first, second in itertools.combinations(PRESET_INGREDIENTS, 2)
    )


def _is_fresh(db: Session, cache_key: str, now: datetime) -> bool:
    return (
        db.query(RecommendCache)
        .filter(
            RecommendCache.cache_key == cache_key,
            RecommendCache.expires_at > now,
        )
        .first()
        is not None
    )


def _upsert_cache(
    db: Session,
    cache_key: str,
    response: IngredientRecommendResponse,
    now: datetime,
) -> None:
    expires_at = now + _CACHE_TTL + timedelta(
        seconds=random.uniform(0, _MAX_TTL_JITTER_SECONDS)
    )
    cached = (
        db.query(RecommendCache)
        .filter(RecommendCache.cache_key == cache_key)
        .first()
    )
    payload = response.model_dump_json()
    if cached:
        cached.payload = payload
        cached.model = OPENROUTER_MODEL
        cached.created_at = now
        cached.expires_at = expires_at
    else:
        db.add(
            RecommendCache(
                cache_key=cache_key,
                payload=payload,
                model=OPENROUTER_MODEL,
                created_at=now,
                expires_at=expires_at,
            )
        )
    db.commit()


def run_pregeneration(
    db: Session,
    budget: int = PREGEN_DAILY_BUDGET,
) -> int:
    """Generate up to ``budget`` missing preset combinations.

    Budget limits attempted LLM calls, not only successful calls, so a broken
    provider cannot trigger requests for the entire 465-combination matrix.
    """
    attempts = 0
    generated = 0

    for ingredients in iter_preset_combos():
        if attempts >= max(0, budget):
            break

        now = datetime.now(timezone.utc)
        cache_key = make_cache_key(ingredients, _CACHE_COUNT)
        if _is_fresh(db, cache_key, now):
            continue

        attempts += 1
        try:
            dishes = generate_dishes_via_llm(
                ingredients,
                _CACHE_COUNT,
                preferences=None,
                allow_extra=False,
                exclude_dishes=None,
            )
            if not dishes:
                logger.warning("预生成返回空结果: %s", ingredients)
                continue

            response = IngredientRecommendResponse(
                dishes=dishes,
                input_ingredients=ingredients,
            )
            _upsert_cache(db, cache_key, response, now)
            generated += 1
        except Exception:
            db.rollback()
            logger.exception("预生成失败，继续下一组合: %s", ingredients)

    logger.info(
        "预生成完成: attempts=%d generated=%d budget=%d",
        attempts,
        generated,
        budget,
    )
    return generated
