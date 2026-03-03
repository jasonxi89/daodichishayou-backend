import pytest
from sqlalchemy.exc import IntegrityError


def test_recipe_create(db):
    from app.models import Recipe

    r = Recipe(
        name="番茄炒蛋",
        source_url="https://example.com/recipe/1",
        rating=8.5,
        made_count=1000,
        category="honor",
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    assert r.id is not None
    assert r.name == "番茄炒蛋"
    assert r.rating == 8.5
    assert r.made_count == 1000


def test_recipe_unique_source_url(db):
    from app.models import Recipe

    r1 = Recipe(name="菜1", source_url="https://example.com/recipe/1")
    r2 = Recipe(name="菜2", source_url="https://example.com/recipe/1")
    db.add(r1)
    db.commit()
    db.add(r2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_recipe_nullable_fields(db):
    from app.models import Recipe

    r = Recipe(name="测试菜谱", source_url="https://example.com/recipe/2")
    db.add(r)
    db.commit()
    db.refresh(r)
    assert r.rating is None
    assert r.made_count == 0
    assert r.image_url is None
    assert r.author is None
    assert r.ingredients_json is None
    assert r.ingredients_text is None
    assert r.steps_json is None
    assert r.category is None


def test_recipe_timestamps(db):
    from app.models import Recipe

    r = Recipe(name="时间测试", source_url="https://example.com/recipe/3")
    db.add(r)
    db.commit()
    db.refresh(r)
    assert r.created_at is not None
    assert r.updated_at is not None


def test_recipe_with_full_data(db):
    from app.models import Recipe

    r = Recipe(
        name="宫保鸡丁",
        source_url="https://example.com/recipe/4",
        rating=9.0,
        made_count=5000,
        image_url="https://img.example.com/1.jpg",
        author="厨师小王",
        ingredients_json='[{"name":"鸡胸肉","amount":"200g"}]',
        ingredients_text="鸡胸肉 花生 干辣椒",
        steps_json='[{"text":"切丁"},{"text":"炒制"}]',
        category="honor",
        list_source="xiachufang",
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    assert r.author == "厨师小王"
    assert r.list_source == "xiachufang"
    assert "鸡胸肉" in r.ingredients_text
