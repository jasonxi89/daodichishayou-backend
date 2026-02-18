import logging
import re

import httpx

from app.crawler.base import BaseCrawler, FoodTrendItem

logger = logging.getLogger(__name__)

SEARCH_KEYWORDS = [
    "今天吃什么",
    "美食推荐",
    "必吃榜",
    "宝藏餐厅",
    "网红美食",
    "家常菜推荐",
    "减脂餐",
    "甜品推荐",
]


class XiaohongshuCrawler(BaseCrawler):
    """小红书美食热度爬虫。

    通过搜索美食关键词获取热门食物。
    注意：小红书有严格反爬，实际使用中可能需要 cookie / 签名。
    当前实现为占位框架，crawl() 返回空列表并记录警告。
    """

    BASE_URL = "https://www.xiaohongshu.com"

    def get_source_name(self) -> str:
        return "xiaohongshu"

    def crawl(self) -> list[FoodTrendItem]:
        items: list[FoodTrendItem] = []
        for keyword in SEARCH_KEYWORDS:
            try:
                page_items = self._search(keyword)
                items.extend(page_items)
            except Exception:
                logger.warning("小红书搜索失败: keyword=%s", keyword, exc_info=True)
        return self._deduplicate(items)

    def _search(self, keyword: str) -> list[FoodTrendItem]:
        """搜索单个关键词，解析结果。

        小红书搜索接口需要签名，当前实现返回空列表。
        后续可接入第三方 API 或 playwright 实现。
        """
        logger.info("小红书搜索(stub): %s", keyword)
        # TODO: 实现实际爬取逻辑
        # 需要处理反爬（X-s签名、cookie等）
        return []

    @staticmethod
    def _deduplicate(items: list[FoodTrendItem]) -> list[FoodTrendItem]:
        seen: dict[str, FoodTrendItem] = {}
        for item in items:
            name = re.sub(r"\s+", "", item.food_name)
            if name in seen:
                seen[name].post_count += item.post_count
                seen[name].heat_score = max(seen[name].heat_score, item.heat_score)
            else:
                seen[name] = item
        return list(seen.values())
