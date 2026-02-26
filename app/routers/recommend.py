import json
import logging

import anthropic
from fastapi import APIRouter, HTTPException

from app.config import AI_CORE_RULES, CLAUDE_API_KEY
from app.schemas import (
    GenerateFoodsRequest,
    GenerateFoodsResponse,
    IngredientRecommendRequest,
    IngredientRecommendResponse,
    RecommendedDish,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["recommend"])

SYSTEM_PROMPT = f"""{AI_CORE_RULES}

你是一位专业中餐厨师和美食顾问。用户会告诉你手头有哪些食材，你需要推荐适合的菜品及详细做法。

要求：
1. 只使用用户提供的食材为主料，可以假设家中有基本调味料（盐、酱油、醋、糖、料酒、生抽、老抽、蚝油、食用油、葱姜蒜、胡椒粉、淀粉等）
2. 推荐的菜品要实用、家常、易操作
3. 步骤要详细清晰，适合厨房新手

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


@router.post("/recommend", response_model=IngredientRecommendResponse)
async def recommend_by_ingredients(req: IngredientRecommendRequest):
    if not CLAUDE_API_KEY:
        raise HTTPException(status_code=500, detail="CLAUDE_API_KEY not configured")

    if not req.ingredients:
        raise HTTPException(status_code=400, detail="At least one ingredient is required")

    count = max(1, min(req.count, 5))

    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    try:
        system_prompt = SYSTEM_PROMPT_EXTRA if req.allow_extra else SYSTEM_PROMPT
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": build_user_prompt(
                        req.ingredients, count, req.preferences,
                        req.allow_extra, req.exclude_dishes,
                    ),
                }
            ],
        )
    except anthropic.APIError as e:
        logger.error("Claude API error: %s", e)
        raise HTTPException(status_code=502, detail="AI service temporarily unavailable")

    raw_text = message.content[0].text.strip()

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        lines = lines[1:]  # remove opening ```json
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw_text = "\n".join(lines)

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.error("Failed to parse Claude response: %s", raw_text[:500])
        raise HTTPException(status_code=502, detail="AI response format error")

    dishes = []
    for item in data.get("dishes", []):
        dishes.append(
            RecommendedDish(
                name=item.get("name", ""),
                summary=item.get("summary", ""),
                ingredients=item.get("ingredients", []),
                steps=item.get("steps", []),
                difficulty=item.get("difficulty"),
                cook_time=item.get("cook_time"),
                extra_ingredients=item.get("extra_ingredients"),
            )
        )

    return IngredientRecommendResponse(
        dishes=dishes,
        input_ingredients=req.ingredients,
    )


CATEGORY_FOODS_PROMPT = f"""{AI_CORE_RULES}

你是一位美食百科专家。用户会给你一个食物分类名称，你需要列出属于该分类的真实食物名称。

要求：
1. 只列出真实存在的、广为人知的食物/菜品名称
2. 名称要简洁（一般2-6个字），不需要描述
3. 尽量覆盖该分类下不同风格和地域的代表性食物

返回格式（纯JSON，无markdown）：
{{{{"foods": ["食物1", "食物2", "食物3"]}}}}"""


@router.post("/foods-by-category", response_model=GenerateFoodsResponse)
async def foods_by_category(req: GenerateFoodsRequest):
    if not CLAUDE_API_KEY:
        raise HTTPException(status_code=500, detail="CLAUDE_API_KEY not configured")

    count = max(1, min(req.count, 50))

    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=CATEGORY_FOODS_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"请列出{count}个属于「{req.category}」分类的食物名称。",
                }
            ],
        )
    except anthropic.APIError as e:
        logger.error("Claude API error: %s", e)
        raise HTTPException(status_code=502, detail="AI service temporarily unavailable")

    raw_text = message.content[0].text.strip()

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        lines = lines[1:]  # remove opening ```json
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw_text = "\n".join(lines)

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.error("Failed to parse Claude response: %s", raw_text[:500])
        raise HTTPException(status_code=502, detail="AI response format error")

    foods = data.get("foods", [])

    return GenerateFoodsResponse(
        foods=foods,
        category=req.category,
    )
