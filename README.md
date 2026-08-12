# Barnes & Noble Book Scraper

Collects current book data from Barnes & Noble's public **Bestselling Books** page and refreshes the CSV and JSON exports on every run.

Collected fields: title, author, price, previous price and discount when available, rating, review count, format, ISBN-13, availability, bestseller rank, category, product and image URLs, and scrape timestamp.

> The site's page structure may change. This scraper does not attempt to bypass access controls, CAPTCHAs, or login mechanisms.

## Setup (Windows / VS Code)

Open PowerShell in the project directory.

If you already have a `venv` directory, use it:

```powershell
.\venv\Scripts\Activate.ps1
```

Otherwise, create and activate one:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Then install the packages:

```powershell
python -m pip install -r requirements.txt
```

If PowerShell's execution policy prevents activation, run without activating the environment:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe main.py
```

## Running the scraper

```powershell
python main.py
```

The scraper prints page progress in the terminal. When it completes, it creates or updates:

- `exports/books.csv` — written with a UTF-8 BOM for Excel compatibility.
- `exports/books.json` — a formatted, valid JSON array.

These files are **overwritten** on every run; no history or database is retained.

## Settings

The settings are grouped at the top of `scraper.py`:

```python
BASE_URL = "https://www.barnesandnoble.com"
START_URL = f"{BASE_URL}/collections/books/bestselling-books"
TIMEOUT = 30.0
REQUEST_DELAY = 0.7
MAX_PAGES = 5
```

- `MAX_PAGES = 1`: scrape only the first listing page.
- `MAX_PAGES = 5`: scrape at most the first five listing pages.
- `MAX_PAGES = None`: continue until no new page is available.
- `REQUEST_DELAY`: a respectful delay between requests; do not reduce it.

## Fields

- `title`: The book's product title.
- `author`: The primary author or contributor shown on the detail page.
- `current_price`: The current selling price for the selected ISBN/format (`float`).
- `original_price`: A higher previous list price actually shown in the main product price box; otherwise `null`.
- `discount_percent`: `(original_price - current_price) / original_price * 100` when a previous price is verified; otherwise `null`.
- `rating` and `review_count`: Schema.org aggregate rating data from the page, as `float` and `integer`; otherwise `null`.
- `format`: The selected ISBN's format, such as `Hardcover` or `Paperback`.
- `isbn`: The ISBN-13/EAN product identifier.
- `availability`: The product's online purchase status. Explicit visible stock text and live `Add To Cart` / `Pre-Order` actions take priority; JSON-LD is used as a fallback.
- `bestseller_rank`: A numeric sales rank only when it is reliably present in the listing response; otherwise `null`.
- `category`: Detail-page category links joined with ` | `.
- `product_url` and `image_url`: Canonical product and cover-image URLs.
- `scraped_at`: A UTC ISO-8601 timestamp generated once per scraper run.

## Known limitations

- Only public pages are read; CAPTCHAs, logins, accounts, and access controls are not bypassed.
- B&N can change prices, availability, ratings, and catalogue data at any time. Missing or unreliable values are not guessed and remain `null`.
- `original_price` is read only from the current product's main price box. Prices from recommendation cards are never used.
- `MAX_PAGES = None` stops when no new unique books remain. A numeric limit is still more predictable for normal use.

## If the site changes

Detail-page CSS selectors are centralized in the `SELECTORS` dictionary in `scraper.py`. Update those values first if title, author, price, format, availability, or category fields disappear.

On B&N's current platform, the listing page sends product data through a React Router data stream instead of normal product-card HTML. Listing identifiers are extracted by `RSC_PRODUCT_PATTERN` and `extract_listing_seeds()`. If no listing products are found, this is the most likely section to update.

## Dependencies

- `httpx`: Makes HTTP requests with connection reuse, timeout, redirect, and retry handling.
- `selectolax`: Parses JSON-LD and product-detail HTML quickly with CSS selectors.

Playwright is not used. Required product-detail data is already available in HTML returned by normal HTTP requests, so opening a browser is unnecessary.
