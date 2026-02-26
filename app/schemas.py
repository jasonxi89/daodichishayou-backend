from datetime import datetime

from pydantic import BaseModel


class FoodTrendOut(BaseModel):
    id: int
    food_name: str
    source: str
    heat_score: int
    post_count: int
    category: str | None = None
    image_url: str | None = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class TrendingResponse(BaseModel):
    total: int
    items: list[FoodTrendOut]


class FoodTrendImport(BaseModel):
    food_name: str
    source: str = "manual"
    heat_score: int = 0
    post_count: int = 0
    category: str | None = None
    image_url: str | None = None


class CrawlResult(BaseModel):
    source: str
    status: str
    items_count: int
    message: str


class HealthResponse(BaseModel):
    status: str
    version: str


class IngredientRecommendRequest(BaseModel):
    ingredients: list[str]
    count: int = 3
    preferences: str | None = None
    allow_extra: bool = False
    exclude_dishes: list[str] = []


class RecommendedDish(BaseModel):
    name: str
    summary: str
    ingredients: list[str]
    steps: list[str]
    difficulty: str | None = None
    cook_time: str | None = None
    extra_ingredients: list[str] | None = None


class IngredientRecommendResponse(BaseModel):
    dishes: list[RecommendedDish]
    input_ingredients: list[str]


class GenerateFoodsRequest(BaseModel):
    category: str
    count: int = 30


class GenerateFoodsResponse(BaseModel):
    foods: list[str]
    category: str
