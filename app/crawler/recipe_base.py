from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class RecipeItem:
    name: str
    source_url: str
    rating: float | None = None
    made_count: int = 0
    image_url: str | None = None
    author: str | None = None
    ingredients: list[dict] | None = None
    ingredients_text: str | None = None
    steps: list[dict] | None = None
    category: str | None = None
    list_source: str | None = None


class BaseRecipeScraper(ABC):
    @abstractmethod
    def get_source_name(self) -> str: ...

    @abstractmethod
    def scrape(self, existing_urls: set[str] | None = None) -> list[RecipeItem]: ...
