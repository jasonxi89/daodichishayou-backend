from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class FoodTrendItem:
    food_name: str
    heat_score: int = 0
    post_count: int = 0
    category: str | None = None
    image_url: str | None = None


class BaseCrawler(ABC):
    @abstractmethod
    def get_source_name(self) -> str: ...

    @abstractmethod
    def crawl(self) -> list[FoodTrendItem]: ...
