from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class FoodTrendOut(BaseModel):
    id: int
    food_name: str
    source: str
    heat_score: int
    post_count: int
    category: str | None = None
    image_url: str | None = None
    updated_at: datetime
    canonical_name: str | None = None
    aliases: list[str] = []
    sources: list[str] = []
    trend_type: str | None = None
    trend_context: str | None = None

    model_config = {"from_attributes": True}


class TrendingResponse(BaseModel):
    total: int
    items: list[FoodTrendOut]


class AnnotatedCategory(BaseModel):
    name: str
    note: str | None = None


class AnnotatedCategoriesResponse(BaseModel):
    categories: list[AnnotatedCategory]


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


def _normalized_text_list(
    values: list[str],
    *,
    maximum_items: int,
    maximum_length: int,
) -> list[str]:
    if len(values) > maximum_items:
        raise ValueError(f"At most {maximum_items} values are allowed")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Values must not be blank")
        if len(cleaned) > maximum_length:
            raise ValueError(f"Values must be at most {maximum_length} characters")
        if any(ord(character) < 32 for character in cleaned):
            raise ValueError("Control characters are not allowed")
        folded = cleaned.casefold()
        if folded not in seen:
            normalized.append(cleaned)
            seen.add(folded)
    return normalized


class IngredientRecommendRequest(BaseModel):
    ingredients: list[str]
    count: int = 3
    preferences: str | None = None
    allow_extra: bool = False
    exclude_dishes: list[str] = []

    @field_validator("ingredients")
    @classmethod
    def validate_ingredients(cls, values: list[str]) -> list[str]:
        normalized = _normalized_text_list(
            values,
            maximum_items=20,
            maximum_length=64,
        )
        if not normalized:
            raise ValueError("At least one ingredient is required")
        return normalized

    @field_validator("exclude_dishes")
    @classmethod
    def validate_exclusions(cls, values: list[str]) -> list[str]:
        return _normalized_text_list(
            values,
            maximum_items=50,
            maximum_length=100,
        ) if values else []

    @field_validator("preferences")
    @classmethod
    def validate_preferences(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if len(cleaned) > 200:
            raise ValueError("Preferences must be at most 200 characters")
        return cleaned


class RecommendedDish(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=500)
    ingredients: list[str]
    steps: list[str]
    difficulty: str | None = None
    cook_time: str | None = None
    extra_ingredients: list[str] | None = None

    @field_validator("name", "summary")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Dish text fields must not be blank")
        return cleaned

    @field_validator("ingredients", "steps")
    @classmethod
    def validate_required_lists(cls, values: list[str]) -> list[str]:
        normalized = _normalized_text_list(
            values,
            maximum_items=50,
            maximum_length=500,
        )
        if not normalized:
            raise ValueError("Dish ingredients and steps must not be empty")
        return normalized

    @field_validator("extra_ingredients")
    @classmethod
    def validate_extra_ingredients(
        cls, values: list[str] | None
    ) -> list[str] | None:
        if not values:
            return None
        return _normalized_text_list(
            values,
            maximum_items=20,
            maximum_length=100,
        )


class IngredientRecommendResponse(BaseModel):
    dishes: list[RecommendedDish] = Field(min_length=1, max_length=5)
    input_ingredients: list[str]


class QuickRecommendedDish(BaseModel):
    name: str
    summary: str
    difficulty: str | None = None
    cook_time: str | None = None


class QuickRecommendResponse(BaseModel):
    dishes: list[QuickRecommendedDish]
    input_ingredients: list[str]


class DishStepsRequest(BaseModel):
    dish_name: str
    ingredients: list[str]
    preferences: str | None = None
    allow_extra: bool = False

    @field_validator("dish_name")
    @classmethod
    def validate_dish_name(cls, value: str) -> str:
        cleaned = value.strip()
        if (
            not cleaned
            or len(cleaned) > 100
            or cleaned in {"%", "_"}
            or any(ord(character) < 32 for character in cleaned)
        ):
            raise ValueError("A valid dish name is required")
        return cleaned

    @field_validator("ingredients")
    @classmethod
    def validate_step_ingredients(cls, values: list[str]) -> list[str]:
        normalized = _normalized_text_list(
            values,
            maximum_items=20,
            maximum_length=64,
        )
        if not normalized:
            raise ValueError("At least one ingredient is required")
        return normalized

    @field_validator("preferences")
    @classmethod
    def validate_step_preferences(cls, value: str | None) -> str | None:
        return IngredientRecommendRequest.validate_preferences(value)


class GenerateFoodsRequest(BaseModel):
    category: str
    count: int = 30


class GenerateFoodsResponse(BaseModel):
    foods: list[str]
    category: str


class BulkGenerateFoodsRequest(BaseModel):
    categories: list[str]
    count: int = 30


class BulkGenerateFoodsResponse(BaseModel):
    results: dict[str, list[str]]


class FoodDigestOut(BaseModel):
    id: int
    digest_date: datetime
    summary: str
    top_foods: list[str]
    recommendation: str | None = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class FoodTrendSnapshotOut(BaseModel):
    food_name: str
    heat_score: int
    source: str
    category: str | None = None
    snapshot_date: datetime

    model_config = {"from_attributes": True}


class TrendHistoryResponse(BaseModel):
    food_name: str
    history: list[FoodTrendSnapshotOut]


class RecipeOut(BaseModel):
    id: int
    name: str
    rating: float | None = None
    made_count: int = 0
    image_url: str | None = None
    author: str | None = None
    ingredients_json: str | None = None
    ingredients_text: str | None = None
    steps_json: str | None = None
    category: str | None = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class RecipeSearchResponse(BaseModel):
    total: int
    items: list[RecipeOut]
