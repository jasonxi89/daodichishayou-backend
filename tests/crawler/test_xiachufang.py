import json
from unittest.mock import MagicMock, patch

import httpx
import pytest


SAMPLE_LIST_HTML = """
<html><body>
<div class="normal-recipe-list honor-recipe-list">
<ul class="list">
  <li class="pure-g">
    <div class="pure-u-11-12">
      <div class="recipe recipe-215-horizontal pure-g image-link display-block">
        <a href="/recipe/100001/" target="_blank">
          <div class="cover pure-u">
            <img data-src="https://img.example.com/1.jpg" width="215" height="136" />
          </div>
        </a>
        <div class="info pure-u">
          <p class="name">
            <a href="/recipe/100001/" target="_blank">番茄炒蛋</a>
          </p>
          <p class="ing ellipsis">
            <a href="/category/1/">番茄</a>、<a href="/category/2/">鸡蛋</a>
          </p>
          <p class="stats">
            综合评分&nbsp;<span class="score bold green-font">8.9</span>
            &nbsp;（<span class="bold score">12345</span>&nbsp;做过）
          </p>
          <p class="author">
            <a href="/cook/1001/" class="gray-font" target="_blank">厨师小明</a>
          </p>
        </div>
      </div>
    </div>
  </li>
  <li class="pure-g">
    <div class="pure-u-11-12">
      <div class="recipe recipe-215-horizontal pure-g image-link display-block">
        <a href="/recipe/100002/" target="_blank">
          <div class="cover pure-u">
            <img data-src="https://img.example.com/2.jpg" width="215" height="136" />
          </div>
        </a>
        <div class="info pure-u">
          <p class="name">
            <a href="/recipe/100002/" target="_blank">红烧肉</a>
          </p>
          <p class="ing ellipsis">
            <a href="/category/3/">五花肉</a>、<span>冰糖</span>
          </p>
          <p class="stats">
            综合评分&nbsp;<span class="score bold green-font">9.2</span>
            &nbsp;（<span class="bold score">8000</span>&nbsp;做过）
          </p>
          <p class="author">
            <a href="/cook/1002/" class="gray-font" target="_blank">大厨老王</a>
          </p>
        </div>
      </div>
    </div>
  </li>
</ul>
</div>
</body></html>
"""

CAPTCHA_HTML = """
<html><body>
<div>Aliyun CAPTCHA verification required</div>
</body></html>
"""

SAMPLE_DETAIL_HTML_WITH_LD = """
<html><head>
<script type="application/ld+json">
{
  "@type": "Recipe",
  "name": "番茄炒蛋",
  "image": "https://img.example.com/detail.jpg",
  "author": {"name": "厨师小明"},
  "recipeIngredient": ["番茄 2个", "鸡蛋 3个", "盐 适量"],
  "recipeInstructions": ["切番茄", "打蛋", "翻炒"],
  "aggregateRating": {"ratingValue": 8.9, "reviewCount": 500}
}
</script>
</head><body></body></html>
"""

SAMPLE_DETAIL_HTML_WITH_TABLE = """
<html><body>
<div class="ings">
  <table>
    <tr><td class="name">鸡胸肉</td><td class="unit">200g</td></tr>
    <tr><td class="name">花生</td><td class="unit">50g</td></tr>
  </table>
</div>
<div class="steps">
  <ol>
    <li><p class="text">切丁腌制</p></li>
    <li><p class="text">热油翻炒</p></li>
  </ol>
</div>
</body></html>
"""


def test_parse_list_page():
    from app.crawler.xiachufang import _parse_list_page

    items = _parse_list_page(SAMPLE_LIST_HTML, "honor")
    assert len(items) == 2

    first = items[0]
    assert first.name == "番茄炒蛋"
    assert first.source_url == "https://www.xiachufang.com/recipe/100001/"
    assert first.rating == 8.9
    assert first.made_count == 12345
    assert first.image_url == "https://img.example.com/1.jpg"
    assert first.author == "厨师小明"
    assert first.category == "honor"
    assert "番茄" in first.ingredients_text
    assert "鸡蛋" in first.ingredients_text

    second = items[1]
    assert second.name == "红烧肉"
    assert second.rating == 9.2
    assert second.made_count == 8000


def test_parse_list_page_empty():
    from app.crawler.xiachufang import _parse_list_page

    items = _parse_list_page("<html><body></body></html>", "honor")
    assert items == []


def test_is_captcha_page():
    from app.crawler.xiachufang import _is_captcha_page

    assert _is_captcha_page(CAPTCHA_HTML) is True
    assert _is_captcha_page(SAMPLE_LIST_HTML) is False
    assert _is_captcha_page("<html>normal page</html>") is False


def test_parse_detail_page_json_ld():
    from app.crawler.xiachufang import _parse_detail_page
    from app.crawler.recipe_base import RecipeItem

    item = RecipeItem(
        name="番茄炒蛋",
        source_url="https://example.com/recipe/1",
    )
    result = _parse_detail_page(SAMPLE_DETAIL_HTML_WITH_LD, item)
    assert result.ingredients is not None
    assert len(result.ingredients) == 3
    assert result.steps is not None
    assert len(result.steps) == 3
    assert result.rating == 8.9
    assert result.image_url == "https://img.example.com/detail.jpg"


def test_parse_detail_page_html_table():
    from app.crawler.xiachufang import _parse_detail_page
    from app.crawler.recipe_base import RecipeItem

    item = RecipeItem(
        name="宫保鸡丁",
        source_url="https://example.com/recipe/2",
    )
    result = _parse_detail_page(SAMPLE_DETAIL_HTML_WITH_TABLE, item)
    assert result.ingredients is not None
    assert len(result.ingredients) == 2
    assert result.ingredients[0]["name"] == "鸡胸肉"
    assert result.steps is not None
    assert len(result.steps) == 2


def test_parse_made_count():
    from app.crawler.xiachufang import _parse_made_count

    assert _parse_made_count("12345") == 12345
    assert _parse_made_count("12,345") == 12345
    assert _parse_made_count("  0  ") == 0
    assert _parse_made_count("no number") == 0


def test_parse_rating():
    from app.crawler.xiachufang import _parse_rating

    assert _parse_rating("9.1") == 9.1
    assert _parse_rating("  8.5  ") == 8.5
    assert _parse_rating("no rating") is None


def test_scraper_skips_existing_urls():
    from app.crawler.xiachufang import XiachufangScraper

    scraper = XiachufangScraper()

    mock_resp = MagicMock()
    mock_resp.text = SAMPLE_LIST_HTML
    mock_resp.raise_for_status = MagicMock()

    existing = {"https://www.xiachufang.com/recipe/100001/"}

    with patch.object(scraper._client, "get", return_value=mock_resp), \
         patch("app.crawler.xiachufang.time.sleep"):
        items = scraper.scrape(existing_urls=existing)

    # Only recipe 100002 should be returned since 100001 is existing
    names = [i.name for i in items]
    assert "番茄炒蛋" not in names
    assert "红烧肉" in names


def test_scraper_handles_captcha():
    from app.crawler.xiachufang import XiachufangScraper

    scraper = XiachufangScraper()

    mock_resp = MagicMock()
    mock_resp.text = CAPTCHA_HTML
    mock_resp.raise_for_status = MagicMock()

    with patch.object(scraper._client, "get", return_value=mock_resp), \
         patch("app.crawler.xiachufang.time.sleep"):
        items = scraper.scrape()

    assert items == []


def test_scraper_handles_network_error():
    from app.crawler.xiachufang import XiachufangScraper

    scraper = XiachufangScraper()

    with patch.object(
        scraper._client, "get",
        side_effect=httpx.ConnectError("Network error"),
    ), patch("app.crawler.xiachufang.time.sleep"):
        items = scraper.scrape()

    assert items == []


def test_scraper_get_source_name():
    from app.crawler.xiachufang import XiachufangScraper

    assert XiachufangScraper().get_source_name() == "xiachufang"


def test_parse_detail_page_no_structured_data():
    """Detail page with no JSON-LD and no ings/steps divs."""
    from app.crawler.xiachufang import _parse_detail_page
    from app.crawler.recipe_base import RecipeItem

    item = RecipeItem(name="空页面", source_url="https://example.com/r/5")
    result = _parse_detail_page("<html><body>nothing</body></html>", item)
    assert result.ingredients is None
    assert result.steps is None


def test_parse_detail_page_invalid_json_ld():
    """JSON-LD with invalid JSON should fall back to HTML parsing."""
    from app.crawler.xiachufang import _parse_detail_page
    from app.crawler.recipe_base import RecipeItem

    html = """<html><head>
    <script type="application/ld+json">{invalid json!!}</script>
    </head><body></body></html>"""
    item = RecipeItem(name="坏数据", source_url="https://example.com/r/6")
    result = _parse_detail_page(html, item)
    assert result.name == "坏数据"


def test_extract_ingredients_from_list_page():
    from app.crawler.xiachufang import _extract_ingredients_from_list_page
    from bs4 import BeautifulSoup

    html = '<p class="ing"><a>番茄</a>、<a>鸡蛋</a>、<span>盐</span></p>'
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("p")
    ingredients, text = _extract_ingredients_from_list_page(tag)
    assert len(ingredients) == 3
    assert "番茄" in text
    assert "盐" in text


def test_scraper_detail_captcha_stops_enrichment():
    """CAPTCHA on detail page stops enrichment but keeps list data."""
    from app.crawler.xiachufang import XiachufangScraper

    scraper = XiachufangScraper()

    list_resp = MagicMock()
    list_resp.text = SAMPLE_LIST_HTML
    list_resp.raise_for_status = MagicMock()

    captcha_resp = MagicMock()
    captcha_resp.text = CAPTCHA_HTML
    captcha_resp.raise_for_status = MagicMock()

    call_count = [0]
    def side_effect(url):
        call_count[0] += 1
        if "/explore/" in url:
            return list_resp
        return captcha_resp

    with patch.object(scraper._client, "get", side_effect=side_effect), \
         patch("app.crawler.xiachufang.time.sleep"):
        items = scraper.scrape()

    # Items from list pages are still returned
    assert len(items) > 0
