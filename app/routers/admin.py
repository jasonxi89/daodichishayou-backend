"""管理端点 — 人工触发的一次性或低频操作（如 AI 别名合并）。"""

import json
import logging
from datetime import datetime, timezone

from anthropic import Anthropic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AI_CORE_RULES, DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_BASE_URL
from app.database import get_db
from app.models import FoodAlias, FoodTrend

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

_MERGE_BATCH_SIZE = 50

_MERGE_SYSTEM_PROMPT = f"""{AI_CORE_RULES}

你是一个食物同义词归并专家。给你一批食物名，请识别其中哪些是同一食物的别名或语义同类，输出规范化分组。

规则：
- 同一食物的变体归为一组（如"川式火锅"、"重庆火锅"、"四川火锅" → canonical="火锅"）
- canonical 必须是该组中最通用、最短的规范名
- 只归并语义上明确同类的词；若有疑虑，独立成组（每个词自成 canonical）
- 不要把不同食物强行归组

返回格式（纯 JSON，无 markdown）：
{{"groups": [{{"canonical": "火锅", "aliases": ["川式火锅", "重庆火锅"]}}, ...]}}
"""


@router.post("/merge-aliases")
def merge_aliases(db: Session = Depends(get_db)) -> dict:
    """扫描 food_trends 里所有 food_name，用 AI 生成 alias → canonical 映射。"""
    if not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=503, detail="DEEPSEEK_API_KEY 未配置")

    names = sorted({
        row for row in db.execute(
            select(FoodTrend.food_name).distinct()
        ).scalars().all()
    })

    if not names:
        return {"status": "ok", "groups_processed": 0, "aliases_created": 0}

    client = Anthropic(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    groups_processed = 0
    aliases_created = 0

    for i in range(0, len(names), _MERGE_BATCH_SIZE):
        batch = names[i:i + _MERGE_BATCH_SIZE]
        try:
            batch_groups = _call_merge(client, batch)
        except Exception:
            logger.error("batch %d 合并失败", i // _MERGE_BATCH_SIZE, exc_info=True)
            continue

        for group in batch_groups:
            canonical = group.get("canonical", "").strip()
            aliases = group.get("aliases", [])
            if not canonical:
                continue
            groups_processed += 1
            for alias in aliases:
                alias = alias.strip()
                if not alias or alias == canonical:
                    continue
                existing = db.execute(
                    select(FoodAlias).where(FoodAlias.alias_name == alias)
                ).scalar_one_or_none()
                if existing:
                    existing.canonical_name = canonical
                    existing.created_by = "ai"
                else:
                    db.add(FoodAlias(
                        alias_name=alias,
                        canonical_name=canonical,
                        created_by="ai",
                    ))
                    aliases_created += 1
                db.execute(
                    FoodTrend.__table__.update()
                    .where(FoodTrend.food_name == alias)
                    .values(canonical_name=canonical, updated_at=datetime.now(timezone.utc))
                )

        db.commit()

    return {
        "status": "ok",
        "groups_processed": groups_processed,
        "aliases_created": aliases_created,
        "total_names_scanned": len(names),
    }


def _call_merge(client: Anthropic, batch: list[str]) -> list[dict]:
    user_prompt = "请归并以下食物名（找出同义/变体）：\n" + "\n".join(
        f"- {n}" for n in batch
    )
    resp = client.messages.create(
        model=DEEPSEEK_MODEL,
        max_tokens=2000,
        system=_MERGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines)
    data = json.loads(raw)
    return data.get("groups", [])
