"""AI 美食趋势快报 — 每次爬虫后生成当日美食趋势总结。"""

import json
import logging
from datetime import date, datetime, timezone

from anthropic import Anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AI_CORE_RULES, CLAUDE_API_KEY, CLAUDE_MODEL
from app.models import FoodDigest, FoodTrend

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = f"""{AI_CORE_RULES}

你是一位美食趋势分析师。根据今日各平台热搜美食数据，生成一份简洁的美食趋势快报。

要求：
1. 总结当前最火的 3-5 种美食，说明为什么火（季节、节日、综艺、社交媒体带货等可能原因）
2. 发现趋势变化：哪些食物在上升、哪些在下降
3. 给出一句话"今日推荐"，适合当天吃的食物建议
4. 风格轻松有趣，适合年轻人阅读，100-200字即可
5. 只分析提供的数据，不要编造数据中没有的食物

返回格式（纯JSON，无markdown）：
{{"summary": "趋势快报正文", "top_foods": ["食物1", "食物2", "食物3"], "recommendation": "今日推荐一句话"}}"""


def generate_daily_digest(db: Session) -> FoodDigest | None:
    """基于当前热度数据生成今日美食趋势快报。"""
    if not CLAUDE_API_KEY:
        logger.warning("未配置 CLAUDE_API_KEY，跳过趋势总结")
        return None

    # 查询当前热度 Top 30
    top_items = (
        db.execute(
            select(FoodTrend)
            .order_by(FoodTrend.heat_score.desc())
            .limit(30)
        )
        .scalars()
        .all()
    )

    if not top_items:
        logger.info("没有热度数据，跳过趋势总结")
        return None

    # 构建数据摘要给 AI
    data_lines = []
    for item in top_items:
        data_lines.append(
            f"- {item.food_name}（{item.category or '未分类'}）"
            f" 热度:{item.heat_score} 来源:{item.source}"
        )
    data_text = "\n".join(data_lines)

    client = Anthropic(api_key=CLAUDE_API_KEY)
    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            system=_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"以下是今日各平台美食热度数据：\n\n{data_text}\n\n请生成今日美食趋势快报。",
            }],
        )
    except Exception:
        logger.error("AI 趋势总结调用失败", exc_info=True)
        return None

    raw_text = resp.content[0].text.strip()
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw_text = "\n".join(lines)

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.error("AI 趋势总结 JSON 解析失败: %s", raw_text[:300])
        return None

    today = date.today()
    summary = data.get("summary", "")
    top_foods = data.get("top_foods", [])
    recommendation = data.get("recommendation", "")

    # Upsert: 同一天只保留最新一条
    existing = db.execute(
        select(FoodDigest).where(FoodDigest.digest_date == today)
    ).scalar_one_or_none()

    if existing:
        existing.summary = summary
        existing.top_foods = json.dumps(top_foods, ensure_ascii=False)
        existing.recommendation = recommendation
        existing.updated_at = datetime.now(timezone.utc)
        digest = existing
    else:
        digest = FoodDigest(
            digest_date=today,
            summary=summary,
            top_foods=json.dumps(top_foods, ensure_ascii=False),
            recommendation=recommendation,
        )
        db.add(digest)

    db.commit()
    db.refresh(digest)
    logger.info("今日美食趋势快报已生成: %s", today)
    return digest
