"""recipes.steps_source 列迁移：老库补列，幂等可重跑。"""
from sqlalchemy import create_engine, inspect, text


def _make_legacy_engine():
    """建一个没有 steps_source 列的老版 recipes 表。"""
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE recipes ("
            "id INTEGER PRIMARY KEY, name VARCHAR(200), "
            "source_url VARCHAR(500), steps_json TEXT)"
        ))
        conn.commit()
    return engine


def test_migration_adds_steps_source_column():
    from app.migrations.add_steps_source import migrate_steps_source

    engine = _make_legacy_engine()
    migrate_steps_source(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("recipes")}
    assert "steps_source" in columns


def test_migration_is_idempotent():
    from app.migrations.add_steps_source import migrate_steps_source

    engine = _make_legacy_engine()
    migrate_steps_source(engine)
    migrate_steps_source(engine)  # 二次执行不得报错
    columns = {c["name"] for c in inspect(engine).get_columns("recipes")}
    assert "steps_source" in columns


def test_migration_skips_when_table_missing():
    from app.migrations.add_steps_source import migrate_steps_source

    engine = create_engine("sqlite:///:memory:")
    migrate_steps_source(engine)  # 无 recipes 表时静默跳过，不抛异常
