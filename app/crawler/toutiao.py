"""头条热搜爬虫 — 从今日头条热榜中筛选美食相关话题。"""

import logging

import httpx

from app.crawler.base import BaseCrawler, FoodTrendItem
from app.crawler.food_keywords import get_category, is_food_related, match_food_in_text

logger = logging.getLogger(__name__)

HOT_BOARD_URL = "https://www.toutiao.com/hot-event/hot-board/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


class ToutiaoCrawler(BaseCrawler):
    """今日头条热搜爬虫。

    从头条热榜 API 获取实时热搜，筛选出美食相关条目。
    API 无需认证，返回 50 条热搜，包含标题、热度值、分类、图片。
    """

    def get_source_name(self) -> str:
        return "toutiao"

    def crawl(self) -> list[FoodTrendItem]:
        self.unmatched_titles = []
        try:
            resp = httpx.get(
                HOT_BOARD_URL,
                params={"origin": "toutiao_pc"},
                headers=HEADERS,
                follow_redirects=True,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.error("头条热搜请求失败", exc_info=True)
            return []

        items: list[FoodTrendItem] = []
        for entry in data.get("data", []):
            title = entry.get("Title", "")
            # 必须匹配到具体食物名才收录，避免"饭店倒闭"之类的误匹配
            food_name = match_food_in_text(title)
            if not food_name:
                if title:
                    self.unmatched_titles.append(title)
                continue
            hot_value = int(entry.get("HotValue", 0))

            image_url = None
            img = entry.get("Image")
            if img and img.get("url"):
                image_url = img["url"]

            items.append(
                FoodTrendItem(
                    food_name=food_name,
                    heat_score=self._normalize_score(hot_value),
                    post_count=hot_value,
                    category=get_category(food_name),
                    image_url=image_url,
                )
            )

        logger.info("头条热搜: 获取 %d 条美食相关", len(items))
        return items

    @staticmethod
    def _normalize_score(hot_value: int) -> int:
        """将头条热度值归一化到 0-100 区间。"""
        if hot_value >= 10_000_000:
            return 100
        if hot_value >= 5_000_000:
            return 90 + (hot_value - 5_000_000) * 10 // 5_000_000
        if hot_value >= 1_000_000:
            return 70 + (hot_value - 1_000_000) * 20 // 4_000_000
        if hot_value >= 100_000:
            return 40 + (hot_value - 100_000) * 30 // 900_000
        return max(1, hot_value * 40 // 100_000)
