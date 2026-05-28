"""AI 智能食物提取 — 用 Claude 从未匹配标题中发现新食物（带哈希缓存）。"""

import hashlib
import json
import logging
from dataclasses import dataclass

from anthropic import Anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AI_CORE_RULES, ANTHROPIC_API_KEY, ANTHROPIC_MODEL, AI_EXTRACT_ENABLED
from app.crawler.base import FoodTrendItem  # noqa: F401  crawlers still use this
from app.database import SessionLocal
from app.models import AITitleCache

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {
    "正餐", "小吃", "面食", "烧烤", "火锅", "西餐",
    "日料", "韩餐", "东南亚", "甜品", "饮品", "早餐",
    "轻食", "点心", "零食",
}

VALID_TREND_TYPES = {"event", "seasonal", "evergreen", "meme"}


@dataclass
class ExtractedFoodItem:
    name: str
    category: str | None = None
    canonical_of: str | None = None
    trend_type: str | None = None
    trend_context: str | None = None
    source_title: str | None = None


_MAX_TITLES_PER_BATCH = 50
_DEFAULT_HEAT_SCORE = 50

_SYSTEM_PROMPT = f"""{AI_CORE_RULES}

你是一个美食识别+趋势分析专家。给你一批热搜标题，请对每条标题完成 3 件事：
1. 提取具体食物/菜品/饮品名称
2. 给每个食物归入分类
3. 判断该食物当前热度的归因类型 + 关联上下文

规则：
- 只提取具体食物名，不要提取泛称如"美食"、"小吃"
- 食物名长度 2-10 个字
- 每个食物必须归入：正餐/小吃/面食/烧烤/火锅/西餐/日料/韩餐/东南亚/甜品/饮品/早餐/轻食/点心/零食
- canonical_of：如果这个食物是某个已知食物的别名（如"川式火锅"→"火锅"、"酱香拿铁"→"拿铁"），填规范名；否则填本名
- trend_type：event(综艺/直播/事件带火) | seasonal(季节相关) | evergreen(长青品类) | meme(网络梗)
- trend_context：≤15 字，解释为何火（如"综艺XX同款"、"入冬涮锅季"）；如果是 evergreen 可为空
- 如果标题没有食物，foods 返回空数组
- 只返回真实存在的食物，不要编造"""


def _hash_title(title: str) -> str:
    return hashlib.sha256(title.strip().encode()).hexdigest()


def _load_cached(
    db: Session, titles: list[str]
) -> tuple[list[ExtractedFoodItem], list[str]]:
    """从缓存加载已处理标题的结果，返回 (缓存命中的items, 需要调 AI 的titles)。"""
    cached_items: list[ExtractedFoodItem] = []
    uncached_titles: list[str] = []

    for title in titles:
        h = _hash_title(title)
        row = db.execute(
            select(AITitleCache).where(AITitleCache.title_hash == h)
        ).scalar_one_or_none()

        if row:
            try:
                foods = json.loads(row.extracted_foods)
            except json.JSONDecodeError:
                uncached_titles.append(title)
                continue
            for food in foods:
                name = food.get("name", "")
                if not name:
                    continue
                cached_items.append(ExtractedFoodItem(
                    name=name,
                    category=food.get("category"),
                    canonical_of=food.get("canonical_of") or name,
                    trend_type=food.get("trend_type"),
                    trend_context=food.get("trend_context"),
                    source_title=title,
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


def extract_foods_from_titles(titles: list[str]) -> list[ExtractedFoodItem]:
    """从未匹配的热搜标题中用 AI 提取食物名（带哈希缓存）。"""
    if not AI_EXTRACT_ENABLED:
        logger.info("AI 提取已禁用")
        return []

    if not ANTHROPIC_API_KEY:
        logger.warning("未配置 ANTHROPIC_API_KEY，跳过 AI 提取")
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

        ai_items: list[ExtractedFoodItem] = []
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
        seen: dict[str, ExtractedFoodItem] = {}
        for item in all_items:
            if item.name not in seen:
                seen[item.name] = item

        result = list(seen.values())
        logger.info("AI 提取: 发现 %d 种新食物 (缓存 %d + AI %d)",
                     len(result), len(cached_items), len(ai_items))
        return result
    finally:
        db.close()


def _extract_batch(
    titles: list[str],
) -> tuple[list[ExtractedFoodItem], dict[str, list[dict]]]:
    """对一批标题调用 Claude 提取食物，返回 (items, {title: [food_dicts]})。"""
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    titles_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    user_prompt = f"""请对以下热搜标题提取食物 + 归因。

热搜标题：
{titles_text}

请严格按以下 JSON 格式返回（无 markdown，仅 JSON）：
{{"results": [{{"title": "原标题", "foods": [{{"name": "食物名", "category": "分类", "canonical_of": "规范名或本名", "trend_type": "event|seasonal|evergreen|meme", "trend_context": "归因短语"}}]}}]}}

如果某个标题没有食物，其 foods 为空数组。"""

    resp = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=2000,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return _parse_response(next((b.text for b in resp.content if getattr(b, "type", None) == "text"), ""), titles)


def _parse_response(
    text: str, original_titles: list[str]
) -> tuple[list[ExtractedFoodItem], dict[str, list[dict]]]:
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

    items: list[ExtractedFoodItem] = []
    title_mapping: dict[str, list[dict]] = {t: [] for t in original_titles}

    for result in data.get("results", []):
        title = result.get("title", "")
        foods_for_title: list[dict] = []
        for food in result.get("foods", []):
            name = food.get("name", "").strip()
            category = food.get("category", "").strip()
            canonical_of = food.get("canonical_of", "").strip() or name
            trend_type = food.get("trend_type", "").strip() or None
            trend_context = food.get("trend_context", "").strip() or None

            if not name or len(name) < 2 or len(name) > 10:
                continue
            if category not in VALID_CATEGORIES:
                category = "小吃"
            if trend_type not in VALID_TREND_TYPES:
                trend_type = None
            if trend_context and len(trend_context) > 15:
                trend_context = trend_context[:15]

            food_dict = {
                "name": name,
                "category": category,
                "canonical_of": canonical_of,
                "trend_type": trend_type,
                "trend_context": trend_context,
            }
            foods_for_title.append(food_dict)
            items.append(ExtractedFoodItem(
                name=name,
                category=category,
                canonical_of=canonical_of,
                trend_type=trend_type,
                trend_context=trend_context,
                source_title=title,
            ))

        if title in title_mapping:
            title_mapping[title] = foods_for_title

    return items, title_mapping
