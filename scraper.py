"""Export current Barnes & Noble book data to CSV and JSON."""

from __future__ import annotations

import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from selectolax.parser import HTMLParser


# ---------------------------------------------------------------------------
# Easy-to-change settings
# ---------------------------------------------------------------------------
BASE_URL = "https://www.barnesandnoble.com"
START_URL = f"{BASE_URL}/collections/books/bestselling-books"
TIMEOUT = 30.0
REQUEST_DELAY = 0.7
MAX_PAGES: int | None = 5  # Set to None to scrape all available pages.
RETRIES = 3

EXPORT_DIR = Path("exports")
CSV_PATH = EXPORT_DIR / "books.csv"
JSON_PATH = EXPORT_DIR / "books.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

# Check this dictionary first if the site changes its CSS classes.
SELECTORS = {
    "json_ld": 'script[type="application/ld+json"]',
    "title": "h1.product__title",
    "author": ".product__contributor.desktop-only a",
    "price_box": ".product__prices",
    "format": ".product__prices > div:first-child",
    # JSON-LD can sometimes lag behind availability changes. The live purchase
    # action on the product page is a more accurate source of current status.
    "purchase_button": 'form[action="/cart"] button[type="submit"]',
    "canonical_url": 'link[rel="canonical"]',
    "categories": '[aria-label^="Category:"]',
}

FIELDNAMES = [
    "title",
    "author",
    "current_price",
    "original_price",
    "discount_percent",
    "rating",
    "review_count",
    "format",
    "isbn",
    "availability",
    "bestseller_rank",
    "category",
    "product_url",
    "image_url",
    "scraped_at",
]


# Barnes & Noble's current listing pages deliver product cards through a React
# Router data stream rather than conventional HTML. These patterns locate the
# beginning of product records in that public response.
RSC_PRODUCT_PATTERN = re.compile(
    r"(?:generated/shopify|projects/[^\"\\]+)/products/(?P<product_id>\d+)"
)
EAN_PATTERN = re.compile(r"(?<!\d)(97[89]\d{10})(?!\d)")
WORK_ID_PATTERN = re.compile(r"(?<!\d)(\d{10})(?!\d)")
SEO_KEYWORD_PATTERN = re.compile(r'\\"([^"\\]{1,180}/[^"\\]{1,180})\\"')
MONEY_PATTERN = re.compile(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)")


def clean_text(value: str | None) -> str | None:
    """Normalize whitespace and convert empty values to None."""
    if not value:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def first_text(tree: HTMLParser, selector: str) -> str | None:
    node = tree.css_first(selector)
    return clean_text(node.text()) if node else None


def to_float(value: Any) -> float | None:
    """Safely convert a price or numeric value to float."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    number = to_float(value)
    return int(number) if number is not None else None


def get_offer(node: dict[str, Any]) -> dict[str, Any]:
    offers = node.get("offers")
    if isinstance(offers, dict):
        return offers
    if isinstance(offers, list):
        return next((item for item in offers if isinstance(item, dict)), {})
    return {}


def schema_type_is(node: dict[str, Any], wanted: str) -> bool:
    node_type = node.get("@type")
    if isinstance(node_type, list):
        return wanted in node_type
    return node_type == wanted


def load_schema_nodes(tree: HTMLParser) -> list[dict[str, Any]]:
    """Flatten the page's JSON-LD blocks into a list of schema.org nodes."""
    nodes: list[dict[str, Any]] = []
    for script in tree.css(SELECTORS["json_ld"]):
        try:
            data = json.loads(script.text())
        except (json.JSONDecodeError, TypeError):
            continue

        if isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                nodes.extend(item for item in graph if isinstance(item, dict))
            else:
                nodes.append(data)
        elif isinstance(data, list):
            nodes.extend(item for item in data if isinstance(item, dict))
    return nodes


def normalise_availability(value: Any) -> str | None:
    if not value:
        return None
    name = str(value).rstrip("/").rsplit("/", 1)[-1]
    names = {
        "InStock": "In Stock",
        "OutOfStock": "Out of Stock",
        "PreOrder": "Pre-Order",
        "BackOrder": "Backorder",
        "Discontinued": "Discontinued",
    }
    return names.get(name, name.replace("_", " "))


def page_url(page_number: int) -> str:
    """Add Barnes & Noble's ?page=N parameter to START_URL."""
    parts = urlsplit(START_URL)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if page_number > 1:
        query["page"] = str(page_number)
    else:
        query.pop("page", None)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def value_after_marker(segment: str, marker: str, pattern: re.Pattern[str]) -> str | None:
    """Return the first matching value after a marker in an RSC product record."""
    marker_position = segment.find(marker)
    if marker_position < 0:
        return None
    nearby = segment[marker_position : marker_position + 700]
    match = pattern.search(nearby)
    return match.group(1) if match else None


def make_product_url(seo_keyword: str, work_id: str, ean: str) -> str:
    # The site's client code replaces "/" with "-" in the SEO keyword.
    slug = quote(seo_keyword.lower().replace("/", "-"), safe="-")
    return f"{BASE_URL}/w/{slug}/{work_id}?ean={ean}"


def extract_listing_seeds(html: str) -> list[dict[str, Any]]:
    """Extract the three identifiers needed to reach detail pages from listing RSC data.

    On B&N's current pages, product cards cannot be selected from HTTP HTML with
    CSS. The same public response contains ISBN, work ID, and SEO keyword data
    inside retailSearchResults.
    """
    results_position = html.rfind("retailSearchResults")
    if results_position < 0:
        return []

    result_data = html[results_position:]
    matches = list(RSC_PRODUCT_PATTERN.finditer(result_data))
    seeds: list[dict[str, Any]] = []
    seen_eans: set[str] = set()

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(result_data)
        segment = result_data[match.start() : end]

        # The first product record exposes field names directly. Later RSC
        # records can reuse field-name references, so fall back to unique ISBN,
        # work ID, and SEO values within the record when a marker is absent.
        ean = value_after_marker(segment, "mfield_bnb__ean", EAN_PATTERN)
        work_id = value_after_marker(segment, "mfield_bnb__workId", WORK_ID_PATTERN)
        seo_keyword = value_after_marker(segment, "mfield_bnb__seoKeywords", SEO_KEYWORD_PATTERN)
        ean = ean or (EAN_PATTERN.search(segment).group(1) if EAN_PATTERN.search(segment) else None)
        work_id = work_id or (
            WORK_ID_PATTERN.search(segment).group(1) if WORK_ID_PATTERN.search(segment) else None
        )
        seo_keyword = seo_keyword or (
            SEO_KEYWORD_PATTERN.search(segment).group(1)
            if SEO_KEYWORD_PATTERN.search(segment)
            else None
        )
        rank_value = value_after_marker(
            segment,
            "mfield_bnb__salesRank",
            re.compile(r"\[(\d{1,7})\]"),
        )

        if not ean or not work_id or not seo_keyword or ean in seen_eans:
            continue

        seen_eans.add(ean)
        seeds.append(
            {
                "isbn": ean,
                "work_id": work_id,
                "product_url": make_product_url(seo_keyword, work_id, ean),
                "bestseller_rank": to_int(rank_value),
            }
        )

    return seeds


def select_variant(product_group: dict[str, Any], isbn: str) -> dict[str, Any]:
    variants = product_group.get("hasVariant")
    if not isinstance(variants, list):
        return {}
    for variant in variants:
        if isinstance(variant, dict) and str(variant.get("sku")) == isbn:
            return variant
    return next((item for item in variants if isinstance(item, dict)), {})


def categories_from_page(tree: HTMLParser) -> str | None:
    categories: list[str] = []
    for node in tree.css(SELECTORS["categories"]):
        label = node.attributes.get("aria-label", "")
        category = clean_text(label.removeprefix("Category:").strip())
        if category and category not in categories:
            categories.append(category)
    return " | ".join(categories) if categories else None


def original_price_from_page(tree: HTMLParser, current_price: float | None) -> float | None:
    """Find the previous price shown in the main product price box, if present.

    Recommendation and similar-product cards are deliberately ignored because
    their prices can belong to a different product. JSON-LD does not provide a
    previous price, so the field remains None when it is not shown on the page.
    """
    price_box = tree.css_first(SELECTORS["price_box"])
    if not price_box or current_price is None:
        return None
    prices = [to_float(value) for value in MONEY_PATTERN.findall(price_box.text())]
    higher_prices = [price for price in prices if price is not None and price > current_price]
    return max(higher_prices) if higher_prices else None


def availability_from_page(tree: HTMLParser, schema_availability: Any) -> str | None:
    """Prefer visible stock text and purchase actions over JSON-LD availability."""
    body = tree.body
    page_text = clean_text(body.text()).casefold() if body else ""
    if "currently out of stock online" in page_text:
        return "Out of Stock"
    if "will be released" in page_text:
        return "Pre-Order"

    purchase_action = first_text(tree, SELECTORS["purchase_button"])
    action = purchase_action.casefold() if purchase_action else ""

    if "pre-order" in action or "preorder" in action:
        return "Pre-Order"
    if "notify me" in action or "email me" in action or "out of stock" in action:
        return "Out of Stock"
    if "add to cart" in action:
        return "In Stock"

    return normalise_availability(schema_availability)


def parse_product_page(
    html: str,
    seed: dict[str, Any],
    scraped_at: str,
) -> dict[str, Any] | None:
    tree = HTMLParser(html)
    schema_nodes = load_schema_nodes(tree)
    product_group = next(
        (node for node in schema_nodes if schema_type_is(node, "ProductGroup")),
        {},
    )
    variant = select_variant(product_group, seed["isbn"])
    offer = get_offer(variant)

    # Some products have a direct Book node instead of a ProductGroup.
    book = next(
        (
            node
            for node in schema_nodes
            if schema_type_is(node, "Book") or schema_type_is(node, "Product")
        ),
        {},
    )
    if not product_group and not book:
        return None

    book_offer = get_offer(book)
    current_price = to_float(offer.get("price")) or to_float(book_offer.get("price"))
    if current_price is None:
        current_price = to_float(first_text(tree, f'{SELECTORS["price_box"]} .product-price'))

    rating_data = book.get("aggregateRating")
    if not isinstance(rating_data, dict):
        rating_data = product_group.get("aggregateRating")
    if not isinstance(rating_data, dict):
        rating_data = {}

    canonical = tree.css_first(SELECTORS["canonical_url"])
    canonical_url = canonical.attributes.get("href") if canonical else None
    product_url = urljoin(
        BASE_URL,
        offer.get("url") or variant.get("url") or canonical_url or seed["product_url"],
    )

    image_url = variant.get("image") or product_group.get("image") or book.get("image")
    if isinstance(image_url, list):
        image_url = image_url[0] if image_url else None
    if isinstance(image_url, dict):
        image_url = image_url.get("url")

    product_format = variant.get("bookFormat")
    if isinstance(product_format, str):
        product_format = product_format.rstrip("/").rsplit("/", 1)[-1]

    title = clean_text(product_group.get("name") or book.get("name")) or first_text(
        tree, SELECTORS["title"]
    )
    author = first_text(tree, SELECTORS["author"])
    availability = availability_from_page(
        tree,
        offer.get("availability") or book_offer.get("availability"),
    )

    original_price = original_price_from_page(tree, current_price)
    discount_percent = None
    if original_price and current_price is not None and original_price > current_price:
        discount_percent = round((original_price - current_price) / original_price * 100, 2)

    return {
        "title": title,
        "author": author,
        "current_price": current_price,
        "original_price": original_price,
        "discount_percent": discount_percent,
        "rating": to_float(rating_data.get("ratingValue")),
        "review_count": to_int(rating_data.get("reviewCount")),
        "format": clean_text(product_format) or first_text(tree, SELECTORS["format"]),
        "isbn": seed["isbn"],
        "availability": availability,
        "bestseller_rank": seed.get("bestseller_rank"),
        "category": categories_from_page(tree),
        "product_url": product_url,
        "image_url": image_url,
        "scraped_at": scraped_at,
    }


class BarnesNobleScraper:
    def __init__(self) -> None:
        self.client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(TIMEOUT),
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=3),
        )

    def close(self) -> None:
        self.client.close()

    def fetch_html(self, url: str) -> tuple[str, str] | None:
        """Report HTTP errors and retry temporary failures."""
        for attempt in range(1, RETRIES + 1):
            try:
                response = self.client.get(url)
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        f"Temporary HTTP error: {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.text, str(response.url)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as error:
                if attempt == RETRIES:
                    print(f"  Request failed after {RETRIES} attempts: {url}\n  {error}")
                    return None
                wait_seconds = attempt * 2
                print(f"  Request issue ({attempt}/{RETRIES}), retrying in {wait_seconds}s: {error}")
                time.sleep(wait_seconds)
        return None

    def scrape(self) -> list[dict[str, Any]]:
        books: list[dict[str, Any]] = []
        seen_products: set[str] = set()
        seen_isbns: set[str] = set()
        page_number = 1
        scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        while MAX_PAGES is None or page_number <= MAX_PAGES:
            listing_url = page_url(page_number)
            fetched_listing = self.fetch_html(listing_url)
            if not fetched_listing:
                break

            html, final_url = fetched_listing
            if "retailSearchResults" not in html:
                print(f"Page {page_number} skipped: product data was not found ({final_url})")
                break

            seeds = extract_listing_seeds(html)
            if not seeds:
                print(f"Page {page_number} skipped: no complete product identifiers were found.")
                break

            page_books = 0
            for seed in seeds:
                if seed["product_url"] in seen_products or seed["isbn"] in seen_isbns:
                    continue
                seen_products.add(seed["product_url"])
                seen_isbns.add(seed["isbn"])

                time.sleep(REQUEST_DELAY)
                fetched_product = self.fetch_html(seed["product_url"])
                if not fetched_product:
                    continue

                product_html, _ = fetched_product
                book = parse_product_page(product_html, seed, scraped_at)
                if not book or not book.get("title"):
                    print(f"  Product skipped: structured data was not found ({seed['product_url']})")
                    continue

                books.append(book)
                page_books += 1

            print(f"Page {page_number} scraped: {page_books} books")
            if page_books == 0:
                # Prevent an infinite loop when MAX_PAGES=None reaches the end.
                break

            page_number += 1
            time.sleep(REQUEST_DELAY)

        return books


def write_exports(books: list[dict[str, Any]]) -> None:
    EXPORT_DIR.mkdir(exist_ok=True)

    with JSON_PATH.open("w", encoding="utf-8") as json_file:
        json.dump(books, json_file, ensure_ascii=False, indent=2)

    # utf-8-sig helps Excel on Windows open Unicode and special characters correctly.
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(books)


def main() -> None:
    print("Starting scraper...")
    scraper = BarnesNobleScraper()
    try:
        books = scraper.scrape()
    finally:
        scraper.close()

    write_exports(books)
    print("\nScrape completed.")
    print(f"Books collected: {len(books)}")
    print(f"CSV: {CSV_PATH}")
    print(f"JSON: {JSON_PATH}")


if __name__ == "__main__":
    main()
