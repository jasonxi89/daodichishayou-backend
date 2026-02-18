import json
import logging

import anthropic
from fastapi import APIRouter, HTTPException

from app.config import CLAUDE_API_KEY
from app.schemas import (
    IngredientRecommendRequest,
    IngredientRecommendResponse,
    RecommendedDish,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["recommend"])

SYSTEM_PROMPT = """你是一位专业中餐厨师和美食顾问。用户会告诉你手头有哪些食材，你需要推荐适合的菜品及详细做法。

要求：
1. 只使用用户提供的食材为主料，可以假设家中有基本调味料（盐、酱油、醋、糖、料酒、生抽、老抽、蚝油、食用油、葱姜蒜、胡椒粉、淀粉等）
2. 推荐的菜品要实用、家常、易操作
3. 步骤要详细清晰，适合厨房新手
4. 必须严格按照JSON格式返回，不要添加任何其他文字

返回格式（纯JSON，无markdown）：
{
  "dishes": [
    {
      "name": "菜品名称",
      "summary": "一句话简介（20字以内）",
      "ingredients": ["食材1 用量", "食材2 用量", "盐 适量"],
      "steps": ["步骤1描述", "步骤2描述", "步骤3描述"],
      "difficulty": "简单/中等/较难",
      "cook_time": "约X分钟"
    }
  ]
}"""


def build_user_prompt(ingredients: list[str], count: int, preferences: str | None) -> str:
    parts = [f"我手头有这些食材：{', '.join(ingredients)}"]
    parts.append(f"请推荐{count}道菜。")
    if preferences:
        parts.append(f"偏好：{preferences}")
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
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": build_user_prompt(req.ingredients, count, req.preferences),
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
            )
        )

    return IngredientRecommendResponse(
        dishes=dishes,
        input_ingredients=req.ingredients,
    )
