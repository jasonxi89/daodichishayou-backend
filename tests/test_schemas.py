import pytest
from pydantic import ValidationError


def test_food_trend_import_defaults():
    from app.schemas import FoodTrendImport
    item = FoodTrendImport(food_name="火锅")
    assert item.source == "manual"
    assert item.heat_score == 0
    assert item.post_count == 0
    assert item.category is None
    assert item.image_url is None


def test_food_trend_import_full():
    from app.schemas import FoodTrendImport
    item = FoodTrendImport(
        food_name="奶茶", source="toutiao", heat_score=87, post_count=70000, category="饮品"
    )
    assert item.food_name == "奶茶"
    assert item.category == "饮品"


def test_ingredient_recommend_request_defaults():
    from app.schemas import IngredientRecommendRequest
    req = IngredientRecommendRequest(ingredients=["番茄", "鸡蛋"])
    assert req.count == 3
    assert req.preferences is None


def test_ingredient_recommend_request_with_prefs():
    from app.schemas import IngredientRecommendRequest
    req = IngredientRecommendRequest(ingredients=["猪肉"], count=2, preferences="家常")
    assert req.count == 2
    assert req.preferences == "家常"


def test_health_response():
    from app.schemas import HealthResponse
    r = HealthResponse(status="ok", version="0.1.0")
    assert r.status == "ok"


def test_crawl_result():
    from app.schemas import CrawlResult
    r = CrawlResult(source="toutiao", status="success", items_count=10, message="done")
    assert r.items_count == 10


def test_recommended_dish_optional_fields():
    from app.schemas import RecommendedDish
    d = RecommendedDish(name="番茄炒蛋", summary="家常菜", ingredients=["番茄", "鸡蛋"], steps=["炒"])
    assert d.difficulty is None
    assert d.cook_time is None


def test_trending_response():
    from app.schemas import TrendingResponse
    r = TrendingResponse(total=0, items=[])
    assert r.total == 0
    assert r.items == []


def test_ingredient_recommend_request_allow_extra_default():
    from app.schemas import IngredientRecommendRequest
    req = IngredientRecommendRequest(ingredients=["番茄"])
    assert req.allow_extra is False


def test_ingredient_recommend_request_exclude_dishes_default():
    from app.schemas import IngredientRecommendRequest
    req = IngredientRecommendRequest(ingredients=["番茄"])
    assert req.exclude_dishes == []


def test_recommended_dish_extra_ingredients():
    from app.schemas import RecommendedDish
    d = RecommendedDish(
        name="番茄牛腩",
        summary="经典炖菜",
        ingredients=["番茄", "牛腩"],
        steps=["炖煮"],
        extra_ingredients=["牛腩"],
    )
    assert d.extra_ingredients == ["牛腩"]

    # Also verify default is None when not provided
    d2 = RecommendedDish(
        name="番茄炒蛋",
        summary="家常菜",
        ingredients=["番茄", "鸡蛋"],
        steps=["炒"],
    )
    assert d2.extra_ingredients is None


def test_generate_foods_request_defaults():
    from app.schemas import GenerateFoodsRequest
    req = GenerateFoodsRequest(category="川菜")
    assert req.category == "川菜"
    assert req.count == 30


def test_generate_foods_request_custom():
    from app.schemas import GenerateFoodsRequest
    req = GenerateFoodsRequest(category="粤菜", count=10)
    assert req.category == "粤菜"
    assert req.count == 10


def test_generate_foods_response():
    from app.schemas import GenerateFoodsResponse
    resp = GenerateFoodsResponse(foods=["火锅", "串串香"], category="川菜")
    assert resp.foods == ["火锅", "串串香"]
    assert resp.category == "川菜"
