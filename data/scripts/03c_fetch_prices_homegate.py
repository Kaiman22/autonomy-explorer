#!/usr/bin/env python3
"""
Fetch real estate prices from Homegate.ch by scraping search result pages.

Strategy:
  - For each municipality, navigate to the Homegate buy search page
  - Extract listing data from Vue __INITIAL_STATE__ (price + living space)
  - Compute median CHF/m² from listings with both price and living space
  - Paginate to get up to ~60 listings per municipality (3 pages)
  - Output: data/processed/prices_homegate.json

Resumable: saves progress every 25 municipalities.
"""
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

from config import PROCESSED_DIR

DELAY_MIN = 2.0
DELAY_MAX = 4.5
SAVE_EVERY = 25
MAX_PAGES_PER_MUNI = 2  # max result pages to fetch per municipality (20 listings each)


def normalize_for_url(name):
    """Convert municipality name to Homegate URL slug."""
    n = name.strip()
    # Homegate uses the original name with special chars, but URL-encoded
    # e.g. "Zürich" -> "zuerich", "St. Gallen" -> "st-gallen"
    n = n.lower()
    n = n.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    n = n.replace("à", "a").replace("â", "a").replace("é", "e").replace("è", "e")
    n = n.replace("ê", "e").replace("ë", "e").replace("ô", "o").replace("î", "i")
    n = n.replace("ï", "i").replace("û", "u").replace("ù", "u").replace("ç", "c")
    n = re.sub(r"\s*\(.*?\)", "", n)
    n = n.replace(" ", "-").replace("/", "-").replace(".", "-").replace("'", "-")
    n = re.sub(r"-+", "-", n).strip("-")
    n = re.sub(r"[^a-z0-9-]", "", n)
    return n


def extract_listings(page):
    """Extract listing data from Homegate Vue __INITIAL_STATE__."""
    data = page.evaluate("""() => {
        const listings = window.__INITIAL_STATE__?.resultList?.search?.fullSearch?.result?.listings || [];
        const resultCount = window.__INITIAL_STATE__?.resultList?.search?.fullSearch?.result?.resultCount || 0;
        const pageCount = window.__INITIAL_STATE__?.resultList?.search?.fullSearch?.result?.pageCount || 0;
        return {
            resultCount: parseInt(resultCount) || 0,
            pageCount: parseInt(pageCount) || 0,
            listings: listings.map(item => {
                const l = item.listing || {};
                const chars = l.characteristics || {};
                const prices = l.prices || {};
                const buyPrice = prices.buy ? prices.buy.price : null;
                return {
                    livingSpace: chars.livingSpace || null,
                    buyPrice: buyPrice,
                    categories: l.categories || [],
                    locality: (l.address || {}).locality || null,
                };
            })
        };
    }""")
    return data


def compute_chf_per_m2(listings):
    """Compute median CHF/m² from listings that have both price and living space."""
    prices_per_m2 = []
    for l in listings:
        price = l.get("buyPrice")
        space = l.get("livingSpace")
        if price and space and space > 10 and price > 50000:
            chf_m2 = price / space
            # Sanity check: 1,000 - 50,000 CHF/m²
            if 1000 <= chf_m2 <= 50000:
                prices_per_m2.append(chf_m2)

    if len(prices_per_m2) < 2:
        return None, 0

    return int(statistics.median(prices_per_m2)), len(prices_per_m2)


def main():
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth

    stealth = Stealth(
        navigator_languages_override=("de-CH", "de"),
        navigator_platform_override="MacIntel",
    )

    # Load municipalities
    with open(PROCESSED_DIR / "municipalities.json") as f:
        municipalities = json.load(f)

    # Load existing results for resume
    results_path = PROCESSED_DIR / "prices_homegate.json"
    existing = {}
    if results_path.exists():
        with open(results_path) as f:
            existing = json.load(f)
        print(f"Resuming: {len(existing)} already fetched")

    # Build work list
    todo = []
    for m in municipalities:
        if m["id"] not in existing:
            todo.append(m)

    random.shuffle(todo)
    print(f"Municipalities: {len(municipalities)} total, {len(todo)} remaining")
    sys.stdout.flush()

    if not todo:
        print("All done!")
        return

    profile_dir = Path("/tmp/homegate_chrome_profile")
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            channel="chrome",
            locale="de-CH",
            viewport={"width": 1366, "height": 768},
        )
        page = context.pages[0] if context.pages else context.new_page()
        stealth.apply_stealth_sync(page)

        # Warm up + dismiss cookies
        print("Warming up...")
        sys.stdout.flush()
        try:
            page.goto("https://www.homegate.ch",
                       timeout=30000, wait_until="domcontentloaded")
            time.sleep(3)
            for btn in page.query_selector_all("button"):
                if "akzeptieren" in btn.text_content().strip().lower():
                    btn.click()
                    time.sleep(2)
                    break
        except Exception:
            pass

        success = 0
        no_listings = 0
        errors = 0
        consecutive_errors = 0

        for i, m in enumerate(todo):
            slug = normalize_for_url(m["name"])
            all_listings = []

            try:
                # First page
                url = f"https://www.homegate.ch/kaufen/immobilien/ort-{slug}/trefferliste?ep=1"
                resp = page.goto(url, timeout=20000, wait_until="domcontentloaded")
                status = resp.status if resp else 0

                if status != 200:
                    errors += 1
                    consecutive_errors += 1
                    existing[m["id"]] = {"chf_per_m2": None, "n_listings": 0, "type": "homegate", "error": f"status_{status}"}
                else:
                    time.sleep(random.uniform(2, 4))

                    data = extract_listings(page)
                    all_listings.extend(data.get("listings", []))
                    total_pages = data.get("pageCount", 0)
                    result_count = data.get("resultCount", 0)

                    # Fetch additional pages if available
                    if total_pages > 1 and MAX_PAGES_PER_MUNI > 1:
                        for pg in range(2, min(total_pages + 1, MAX_PAGES_PER_MUNI + 1)):
                            time.sleep(random.uniform(1.5, 3))
                            url_pg = f"https://www.homegate.ch/kaufen/immobilien/ort-{slug}/trefferliste?ep={pg}"
                            try:
                                resp2 = page.goto(url_pg, timeout=20000, wait_until="domcontentloaded")
                                if resp2 and resp2.status == 200:
                                    time.sleep(random.uniform(2, 3))
                                    data2 = extract_listings(page)
                                    all_listings.extend(data2.get("listings", []))
                            except Exception:
                                break

                    # Compute CHF/m²
                    median_price, n_valid = compute_chf_per_m2(all_listings)

                    if median_price:
                        existing[m["id"]] = {
                            "chf_per_m2": median_price,
                            "n_listings": len(all_listings),
                            "n_valid": n_valid,
                            "result_count": result_count,
                            "type": "homegate",
                        }
                        success += 1
                        consecutive_errors = 0
                    else:
                        existing[m["id"]] = {
                            "chf_per_m2": None,
                            "n_listings": len(all_listings),
                            "n_valid": n_valid if n_valid else 0,
                            "result_count": result_count,
                            "type": "homegate",
                        }
                        no_listings += 1
                        consecutive_errors = 0

            except Exception as e:
                errors += 1
                consecutive_errors += 1
                existing[m["id"]] = {"chf_per_m2": None, "n_listings": 0, "type": "homegate", "error": str(e)[:100]}

            # Long pause after consecutive errors
            if consecutive_errors >= 10:
                print(f"  10 consecutive errors — pausing 60s...")
                sys.stdout.flush()
                time.sleep(60)
                consecutive_errors = 0
                try:
                    page.close()
                    page = context.new_page()
                    stealth.apply_stealth_sync(page)
                    page.goto("https://www.homegate.ch",
                               timeout=30000, wait_until="domcontentloaded")
                    time.sleep(3)
                except Exception:
                    pass

            done = i + 1
            if done % SAVE_EVERY == 0 or done == len(todo):
                with_price = sum(1 for v in existing.values() if v.get("chf_per_m2"))
                print(f"  {done}/{len(todo)}: {success} ok, {no_listings} empty, {errors} err "
                      f"| {with_price} have prices")
                sys.stdout.flush()
                with open(results_path, "w") as f:
                    json.dump(existing, f, indent=2, ensure_ascii=False)

            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        context.close()

    # Final summary
    with_price = {k: v for k, v in existing.items() if v.get("chf_per_m2")}
    print(f"\nDone: {success} ok, {no_listings} empty, {errors} errors")
    print(f"Municipalities with price: {len(with_price)}/{len(municipalities)}")
    if with_price:
        vals = [v["chf_per_m2"] for v in with_price.values()]
        print(f"CHF/m²: min={min(vals)}, max={max(vals)}, median={statistics.median(vals):.0f}")


if __name__ == "__main__":
    main()
