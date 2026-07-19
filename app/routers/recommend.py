import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import openai
from openai import OpenAI
from fastapi import APIRouter, Depends, HTTPException
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
from app.models import FoodsCategoryCache
from app.schemas import (
    BulkGenerateFoodsRequest,
    BulkGenerateFoodsResponse,
    GenerateFoodsRequest,
    GenerateFoodsResponse,
    IngredientRecommendRequest,
    IngredientRecommendResponse,
    RecommendedDish,
)
from app.services.recipe_search import search_local_recipes as _search_local_recipes
from app.services.recommend_fallback import get_fallback_recommendation
from app.services.recommend_cache import (
    get_cached_recommendation as _get_cached_recommendation,
    make_cache_key,
    store_recommendation as _store_recommendation,
)

logger = logging.getLogger(__name__)

CATEGORY_CACHE_TTL = timedelta(days=1)

router = APIRouter(prefix="/api", tags=["recommend"])

SYSTEM_PROMPT = f"""{AI_CORE_RULES}

你是一位专业中餐厨师和美食顾问。用户会告诉你手头有哪些食材，你需要推荐适合的菜品及详细做法。

要求：
1. 只使用用户提供的食材为主料，可以假设家中有基本调味料（盐、酱油、醋、糖、料酒、生抽、老抽、蚝油、食用油、葱姜蒜、胡椒粉、淀粉等）
2. 推荐的菜品要实用、家常、易操作
3. 步骤要详细清晰，适合厨房新手
4. 推荐的菜品应该是经过大量家庭厨房验证的经典做法，优先推荐在各大菜谱平台上有高评分（8.5分以上）、大量用户实际做过的成熟菜谱做法，确保步骤可靠、配比准确

返回格式（纯JSON，无markdown）：
{{
  "dishes": [
    {{
      "name": "菜品名称",
      "summary": "一句话简介（20字以内）",
      "ingredients": ["食材1 用量", "食材2 用量", "盐 适量"],
      "steps": ["步骤1描述", "步骤2描述", "步骤3描述"],
      "difficulty": "简单/中等/较难",
      "cook_time": "约X分钟"
    }}
  ]
}}"""

SYSTEM_PROMPT_EXTRA = f"""{AI_CORE_RULES}

你是一位专业中餐厨师和美食顾问。用户会告诉你手头有哪些食材，你可以在此基础上额外使用1-2种需要购买的食材（不含调味料），推荐更丰富的菜品。

要求：
1. 以用户提供的食材为主料，可额外使用1-2种需要购买的食材（不含调味料）
2. 可以假设家中有基本调味料（盐、酱油、醋、糖、料酒、生抽、老抽、蚝油、食用油、葱姜蒜、胡椒粉、淀粉等）
3. 推荐的菜品要实用、家常、易操作
4. 步骤要详细清晰，适合厨房新手
5. 如果菜品需要额外购买的食材，必须在extra_ingredients字段中列出（只列食材名，不含用量，不含调味料）
6. 推荐的菜品应该是经过大量家庭厨房验证的经典做法，优先推荐在各大菜谱平台上有高评分（8.5分以上）、大量用户实际做过的成熟菜谱做法，确保步骤可靠、配比准确

返回格式（纯JSON，无markdown）：
{{
  "dishes": [
    {{
      "name": "菜品名称",
      "summary": "一句话简介（20字以内）",
      "ingredients": ["食材1 用量", "食材2 用量", "盐 适量"],
      "steps": ["步骤1描述", "步骤2描述", "步骤3描述"],
      "difficulty": "简单/中等/较难",
      "cook_time": "约X分钟",
      "extra_ingredients": ["需要额外购买的食材1"]
    }}
  ]
}}"""


def build_user_prompt(
    ingredients: list[str],
    count: int,
    preferences: str | None,
    allow_extra: bool = False,
    exclude_dishes: list[str] | None = None,
) -> str:
    parts = [f"我手头有这些食材：{', '.join(ingredients)}"]
    parts.append(f"请推荐{count}道菜。")
    if preferences:
        parts.append(f"偏好：{preferences}")
    if allow_extra:
        parts.append("可以额外使用1-2种需要购买的食材，请在extra_ingredients中标明。")
    if exclude_dishes:
        parts.append(f"请不要推荐以下已推荐过的菜：{'、'.join(exclude_dishes)}")
    return "\n".join(parts)


def _strip_code_fence(raw_text: str) -> str:
    """Remove an optional Markdown code fence from an LLM JSON response."""
    if not raw_text.startswith("```"):
        return raw_text
    lines = raw_text.split("\n")[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def generate_dishes_via_llm(
    ingredients: list[str],
    count: int,
    preferences: str | None,
    allow_extra: bool = False,
    exclude_dishes: list[str] | None = None,
    *,
    model: str | None = None,
) -> list[RecommendedDish]:
    """Synchronously generate complete dishes through the configured LLM."""
    client = OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
        timeout=LLM_TIMEOUT_SECONDS,
    )
    system_prompt = SYSTEM_PROMPT_EXTRA if allow_extra else SYSTEM_PROMPT
    message = client.chat.completions.create(
        model=model or OPENROUTER_MODEL,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": build_user_prompt(
                    ingredients,
                    count,
                    preferences,
                    allow_extra,
                    exclude_dishes,
                ),
            },
        ],
    )
    raw_text = _strip_code_fence(
        (message.choices[0].message.content or "").strip()
    )
    data = json.loads(raw_text)
    return [
        RecommendedDish(
            name=item.get("name", ""),
            summary=item.get("summary", ""),
            ingredients=item.get("ingredients", []),
            steps=item.get("steps", []),
            difficulty=item.get("difficulty"),
            cook_time=item.get("cook_time"),
            extra_ingredients=item.get("extra_ingredients"),
        )
        for item in data.get("dishes", [])
    ]


@router.post("/recommend", response_model=IngredientRecommendResponse)
async def recommend_by_ingredients(
    req: IngredientRecommendRequest,
    db: Session = Depends(get_db),
):
    if not req.ingredients:
        raise HTTPException(status_code=400, detail="At least one ingredient is required")

    count = max(1, min(req.count, 5))
    now = datetime.now(timezone.utc)

    # Customized requests must not reuse a generic ingredient-only result.
    is_cache_eligible = (
        not req.allow_extra
        and not req.preferences
        and not req.exclude_dishes
    )
    cache_key = make_cache_key(req.ingredients, count)
    if is_cache_eligible:
        cached_response = _get_cached_recommendation(db, cache_key, now)
        if cached_response:
            return cached_response.model_copy(
                update={"input_ingredients": req.ingredients}
            )

    # Local-first: skip when allow_extra or preferences are set.
    is_local_eligible = not req.allow_extra and not req.preferences
    local_dishes: list[RecommendedDish] = []

    if is_local_eligible:
        local_dishes = _search_local_recipes(
            db, req.ingredients, count, req.exclude_dishes or None,
        )
        # 本地有结果就直接返回，宁缺毋滥：LLM 补齐差额实测 44-110s，
        # 远超前端可接受的等待时间，得不偿失
        if local_dishes:
            return IngredientRecommendResponse(
                dishes=local_dishes[:count],
                input_ingredients=req.ingredients,
            )

    if not OPENROUTER_API_KEY:
        fallback = get_fallback_recommendation(
            db,
            req.ingredients,
            count,
            req.exclude_dishes or None,
        )
        if fallback:
            return fallback
        raise HTTPException(
            status_code=500,
            detail="No recommendation source is currently available",
        )

    # Need AI for all or remaining dishes
    ai_count = count - len(local_dishes)
    ai_exclude = list(req.exclude_dishes) if req.exclude_dishes else []
    ai_exclude.extend(d.name for d in local_dishes)

    used_model = OPENROUTER_MODEL
    try:
        ai_dishes = await asyncio.to_thread(
            generate_dishes_via_llm,
            req.ingredients,
            ai_count,
            req.preferences,
            req.allow_extra,
            ai_exclude or None,
        )
    except (openai.OpenAIError, json.JSONDecodeError, ValueError) as error:
        logger.warning("Primary recommendation failed: %s", error)
        if OPENROUTER_FAST_MODEL:
            try:
                ai_dishes = await asyncio.to_thread(
                    generate_dishes_via_llm,
                    req.ingredients,
                    ai_count,
                    req.preferences,
                    req.allow_extra,
                    ai_exclude or None,
                    model=OPENROUTER_FAST_MODEL,
                )
                used_model = OPENROUTER_FAST_MODEL
            except (
                openai.OpenAIError,
                json.JSONDecodeError,
                ValueError,
            ) as fast_error:
                logger.warning("Fast recommendation fallback failed: %s", fast_error)
                ai_dishes = []
        else:
            ai_dishes = []

        if not ai_dishes:
            fallback = get_fallback_recommendation(
                db,
                req.ingredients,
                count,
                req.exclude_dishes or None,
            )
            if fallback:
                return fallback
            raise HTTPException(
                status_code=502,
                detail="AI service temporarily unavailable",
            ) from error

    response = IngredientRecommendResponse(
        dishes=local_dishes + ai_dishes,
        input_ingredients=req.ingredients,
    )
    if is_cache_eligible:
        _store_recommendation(
            db,
            cache_key,
            response,
            used_model,
            now,
        )
    return response


CATEGORY_FOODS_PROMPT = f"""{AI_CORE_RULES}

你是一位美食百科专家。用户会给你一个食物分类名称，你需要列出属于该分类的真实食物名称。

要求：
1. 只列出真实存在的、广为人知的食物/菜品名称
2. 名称要简洁（一般2-6个字），不需要描述
3. 尽量覆盖该分类下不同风格和地域的代表性食物

返回格式（纯JSON，无markdown）：
{{{{"foods": ["食物1", "食物2", "食物3"]}}}}"""


def generate_foods_by_category_via_llm(
    category: str,
    count: int,
) -> list[str]:
    """Synchronously generate food names for one category."""
    client = OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
        timeout=LLM_TIMEOUT_SECONDS,
    )
    message = client.chat.completions.create(
        model=OPENROUTER_MODEL,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": CATEGORY_FOODS_PROMPT},
            {
                "role": "user",
                "content": f"请列出{count}个属于「{category}」分类的食物名称。",
            },
        ],
    )
    raw_text = _strip_code_fence(
        (message.choices[0].message.content or "").strip()
    )
    data = json.loads(raw_text)
    foods = data.get("foods", [])
    return foods if isinstance(foods, list) else []


@router.post("/foods-by-category", response_model=GenerateFoodsResponse)
async def foods_by_category(req: GenerateFoodsRequest, db: Session = Depends(get_db)):
    # Check non-LLM cache before requiring an API key.
    cached = (
        db.query(FoodsCategoryCache)
        .filter(
            FoodsCategoryCache.category == req.category,
            FoodsCategoryCache.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if cached:
        return GenerateFoodsResponse(
            foods=json.loads(cached.foods),
            category=req.category,
        )

    if not OPENROUTER_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="No category source is currently available",
        )

    count = max(1, min(req.count, 50))

    try:
        foods = await asyncio.to_thread(
            generate_foods_by_category_via_llm,
            req.category,
            count,
        )
    except openai.OpenAIError as e:
        logger.error("LLM API error: %s", e)
        raise HTTPException(
            status_code=502,
            detail="AI service temporarily unavailable",
        ) from e
    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM category response")
        raise HTTPException(
            status_code=502,
            detail="AI response format error",
        ) from e

    # Save to cache (upsert by category)
    existing = (
        db.query(FoodsCategoryCache)
        .filter(FoodsCategoryCache.category == req.category)
        .first()
    )
    if existing:
        existing.foods = json.dumps(foods, ensure_ascii=False)
        existing.expires_at = datetime.now(timezone.utc) + CATEGORY_CACHE_TTL
        existing.created_at = datetime.now(timezone.utc)
    else:
        db.add(
            FoodsCategoryCache(
                category=req.category,
                foods=json.dumps(foods, ensure_ascii=False),
                expires_at=datetime.now(timezone.utc) + CATEGORY_CACHE_TTL,
            )
        )
    db.commit()

    return GenerateFoodsResponse(
        foods=foods,
        category=req.category,
    )


BULK_CATEGORY_FOODS_PROMPT = f"""{AI_CORE_RULES}

你是一位美食百科专家。用户会给你多个食物分类名称，你需要为每个分类列出属于该分类的真实食物名称。

要求：
1. 只列出真实存在的、广为人知的食物/菜品名称
2. 名称要简洁（一般2-6个字），不需要描述
3. 尽量覆盖该分类下不同风格和地域的代表性食物

返回格式（纯JSON，无markdown）：
{{{{"分类名1": ["食物1", "食物2"], "分类名2": ["食物3", "食物4"]}}}}"""


def generate_bulk_foods_by_category_via_llm(
    categories: list[str],
    count: int,
) -> dict[str, list[str]]:
    """Synchronously generate food names for multiple categories."""
    client = OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
        timeout=LLM_TIMEOUT_SECONDS,
    )
    categories_text = "、".join(f"「{category}」" for category in categories)
    message = client.chat.completions.create(
        model=OPENROUTER_MODEL,
        max_tokens=8192,
        messages=[
            {"role": "system", "content": BULK_CATEGORY_FOODS_PROMPT},
            {
                "role": "user",
                "content": f"请为以下分类各列出{count}个食物名称：{categories_text}",
            },
        ],
    )
    raw_text = _strip_code_fence(
        (message.choices[0].message.content or "").strip()
    )
    data = json.loads(raw_text)
    return data if isinstance(data, dict) else {}


@router.post("/bulk-foods-by-category", response_model=BulkGenerateFoodsResponse)
async def bulk_foods_by_category(req: BulkGenerateFoodsRequest, db: Session = Depends(get_db)):
    if not req.categories:
        return BulkGenerateFoodsResponse(results={})

    count = max(1, min(req.count, 50))
    now = datetime.now(timezone.utc)

    # Check cache for all requested categories
    cached_rows = (
        db.query(FoodsCategoryCache)
        .filter(
            FoodsCategoryCache.category.in_(req.categories),
            FoodsCategoryCache.expires_at > now,
        )
        .all()
    )

    cached_results: dict[str, list[str]] = {}
    for row in cached_rows:
        cached_results[row.category] = json.loads(row.foods)

    uncached_categories = [c for c in req.categories if c not in cached_results]

    # All cached — return immediately without calling Claude
    if not uncached_categories:
        return BulkGenerateFoodsResponse(results=cached_results)

    if not OPENROUTER_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="No category source is currently available",
        )

    try:
        data = await asyncio.to_thread(
            generate_bulk_foods_by_category_via_llm,
            uncached_categories,
            count,
        )
    except openai.OpenAIError as e:
        logger.error("LLM API error: %s", e)
        raise HTTPException(
            status_code=502,
            detail="AI service temporarily unavailable",
        ) from e
    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM bulk-category response")
        raise HTTPException(
            status_code=502,
            detail="AI response format error",
        ) from e

    # Upsert each category into cache
    for category in uncached_categories:
        foods = data.get(category, [])
        if not isinstance(foods, list):
            foods = []
        cached_results[category] = foods

        existing = (
            db.query(FoodsCategoryCache)
            .filter(FoodsCategoryCache.category == category)
            .first()
        )
        if existing:
            existing.foods = json.dumps(foods, ensure_ascii=False)
            existing.expires_at = now + CATEGORY_CACHE_TTL
            existing.created_at = now
        else:
            db.add(
                FoodsCategoryCache(
                    category=category,
                    foods=json.dumps(foods, ensure_ascii=False),
                    expires_at=now + CATEGORY_CACHE_TTL,
                )
            )

    db.commit()

    return BulkGenerateFoodsResponse(results=cached_results)
