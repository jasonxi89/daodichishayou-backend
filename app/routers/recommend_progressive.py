"""Progressive recommendation endpoints: quick names first, steps on demand."""

import asyncio
import json
import logging
from datetime import datetime, timezone

import openai
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI, OpenAI
from sqlalchemy.orm import Session

from app.config import (
    AI_CORE_RULES,
    LLM_TIMEOUT_SECONDS,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_FAST_MODEL,
    OPENROUTER_MODEL,
)
from app.database import get_db
from app.models import RecommendCache
from app.schemas import (
    DishStepsRequest,
    IngredientRecommendRequest,
    IngredientRecommendResponse,
    QuickRecommendResponse,
    QuickRecommendedDish,
    RecommendedDish,
)
from app.services.recommend_fallback import get_fallback_recommendation
from app.services.recommend_cache import (
    get_cached_recommendation,
    make_cache_key,
    normalize_ingredients,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/recommend", tags=["recommend"])

QUICK_SYSTEM_PROMPT = f"""{AI_CORE_RULES}

你是一位专业中餐厨师。根据用户食材快速给出菜名和一句话简介。
本轮不要输出配料用量或做法步骤，只返回轻量结果。

返回格式（纯JSON，无markdown）：
{{"dishes":[{{"name":"菜名","summary":"20字内简介","difficulty":"简单/中等/较难","cook_time":"约X分钟"}}]}}"""

STEPS_SYSTEM_PROMPT = f"""{AI_CORE_RULES}

你是一位专业中餐厨师。用户已经选定一道菜，请只补全这道菜的可靠配料和详细步骤。

返回格式（纯JSON，无markdown）：
{{"name":"菜名","summary":"20字内简介","ingredients":["食材 用量"],"steps":["步骤1"],"difficulty":"简单/中等/较难","cook_time":"约X分钟"}}"""


def _strip_code_fence(raw_text: str) -> str:
    if not raw_text.startswith("```"):
        return raw_text
    lines = raw_text.split("\n")[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def _client() -> OpenAI:
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
        timeout=LLM_TIMEOUT_SECONDS,
    )


def _async_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
        timeout=LLM_TIMEOUT_SECONDS,
    )


def generate_quick_dishes_via_llm(
    ingredients: list[str],
    count: int,
    preferences: str | None,
    allow_extra: bool,
    exclude_dishes: list[str] | None,
    *,
    model: str | None = None,
) -> list[QuickRecommendedDish]:
    """Synchronously generate lightweight dish cards."""
    prompt = [f"食材：{', '.join(ingredients)}", f"推荐{count}道菜。"]
    if preferences:
        prompt.append(f"偏好：{preferences}")
    if allow_extra:
        prompt.append("可额外购买1-2种主料。")
    if exclude_dishes:
        prompt.append(f"不要重复：{'、'.join(exclude_dishes)}")
    message = _client().chat.completions.create(
        model=model or OPENROUTER_MODEL,
        max_tokens=800,
        messages=[
            {"role": "system", "content": QUICK_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(prompt)},
        ],
    )
    raw_text = _strip_code_fence(
        (message.choices[0].message.content or "").strip()
    )
    data = json.loads(raw_text)
    if not isinstance(data, dict) or not isinstance(data.get("dishes"), list):
        raise ValueError("LLM response must contain a dishes list")
    dishes = [
        QuickRecommendedDish.model_validate(item)
        for item in data["dishes"][:count]
    ]
    if not dishes:
        raise ValueError("LLM returned no dishes")
    return dishes


def _steps_messages(
    dish_name: str,
    ingredients: list[str],
    preferences: str | None = None,
    allow_extra: bool = False,
) -> list[dict[str, str]]:
    context = [
        f"菜名：{dish_name}",
        f"用户现有食材：{', '.join(ingredients)}",
    ]
    if preferences:
        context.append(f"偏好与限制：{preferences}")
    context.append(
        "允许额外购买1-2种主料" if allow_extra else "不得增加额外主料"
    )
    return [
        {"role": "system", "content": STEPS_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(context)},
    ]


def generate_steps_via_llm(
    dish_name: str,
    ingredients: list[str],
    preferences: str | None = None,
    allow_extra: bool = False,
) -> RecommendedDish:
    """Synchronously generate the complete recipe for one selected dish."""
    message = _client().chat.completions.create(
        model=OPENROUTER_MODEL,
        max_tokens=4096,
        messages=_steps_messages(
            dish_name,
            ingredients,
            preferences,
            allow_extra,
        ),
    )
    raw_text = _strip_code_fence(
        (message.choices[0].message.content or "").strip()
    )
    dish = RecommendedDish.model_validate(json.loads(raw_text))
    if not dish.ingredients or not dish.steps:
        raise ValueError("LLM returned an incomplete dish")
    return dish


def _stream_frame(frame: dict) -> str:
    return json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n"


def _stream_complete_dish(dish: RecommendedDish):
    """Yield a cached/local dish as one complete NDJSON frame."""
    yield _stream_frame(
        {"type": "complete", "dish": dish.model_dump(mode="json")}
    )


async def _stream_steps_from_llm(
    dish_name: str,
    ingredients: list[str],
    preferences: str | None = None,
    allow_extra: bool = False,
):
    """Asynchronously stream NDJSON and own provider cancellation cleanup."""
    raw_parts: list[str] = []
    client = _async_client()
    chunks = None
    try:
        chunks = await client.chat.completions.create(
            model=OPENROUTER_MODEL,
            max_tokens=4096,
            messages=_steps_messages(
                dish_name,
                ingredients,
                preferences,
                allow_extra,
            ),
            stream=True,
        )
        async for chunk in chunks:
            content = chunk.choices[0].delta.content
            if not content:
                continue
            raw_parts.append(content)
            yield _stream_frame({"type": "delta", "text": content})

        raw_text = _strip_code_fence("".join(raw_parts).strip())
        dish = RecommendedDish.model_validate(json.loads(raw_text))
        yield _stream_frame(
            {"type": "complete", "dish": dish.model_dump(mode="json")}
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Dish steps stream failed")
        yield _stream_frame(
            {"type": "error", "code": "provider_interrupted"}
        )
    finally:
        try:
            if chunks is not None:
                await chunks.close()
        finally:
            await client.close()


def _quick_from_full(
    response: IngredientRecommendResponse,
) -> list[QuickRecommendedDish]:
    return [
        QuickRecommendedDish(
            name=dish.name,
            summary=dish.summary,
            difficulty=dish.difficulty,
            cook_time=dish.cook_time,
        )
        for dish in response.dishes
    ]


def _find_cached_dish(
    db: Session,
    dish_name: str,
    ingredients: list[str],
    now: datetime,
) -> RecommendedDish | None:
    rows = (
        db.query(RecommendCache)
        .filter(RecommendCache.expires_at > now)
        .order_by(RecommendCache.created_at.desc())
        .all()
    )
    for row in rows:
        try:
            response = IngredientRecommendResponse.model_validate_json(
                row.payload
            )
        except ValueError:
            continue
        if normalize_ingredients(response.input_ingredients) != normalize_ingredients(
            ingredients
        ):
            continue
        for dish in response.dishes:
            if dish.name == dish_name:
                return dish
    return None


@router.post("/quick", response_model=QuickRecommendResponse)
async def recommend_quick(
    req: IngredientRecommendRequest,
    db: Session = Depends(get_db),
):
    if not req.ingredients:
        raise HTTPException(
            status_code=400,
            detail="At least one ingredient is required",
        )

    count = max(1, min(req.count, 5))
    cache_eligible = (
        not req.preferences
        and not req.allow_extra
        and not req.exclude_dishes
    )
    if cache_eligible:
        cached = get_cached_recommendation(
            db,
            make_cache_key(req.ingredients, count),
            datetime.now(timezone.utc),
        )
        if cached:
            return QuickRecommendResponse(
                dishes=_quick_from_full(cached),
                input_ingredients=req.ingredients,
            )

    fallback_allowed = not req.preferences and not req.allow_extra
    if not OPENROUTER_API_KEY:
        fallback = get_fallback_recommendation(
            db,
            req.ingredients,
            count,
            req.exclude_dishes or None,
        ) if fallback_allowed else None
        if fallback:
            return QuickRecommendResponse(
                dishes=_quick_from_full(fallback),
                input_ingredients=req.ingredients,
            )
        raise HTTPException(
            status_code=500,
            detail="No recommendation source is currently available",
        )
    db.rollback()
    try:
        dishes = await asyncio.to_thread(
            generate_quick_dishes_via_llm,
            req.ingredients,
            count,
            req.preferences,
            req.allow_extra,
            req.exclude_dishes or None,
        )
        if not dishes:
            raise ValueError("LLM returned no dishes")
    except (openai.OpenAIError, json.JSONDecodeError, ValueError) as error:
        logger.warning("Primary quick recommendation failed: %s", error)
        dishes = []
        if OPENROUTER_FAST_MODEL:
            try:
                dishes = await asyncio.to_thread(
                    generate_quick_dishes_via_llm,
                    req.ingredients,
                    count,
                    req.preferences,
                    req.allow_extra,
                    req.exclude_dishes or None,
                    model=OPENROUTER_FAST_MODEL,
                )
                if not dishes:
                    raise ValueError("Fast model returned no dishes")
            except (
                openai.OpenAIError,
                json.JSONDecodeError,
                ValueError,
            ) as fast_error:
                logger.warning("Fast quick fallback failed: %s", fast_error)

        if not dishes:
            fallback = get_fallback_recommendation(
                db,
                req.ingredients,
                count,
                req.exclude_dishes or None,
            ) if fallback_allowed else None
            if fallback:
                return QuickRecommendResponse(
                    dishes=_quick_from_full(fallback),
                    input_ingredients=req.ingredients,
                )
            raise HTTPException(
                status_code=502,
                detail="AI service temporarily unavailable",
            ) from error

    # Deliberately do not store partial quick payloads in RecommendCache.
    return QuickRecommendResponse(
        dishes=dishes,
        input_ingredients=req.ingredients,
    )


@router.post("/steps", response_model=RecommendedDish)
async def recommend_steps(
    req: DishStepsRequest,
    stream: bool = Query(False),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    context_is_generic = not req.preferences and not req.allow_extra
    cached = _find_cached_dish(
        db,
        req.dish_name,
        req.ingredients,
        now,
    ) if context_is_generic else None
    if cached:
        if stream:
            return StreamingResponse(
                _stream_complete_dish(cached),
                media_type="application/x-ndjson",
            )
        return cached

    if not OPENROUTER_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="OPENROUTER_API_KEY not configured",
        )
    db.rollback()
    if stream:
        return StreamingResponse(
            _stream_steps_from_llm(
                req.dish_name,
                req.ingredients,
                req.preferences,
                req.allow_extra,
            ),
            media_type="application/x-ndjson",
        )

    try:
        return await asyncio.to_thread(
            generate_steps_via_llm,
            req.dish_name,
            req.ingredients,
            req.preferences,
            req.allow_extra,
        )
    except (openai.OpenAIError, json.JSONDecodeError, ValueError) as exc:
        logger.error("Dish steps generation failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="AI service temporarily unavailable",
        ) from exc
