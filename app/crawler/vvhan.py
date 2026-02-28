"""vvhan 聚合热榜爬虫 — 通过 vvhan.com 免费 API 拉取多平台热搜，筛选美食条目。

vvhan API 提供微博、抖音、B站等热榜数据，无需认证，免费使用。
"""

import logging
import re

import httpx

from app.crawler.base import BaseCrawler, FoodTrendItem
from app.crawler.food_keywords import get_category, match_food_in_text

logger = logging.getLogger(__name__)

BASE_URL = "https://api.vvhan.com/api/hotlist"

# 要拉取的平台 endpoint
PLATFORMS = {
    "wbHot": "微博",
    "douyinHot": "抖音",
    "biliHot": "B站",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


class VvhanCrawler(BaseCrawler):
    """vvhan 聚合热榜爬虫。

    从 vvhan.com API 拉取微博、抖音、B站三个平台的热榜，
    用 match_food_in_text 过滤出美食相关条目。
    每个平台独立 try/except，一个失败不影响其他平台。
    """

    def get_source_name(self) -> str:
        return "vvhan"

    def crawl(self) -> list[FoodTrendItem]:
        self.unmatched_titles = []
        all_items: list[FoodTrendItem] = []
        for endpoint, name in PLATFORMS.items():
            try:
                items = self._fetch_platform(endpoint)
                all_items.extend(items)
            except Exception:
                logger.warning("vvhan %s 拉取失败", name, exc_info=True)
        all_items = self._deduplicate(all_items)
        logger.info("vvhan: 共获取 %d 条美食相关", len(all_items))
        return all_items

    def _fetch_platform(self, endpoint: str) -> list[FoodTrendItem]:
        """拉取单个平台的热榜并筛选美食条目。"""
        url = f"{BASE_URL}/{endpoint}"
        resp = httpx.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            logger.warning("vvhan %s 返回失败: %s", endpoint, data.get("message", ""))
            return []

        items: list[FoodTrendItem] = []
        for entry in data.get("data", []):
            title = entry.get("title", "")
            food_name = match_food_in_text(title)
            if not food_name:
                if title:
                    self.unmatched_titles.append(title)
                continue

            hot_value = self._parse_hot(entry.get("hot", 0))

            items.append(
                FoodTrendItem(
                    food_name=food_name,
                    heat_score=self._normalize_score(hot_value),
                    post_count=hot_value,
                    category=get_category(food_name),
                    image_url=entry.get("pic"),
                )
            )
        return items

    @staticmethod
    def _parse_hot(hot_raw) -> int:
        """解析热度值，支持数字和字符串格式如 '123万热度', '5.6万', '12345'。"""
        if isinstance(hot_raw, (int, float)):
            return int(hot_raw)
        if not isinstance(hot_raw, str):
            return 0

        hot_str = hot_raw.strip()
        # 移除尾部的"热度"等中文后缀
        hot_str = re.sub(r"[热度搜]+$", "", hot_str).strip()
        hot_str = hot_str.replace(",", "")

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
