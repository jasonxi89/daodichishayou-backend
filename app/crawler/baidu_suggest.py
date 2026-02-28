"""百度搜索建议爬虫 — 通过百度联想词发现当前热门食物。

原理：百度搜索建议反映了实时用户搜索热度。
对美食相关种子词发起搜索建议请求，从返回的联想词中提取具体食物名。
"""

import logging

import httpx

from app.crawler.base import BaseCrawler, FoodTrendItem
from app.crawler.food_keywords import FOOD_NAMES, get_category, match_food_in_text

logger = logging.getLogger(__name__)

SUGREC_URL = "https://www.baidu.com/sugrec"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# 种子关键词：用于触发美食相关的搜索建议
SEED_KEYWORDS = [
    "今天吃什么", "美食推荐", "好吃的", "网红美食", "必吃",
    "火锅", "奶茶", "烧烤", "小吃推荐", "甜品推荐",
    "外卖推荐", "家常菜", "夜宵", "早餐吃什么", "下午茶",
    "减脂餐", "快餐", "面馆", "饺子", "螺蛳粉",
]


class BaiduSuggestCrawler(BaseCrawler):
    """百度搜索建议爬虫。

    通过百度 sugrec 接口获取美食关键词的搜索联想词，
    从中提取具体食物名并根据出现频率计算热度分数。
    """

    def get_source_name(self) -> str:
        return "baidu_suggest"

    def crawl(self) -> list[FoodTrendItem]:
        self.unmatched_titles = []
        food_counts: dict[str, int] = {}

        for keyword in SEED_KEYWORDS:
            try:
                suggestions = self._get_suggestions(keyword)
                for text in suggestions:
                    self._extract_foods(text, food_counts)
                    if not match_food_in_text(text):
                        self.unmatched_titles.append(text)
            except Exception:
                logger.warning("百度建议请求失败: %s", keyword, exc_info=True)

        items = self._build_items(food_counts)
        logger.info("百度建议: 发现 %d 种食物", len(items))
        return items

    def _get_suggestions(self, keyword: str) -> list[str]:
        """获取单个关键词的搜索建议列表。"""
        resp = httpx.get(
            SUGREC_URL,
            params={
                "prod": "pc",
                "from": "pc_web",
                "wd": keyword,
                "json": "1",
                "ie": "utf-8",
            },
            headers=HEADERS,
            follow_redirects=True,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return [item["q"] for item in data.get("g", []) if "q" in item]

    @staticmethod
    def _extract_foods(text: str, counts: dict[str, int]) -> None:
        """从建议文本中匹配食物名并累加计数。"""
        for name in FOOD_NAMES:
            if name in text:
                counts[name] = counts.get(name, 0) + 1

    @staticmethod
    def _build_items(food_counts: dict[str, int]) -> list[FoodTrendItem]:
        """将食物出现频率转换为热度分数。"""
        if not food_counts:
            return []
        max_count = max(food_counts.values())
        items = []
        for name, count in food_counts.items():
            score = max(1, count * 100 // max_count)
            items.append(
                FoodTrendItem(
                    food_name=name,
                    heat_score=score,
                    post_count=count,
                    category=get_category(name),
                )
            )
        return sorted(items, key=lambda x: x.heat_score, reverse=True)
