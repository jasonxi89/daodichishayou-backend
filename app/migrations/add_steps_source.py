"""v1.14.1 迁移：recipes 表补 steps_source 列（步骤数据来源标记）。

幂等：列已存在时跳过；recipes 表不存在时静默返回（create_all 会带列建表）。
"""
import logging

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.migrations.backfill_v1_9_0 import _add_column_if_missing

logger = logging.getLogger(__name__)


def migrate_steps_source(engine: Engine) -> None:
    if "recipes" not in inspect(engine).get_table_names():
        logger.warning("recipes 表不存在，跳过 steps_source 迁移")
        return
    with engine.connect() as conn:
        _add_column_if_missing(conn, "recipes", "steps_source", "VARCHAR(20)")
        conn.commit()
