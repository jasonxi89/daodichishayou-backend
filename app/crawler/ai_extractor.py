"""AI 智能食物提取 — 用 Claude 从未匹配标题中发现新食物。"""

import json
import logging
from anthropic import Anthropic
from app.config import AI_CORE_RULES, CLAUDE_API_KEY, AI_EXTRACT_ENABLED
from app.crawler.base import FoodTrendItem
from app.crawler.food_keywords import FOOD_NAMES, get_category

logger = logging.getLogger(__name__)

# 15 categories matching food_keywords.py
VALID_CATEGORIES = {
    "正餐", "小吃", "面食", "烧烤", "火锅", "西餐",
    "日料", "韩餐", "东南亚", "甜品", "饮品", "早餐",
    "轻食", "点心", "零食",
}

_SYSTEM_PROMPT = f"""{AI_CORE_RULES}

你是一个美食识别专家。给你一批热搜标题，请从中提取出具体的食物/菜品/饮品名称。

规则：
1. 只提取具体的食物名，如"酱香拿铁"、"脏脏包"，不要提取泛称如"美食"、"小吃"
2. 食物名长度 2-10 个字
3. 每个食物必须归入以下分类之一：正餐、小吃、面食、烧烤、火锅、西餐、日料、韩餐、东南亚、甜品、饮品、早餐、轻食、点心、零食
4. 如果标题中没有具体食物，返回空数组
5. 只返回真实存在的食物，不要编造"""

_MAX_TITLES_PER_BATCH = 50
_DEFAULT_HEAT_SCORE = 50


def extract_foods_from_titles(titles: list[str]) -> list[FoodTrendItem]:
    """从未匹配的热搜标题中用 AI 提取食物名。

    Args:
        titles: 未匹配到词典食物的热搜标题列表

    Returns:
        提取到的食物趋势列表，source 标记为 ai_extract
    """
    if not AI_EXTRACT_ENABLED:
        logger.info("AI 提取已禁用")
        return []

    if not CLAUDE_API_KEY:
        logger.warning("未配置 CLAUDE_API_KEY，跳过 AI 提取")
        return []

    if not titles:
        return []

    # 去重
    unique_titles = list(dict.fromkeys(titles))

    all_items: list[FoodTrendItem] = []
    # 分批处理
    for i in range(0, len(unique_titles), _MAX_TITLES_PER_BATCH):
        batch = unique_titles[i:i + _MAX_TITLES_PER_BATCH]
        try:
            items = _extract_batch(batch)
            all_items.extend(items)
        except Exception:
            logger.error("AI 提取批次 %d 失败", i // _MAX_TITLES_PER_BATCH, exc_info=True)

    # 去重（同名食物只保留一个）
    seen: dict[str, FoodTrendItem] = {}
    for item in all_items:
        if item.food_name not in seen:
            seen[item.food_name] = item

    result = list(seen.values())
    logger.info("AI 提取: 发现 %d 种新食物", len(result))
    return result


def _extract_batch(titles: list[str]) -> list[FoodTrendItem]:
    """对一批标题调用 Claude Haiku 提取食物。"""
    client = Anthropic(api_key=CLAUDE_API_KEY)

    titles_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    user_prompt = f"""请从以下热搜标题中提取具体的食物/菜品/饮品名称。

热搜标题：
{titles_text}

请严格按以下 JSON 格式返回：
{{"results": [{{"title": "原标题", "foods": [{{"name": "食物名", "category": "分类"}}]}}]}}

如果某个标题没有食物，其 foods 为空数组。"""

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return _parse_response(resp.content[0].text)


def _parse_response(text: str) -> list[FoodTrendItem]:
    """解析 Claude 返回的 JSON，过滤并转为 FoodTrendItem。"""
    # 提取 JSON（Claude 可能返回 markdown code block）
    json_text = text.strip()
    if json_text.startswith("```"):
        # Remove markdown code fences
        lines = json_text.split("\n")
        json_lines = []
        in_block = False
        for line in lines:
            if line.strip().startswith("```"):
                in_block = not in_block
                continue
            if in_block:
                json_lines.append(line)
        json_text = "\n".join(json_lines)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        logger.warning("AI 返回的 JSON 解析失败: %s", text[:200])
        return []

    items: list[FoodTrendItem] = []
    for result in data.get("results", []):
        for food in result.get("foods", []):
            name = food.get("name", "").strip()
            category = food.get("category", "").strip()

            # 过滤条件
            if not name or len(name) < 2 or len(name) > 10:
                continue
            if name in FOOD_NAMES:
                continue  # 已在词典中，跳过
            if category not in VALID_CATEGORIES:
                category = "小吃"  # 默认分类

            items.append(
                FoodTrendItem(
                    food_name=name,
                    heat_score=_DEFAULT_HEAT_SCORE,
                    post_count=0,
                    category=category,
                )
            )

    return items
