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
