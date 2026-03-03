import json
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

from app.crawler.recipe_base import BaseRecipeScraper, RecipeItem

logger = logging.getLogger(__name__)

BASE_URL = "https://www.xiachufang.com"
REQUEST_DELAY = 10
REQUEST_TIMEOUT = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

LIST_PAGES = [
    {"path": "/explore/honor/?page={page}", "pages": 3, "category": "honor"},
    {"path": "/explore/monthhonor/?page={page}", "pages": 2, "category": "monthhonor"},
    {"path": "/explore/rising/?page={page}", "pages": 2, "category": "rising"},
]


def _is_captcha_page(html: str) -> bool:
    return "aliyun" in html.lower() and "captcha" in html.lower()


def _parse_made_count(text: str) -> int:
    text = text.strip().replace(",", "").replace(" ", "")
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else 0


def _parse_rating(text: str) -> float | None:
    match = re.search(r"([\d.]+)", text.strip())
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _extract_ingredients_from_list_page(ing_tag) -> tuple[list[dict], str]:
    """Extract ingredient names from list page's ing ellipsis section."""
    ingredients = []
    names = []
    for link in ing_tag.find_all("a"):
        name = link.get_text(strip=True)
        if name:
            ingredients.append({"name": name})
            names.append(name)
    for span in ing_tag.find_all("span"):
        name = span.get_text(strip=True)
        if name:
            ingredients.append({"name": name})
            names.append(name)
    return ingredients, " ".join(names)


def _parse_list_page(html: str, category: str) -> list[RecipeItem]:
    """Parse a single list page HTML and return recipe items."""
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for recipe_div in soup.select("div.recipe"):
        try:
            name_tag = recipe_div.select_one("p.name a")
            if not name_tag:
                continue

            name = name_tag.get_text(strip=True)
            href = name_tag.get("href", "")
            if not href or "/recipe/" not in href:
                continue
            source_url = BASE_URL + href if href.startswith("/") else href

            image_url = None
            img_tag = recipe_div.select_one("img[data-src]")
            if img_tag:
                image_url = img_tag.get("data-src")

            rating = None
            made_count = 0
            stats_tag = recipe_div.select_one("p.stats")
            if stats_tag:
                score_spans = stats_tag.select("span.score")
                if score_spans:
                    rating = _parse_rating(score_spans[0].get_text())
                if len(score_spans) > 1:
                    made_count = _parse_made_count(score_spans[1].get_text())

            author = None
            author_tag = recipe_div.select_one("p.author a")
            if author_tag:
                author = author_tag.get_text(strip=True)

            ingredients = None
            ingredients_text = None
            ing_tag = recipe_div.select_one("p.ing")
            if ing_tag:
                ingredients, ingredients_text = (
                    _extract_ingredients_from_list_page(ing_tag)
                )

            items.append(
                RecipeItem(
                    name=name,
                    source_url=source_url,
                    rating=rating,
                    made_count=made_count,
                    image_url=image_url,
                    author=author,
                    ingredients=ingredients,
                    ingredients_text=ingredients_text,
                    category=category,
                    list_source="xiachufang",
                )
            )
        except Exception as e:
            logger.warning("Failed to parse recipe card: %s", e)
            continue

    return items


def _parse_detail_page(html: str, item: RecipeItem) -> RecipeItem:
    """Enrich a RecipeItem with detail page data (ingredients + steps)."""
    soup = BeautifulSoup(html, "html.parser")

    # Try JSON-LD (Baidu cambrian format)
    ld_script = soup.find("script", type="application/ld+json")
    if ld_script:
        try:
            data = json.loads(ld_script.string)
            if data.get("@type") == "Recipe":
                if "recipeIngredient" in data:
                    ingredients = [
                        {"name": ing} for ing in data["recipeIngredient"]
                    ]
                    item.ingredients = ingredients
                    item.ingredients_text = " ".join(
                        data["recipeIngredient"]
                    )
                if "recipeInstructions" in data:
                    steps = data["recipeInstructions"]
                    if isinstance(steps, list):
                        item.steps = [
                            {"text": s} if isinstance(s, str) else s
                            for s in steps
                        ]
                if data.get("aggregateRating"):
                    rating_val = data["aggregateRating"].get("ratingValue")
                    if rating_val:
                        item.rating = float(rating_val)
                if data.get("image"):
                    item.image_url = data["image"]
                if data.get("author", {}).get("name"):
                    item.author = data["author"]["name"]
                return item
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: parse HTML structure
    # Ingredients: <div class="ings"> or similar
    ings_div = soup.find("div", class_="ings")
    if ings_div:
        ingredients = []
        names = []
        for row in ings_div.find_all("tr"):
            name_td = row.find("td", class_="name")
            unit_td = row.find("td", class_="unit")
            if name_td:
                ing_name = name_td.get_text(strip=True)
                ing_unit = unit_td.get_text(strip=True) if unit_td else ""
                ingredients.append({"name": ing_name, "amount": ing_unit})
                names.append(ing_name)
        if ingredients:
            item.ingredients = ingredients
            item.ingredients_text = " ".join(names)

    # Steps: <div class="steps"> or <ol class="steps">
    steps_container = soup.find("div", class_="steps") or soup.find(
        "ol", class_="steps"
    )
    if steps_container:
        steps = []
        for li in steps_container.find_all("li"):
            text_p = li.find("p", class_="text") or li.find("p")
            if text_p:
                steps.append({"text": text_p.get_text(strip=True)})
        if steps:
            item.steps = steps

    return item


class XiachufangScraper(BaseRecipeScraper):
    def __init__(self) -> None:
        self._client = httpx.Client(
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )

    def get_source_name(self) -> str:
        return "xiachufang"

    def scrape(self, existing_urls: set[str] | None = None) -> list[RecipeItem]:
        existing = existing_urls or set()
        all_items: list[RecipeItem] = []

        for list_config in LIST_PAGES:
            for page in range(1, list_config["pages"] + 1):
                url = BASE_URL + list_config["path"].format(page=page)
                try:
                    logger.info("Fetching list page: %s", url)
                    resp = self._client.get(url)
                    resp.raise_for_status()

                    if _is_captcha_page(resp.text):
                        logger.warning("CAPTCHA detected on %s, skipping", url)
                        break

                    items = _parse_list_page(
                        resp.text, list_config["category"]
                    )
                    logger.info(
                        "Parsed %d recipes from %s", len(items), url
                    )

                    for item in items:
                        if item.source_url in existing:
                            continue
                        existing.add(item.source_url)
                        all_items.append(item)

                except httpx.HTTPError as e:
                    logger.error("Failed to fetch %s: %s", url, e)
                    break

                time.sleep(REQUEST_DELAY)

        # Attempt detail page enrichment
        enriched_count = 0
        for item in all_items:
            try:
                logger.info("Fetching detail: %s", item.source_url)
                resp = self._client.get(item.source_url)
                resp.raise_for_status()

                if _is_captcha_page(resp.text):
                    logger.warning("CAPTCHA on detail page, stopping")
                    break

                _parse_detail_page(resp.text, item)
                enriched_count += 1
            except httpx.HTTPError as e:
                logger.warning("Detail fetch failed for %s: %s", item.name, e)
            time.sleep(REQUEST_DELAY)

        logger.info(
            "Scrape complete: %d recipes, %d enriched",
            len(all_items),
            enriched_count,
        )
        return all_items
