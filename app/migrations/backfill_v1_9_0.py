"""v1.9.0 迁移脚本：给已有表加新列 + backfill canonical_name + 种子 food_aliases。

项目未用 Alembic，`Base.metadata.create_all()` 只创建不存在的表，不会给已存在的表加列。
本脚本在启动时调用，幂等可重跑。
"""

import logging

from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.orm import Session

from app.models import FoodAlias, FoodTrend

logger = logging.getLogger(__name__)


def _column_exists(conn, table: str, column: str) -> bool:
    result = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in result)


def _add_column_if_missing(conn, table: str, column: str, column_type: str) -> None:
    if not _column_exists(conn, table, column):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"))
        logger.info("已添加列 %s.%s", table, column)


def migrate_v1_9_0(engine: Engine) -> None:
    """幂等迁移：加列 → 建索引 → backfill canonical_name → 种子 food_aliases。"""
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())

    required = {"food_trends", "ai_discovered_foods", "food_aliases"}
    missing = required - existing_tables
    if missing:
        logger.warning("缺少必要表，跳过 v1.9.0 迁移: %s", missing)
        return

    with engine.connect() as conn:
        _add_column_if_missing(conn, "food_trends", "canonical_name", "VARCHAR(100)")
        _add_column_if_missing(conn, "food_trends", "trend_type", "VARCHAR(20)")
        _add_column_if_missing(conn, "food_trends", "trend_context", "VARCHAR(100)")
        _add_column_if_missing(
            conn,
            "ai_discovered_foods",
            "promoted_to_trends",
            "BOOLEAN NOT NULL DEFAULT 0",
        )

        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_food_trends_canonical "
            "ON food_trends(canonical_name)"
        ))

        conn.execute(text(
            "UPDATE food_trends SET canonical_name = food_name "
            "WHERE canonical_name IS NULL"
        ))
        conn.commit()
        logger.info("v1.9.0 列迁移完成")

    with Session(engine) as s:
        existing_aliases = {
            row for row in s.execute(select(FoodAlias.alias_name)).scalars().all()
        }
        unique_food_names = {
            row for row in s.execute(select(FoodTrend.food_name).distinct()).scalars().all()
        }
        to_insert = unique_food_names - existing_aliases
        for name in to_insert:
            s.add(FoodAlias(
                alias_name=name,
                canonical_name=name,
                created_by="manual",
            ))
        if to_insert:
            s.commit()
            logger.info("v1.9.0 种子 food_aliases 插入 %d 条", len(to_insert))
