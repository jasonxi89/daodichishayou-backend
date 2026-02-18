import pytest
from sqlalchemy import text


def test_get_db_yields_session():
    from app.database import get_db
    gen = get_db()
    db = next(gen)
    assert db is not None
    try:
        next(gen)
    except StopIteration:
        pass


def test_db_session_can_execute(db):
    result = db.execute(text("SELECT 1")).scalar()
    assert result == 1


def test_base_metadata_has_tables():
    from app.database import Base
    assert "food_trends" in Base.metadata.tables
    assert "crawl_logs" in Base.metadata.tables
