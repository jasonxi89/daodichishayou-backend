"""AI 智能食物提取 — 用 Claude 从未匹配标题中发现新食物（带哈希缓存）。"""

import hashlib
import json
import logging

from anthropic import Anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AI_CORE_RULES, CLAUDE_API_KEY, CLAUDE_MODEL, AI_EXTRACT_ENABLED
from app.crawler.base import FoodTrendItem
from app.crawler.food_keywords import FOOD_NAMES
from app.database import SessionLocal
from app.models import AITitleCache

logger = logging.getLogger(__name__)

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


def _hash_title(title: str) -> str:
    return hashlib.sha256(title.strip().encode()).hexdigest()


def _load_cached(db: Session, titles: list[str]) -> tuple[list[FoodTrendItem], list[str]]:
    """从缓存加载已处理标题的结果，返回 (缓存命中的items, 需要调 AI 的titles)。"""
    cached_items: list[FoodTrendItem] = []
    uncached_titles: list[str] = []

    for title in titles:
        h = _hash_title(title)
        row = db.execute(
            select(AITitleCache).where(AITitleCache.title_hash == h)
        ).scalar_one_or_none()

        if row:
            foods = json.loads(row.extracted_foods)
            for food in foods:
                name = food.get("name", "")
                if name and name not in FOOD_NAMES:
                    cached_items.append(FoodTrendItem(
                        food_name=name,
                        heat_score=_DEFAULT_HEAT_SCORE,
                        post_count=0,
                        category=food.get("category", "小吃"),
                    ))
        else:
            uncached_titles.append(title)

    return cached_items, uncached_titles


def _save_cache(db: Session, title: str, foods: list[dict]) -> None:
    """缓存单条标题的提取结果。"""
    h = _hash_title(title)
    existing = db.execute(
        select(AITitleCache).where(AITitleCache.title_hash == h)
    ).scalar_one_or_none()
    if not existing:
        db.add(AITitleCache(
            title_hash=h,
            title=title[:500],
            extracted_foods=json.dumps(foods, ensure_ascii=False),
        ))


def extract_foods_from_titles(titles: list[str]) -> list[FoodTrendItem]:
    """从未匹配的热搜标题中用 AI 提取食物名（带哈希缓存）。"""
    if not AI_EXTRACT_ENABLED:
        logger.info("AI 提取已禁用")
        return []

    if not CLAUDE_API_KEY:
        logger.warning("未配置 CLAUDE_API_KEY，跳过 AI 提取")
        return []

    if not titles:
        return []

    unique_titles = list(dict.fromkeys(titles))

    db = SessionLocal()
    try:
        cached_items, uncached_titles = _load_cached(db, unique_titles)
        logger.info(
            "标题缓存: %d 命中, %d 需调用 AI",
            len(unique_titles) - len(uncached_titles),
            len(uncached_titles),
        )

        ai_items: list[FoodTrendItem] = []
        if uncached_titles:
            for i in range(0, len(uncached_titles), _MAX_TITLES_PER_BATCH):
                batch = uncached_titles[i:i + _MAX_TITLES_PER_BATCH]
                try:
                    batch_items, batch_mapping = _extract_batch(batch)
                    ai_items.extend(batch_items)
                    # 缓存每条标题的结果
                    for title in batch:
                        foods = batch_mapping.get(title, [])
                        _save_cache(db, title, foods)
                    db.commit()
                except Exception:
                    logger.error(
                        "AI 提取批次 %d 失败",
                        i // _MAX_TITLES_PER_BATCH,
                        exc_info=True,
                    )

        all_items = cached_items + ai_items
        # 去重
        seen: dict[str, FoodTrendItem] = {}
        for item in all_items:
            if item.food_name not in seen:
                seen[item.food_name] = item

        result = list(seen.values())
        logger.info("AI 提取: 发现 %d 种新食物 (缓存 %d + AI %d)",
                     len(result), len(cached_items), len(ai_items))
        return result
    finally:
        db.close()


def _extract_batch(
    titles: list[str],
) -> tuple[list[FoodTrendItem], dict[str, list[dict]]]:
    """对一批标题调用 Claude 提取食物，返回 (items, {title: [food_dicts]})。"""
    client = Anthropic(api_key=CLAUDE_API_KEY)

    titles_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    user_prompt = f"""请从以下热搜标题中提取具体的食物/菜品/饮品名称。

热搜标题：
{titles_text}

请严格按以下 JSON 格式返回：
{{"results": [{{"title": "原标题", "foods": [{{"name": "食物名", "category": "分类"}}]}}]}}

如果某个标题没有食物，其 foods 为空数组。"""

    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return _parse_response(resp.content[0].text, titles)


def _parse_response(
    text: str, original_titles: list[str]
) -> tuple[list[FoodTrendItem], dict[str, list[dict]]]:
    """解析 Claude 返回的 JSON，返回 items 和 title→foods 映射。"""
    json_text = text.strip()
    if json_text.startswith("```"):
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
        return [], {}

    items: list[FoodTrendItem] = []
    title_mapping: dict[str, list[dict]] = {t: [] for t in original_titles}

    for result in data.get("results", []):
        title = result.get("title", "")
        foods_for_title: list[dict] = []
        for food in result.get("foods", []):
            name = food.get("name", "").strip()
            category = food.get("category", "").strip()

            if not name or len(name) < 2 or len(name) > 10:
                continue
            if name in FOOD_NAMES:
                continue
            if category not in VALID_CATEGORIES:
                category = "小吃"

            foods_for_title.append({"name": name, "category": category})
            items.append(FoodTrendItem(
                food_name=name,
                heat_score=_DEFAULT_HEAT_SCORE,
                post_count=0,
                category=category,
            ))

        # 尝试匹配原标题
        if title in title_mapping:
            title_mapping[title] = foods_for_title

    return items, title_mapping
