"""DailyHotApi 聚合热榜爬虫 — 通过自部署 DailyHotApi 拉取多平台热搜，筛选美食条目。

DailyHotApi (imsyy/dailyhot-api) 聚合了微博、抖音、B站、百度等热榜数据。
需要通过 Docker 自部署，默认端口 6688。
"""

import logging
import os

import httpx

from app.crawler.base import BaseCrawler, FoodTrendItem
from app.crawler.food_keywords import get_category, match_food_in_text

logger = logging.getLogger(__name__)

DAILYHOT_API_URL = os.getenv("DAILYHOT_API_URL", "http://localhost:6688")

# 要拉取的平台列表
PLATFORMS = ["weibo", "douyin", "bilibili", "baidu"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


class DailyHotCrawler(BaseCrawler):
    """DailyHotApi 聚合热榜爬虫。

    从自部署的 DailyHotApi 拉取微博、抖音、B站、百度四个平台的热榜，
    用 match_food_in_text 过滤出美食相关条目。
    每个平台独立 try/except，一个失败不影响其他平台。
    """

    def get_source_name(self) -> str:
        return "dailyhot"

    def crawl(self) -> list[FoodTrendItem]:
        all_items: list[FoodTrendItem] = []
        for platform in PLATFORMS:
            try:
                items = self._fetch_platform(platform)
                all_items.extend(items)
            except Exception:
                logger.warning("DailyHot %s 拉取失败", platform, exc_info=True)
        all_items = self._deduplicate(all_items)
        logger.info("DailyHot: 共获取 %d 条美食相关", len(all_items))
        return all_items

    def _fetch_platform(self, platform: str) -> list[FoodTrendItem]:
        """拉取单个平台的热榜并筛选美食条目。"""
        url = f"{DAILYHOT_API_URL}/{platform}"
        resp = httpx.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        items: list[FoodTrendItem] = []
        for entry in data.get("data", []):
            title = entry.get("title", "")
            food_name = match_food_in_text(title)
            if not food_name:
                continue

            hot_value = entry.get("hot", 0)
            if isinstance(hot_value, str):
                hot_value = self._parse_hot(hot_value)
            hot_value = int(hot_value) if hot_value else 0

            items.append(
                FoodTrendItem(
                    food_name=food_name,
                    heat_score=self._normalize_score(hot_value),
                    post_count=hot_value,
                    category=get_category(food_name),
                )
            )
        return items

    @staticmethod
    def _parse_hot(hot_str: str) -> int:
        """解析热度字符串，如 '1234', '56.7万' 等。"""
        if not hot_str:
            return 0
        hot_str = hot_str.strip().replace(",", "")
        try:
            if "亿" in hot_str:
                num = float(hot_str.replace("亿", "").strip())
                return int(num * 100_000_000)
            if "万" in hot_str:
                num = float(hot_str.replace("万", "").strip())
                return int(num * 10_000)
            return int(float(hot_str))
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _normalize_score(hot_value: int) -> int:
        """将热度值归一化到 0-100 区间。"""
        if hot_value >= 10_000_000:
            return 100
        if hot_value >= 5_000_000:
            return 90 + (hot_value - 5_000_000) * 10 // 5_000_000
        if hot_value >= 1_000_000:
            return 70 + (hot_value - 1_000_000) * 20 // 4_000_000
        if hot_value >= 100_000:
            return 40 + (hot_value - 100_000) * 30 // 900_000
        return max(1, hot_value * 40 // 100_000)

    @staticmethod
    def _deduplicate(items: list[FoodTrendItem]) -> list[FoodTrendItem]:
        """同名食物去重，保留最高热度。"""
        seen: dict[str, FoodTrendItem] = {}
        for item in items:
            if item.food_name in seen:
                existing = seen[item.food_name]
                existing.heat_score = max(existing.heat_score, item.heat_score)
                existing.post_count = max(existing.post_count, item.post_count)
            else:
                seen[item.food_name] = item
        return list(seen.values())
