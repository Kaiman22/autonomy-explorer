#!/usr/bin/env python3
"""
Step 3: Fetch real estate prices from Homegate.ch.
Scrapes municipality price pages for CHF/m² data.
Outputs: data/processed/prices.json
"""
import json
import re
import time

import requests

from config import PROCESSED_DIR

# Homegate uses slugified municipality names in URLs
HOMEGATE_BASE = "https://www.homegate.ch/en/property-prices-m2/city-"

# Headers to mimic a browser request
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
}


def slugify(name):
    """Convert municipality name to URL slug."""
    slug = name.lower()
    # Replace special chars
    replacements = {
        "ä": "ae", "ö": "oe", "ü": "ue",
        "à": "a", "é": "e", "è": "e", "ê": "e",
        "ô": "o", "î": "i", "û": "u",
        " ": "-", "/": "-", "(": "", ")": "",
        "'": "", ".": "", ",": "",
    }
    for old, new in replacements.items():
        slug = slug.replace(old, new)
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def extract_initial_state(html):
    """Extract __INITIAL_STATE__ JSON from page HTML."""
    pattern = r"window\.__INITIAL_STATE__\s*=\s*({.*?});\s*</script>"
    match = re.search(pattern, html, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def extract_price_from_state(state):
    """Extract CHF/m² price data from the __INITIAL_STATE__ object."""
    if not state:
        return None

    # Navigate the state tree - structure may vary
    # Look for price data in various possible locations
    try:
        # Try common paths in the Homegate state
        pages = state.get("pages", {})
        price_page = pages.get("propertyPricesPerM2", {})
        if price_page:
            data = price_page.get("data", {})
            buy = data.get("buy", {})
            apartment = buy.get("apartment", {})
            median = apartment.get("median")
            if median is not None:
                return {
                    "chf_per_m2": median,
                    "min": apartment.get("min"),
                    "max": apartment.get("max"),
                    "type": "apartment_buy",
                }
            # Try house prices
            house = buy.get("house", {})
            median_h = house.get("median")
            if median_h is not None:
                return {
                    "chf_per_m2": median_h,
                    "min": house.get("min"),
                    "max": house.get("max"),
                    "type": "house_buy",
                }
    except (AttributeError, KeyError, TypeError):
        pass

    # Fallback: search for any price-like structure
    def find_prices(obj, depth=0):
        if depth > 8 or not isinstance(obj, dict):
            return None
        # Look for keys that suggest price data
        if "median" in obj and isinstance(obj.get("median"), (int, float)):
            return obj
        for v in obj.values():
            if isinstance(v, dict):
                result = find_prices(v, depth + 1)
                if result:
                    return result
        return None

    price_obj = find_prices(state)
    if price_obj and price_obj.get("median"):
        return {
            "chf_per_m2": price_obj["median"],
            "min": price_obj.get("min"),
            "max": price_obj.get("max"),
            "type": "unknown",
        }

    return None


def fetch_price_for_municipality(name):
    """Fetch CHF/m² for a single municipality from Homegate."""
    slug = slugify(name)
    url = f"{HOMEGATE_BASE}{slug}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        state = extract_initial_state(resp.text)
        return extract_price_from_state(state)
    except requests.RequestException:
        return None


def main():
    # Load municipalities
    with open(PROCESSED_DIR / "municipalities.json") as f:
        municipalities = json.load(f)

    print(f"Fetching prices for {len(municipalities)} municipalities...")

    prices = {}
    success = 0
    errors = 0

    for i, m in enumerate(municipalities):
        if i > 0 and i % 50 == 0:
            print(f"  Progress: {i}/{len(municipalities)} ({success} found, {errors} missing)")

        price = fetch_price_for_municipality(m["name"])
        if price:
            prices[m["id"]] = price
            success += 1
        else:
            errors += 1

        # Be polite - 0.5s between requests
        time.sleep(0.5)

    print(f"\nDone: {success} prices found, {errors} missing")

    out_path = PROCESSED_DIR / "prices.json"
    with open(out_path, "w") as f:
        json.dump(prices, f, indent=2)
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
