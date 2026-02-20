#!/usr/bin/env python3
"""
Retry Neho.ch scraping for municipalities that are still missing prices.

Only fetches pages for the ~900 municipalities we don't yet have.
Uses longer delays and periodic session refreshes to avoid Cloudflare.
"""
import json
import random
import re
import sys
import time
from pathlib import Path

from config import PROCESSED_DIR

DELAY_MIN = 1.5
DELAY_MAX = 3.0
SAVE_EVERY = 25
SESSION_REFRESH_EVERY = 75  # new browser context every N pages


def parse_chf(text):
    if not text:
        return None
    cleaned = text.replace("CHF", "").strip()
    cleaned = cleaned.replace("'", "").replace("\u2019", "").replace(" ", "")
    match = re.search(r"(\d+)", cleaned)
    return int(match.group(1)) if match else None


def extract_prices_from_page(page):
    def safe_text(selector):
        el = page.query_selector(selector)
        return el.text_content().strip() if el else None

    avg_apt = parse_chf(safe_text(".js-priceAverageApartments"))
    avg_house = parse_chf(safe_text(".js-priceAverageHouses"))
    info = safe_text(".js-pageSearchInfo") or ""
    m = re.search(r"CHF\s+([\d'\u2019]+)", info)
    avg_overall = parse_chf(m.group(1)) if m else None

    range_apt = safe_text(".js-priceRangeApartments") or ""
    range_house = safe_text(".js-priceRangeHouses") or ""

    def parse_range(text):
        nums = re.findall(r"[\d'\u2019]+", text)
        parsed = [int(n.replace("'", "").replace("\u2019", "")) for n in nums if n.replace("'", "").replace("\u2019", "").isdigit()]
        return (parsed[0], parsed[1]) if len(parsed) >= 2 else (None, None)

    min_apt, max_apt = parse_range(range_apt)
    min_house, max_house = parse_range(range_house)

    primary = avg_apt or avg_house or avg_overall
    if primary is None:
        return None

    return {
        "chf_per_m2": primary,
        "chf_per_m2_apartment": avg_apt,
        "chf_per_m2_house": avg_house,
        "chf_per_m2_overall": avg_overall,
        "min_apartment": min_apt,
        "max_apartment": max_apt,
        "min_house": min_house,
        "max_house": max_house,
        "type": "neho",
    }


def normalize(name):
    n = name.lower().strip()
    n = n.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    n = n.replace("à", "a").replace("â", "a").replace("é", "e").replace("è", "e")
    n = n.replace("ê", "e").replace("ë", "e").replace("ô", "o").replace("î", "i")
    n = n.replace("ï", "i").replace("û", "u").replace("ù", "u").replace("ç", "c")
    n = re.sub(r"\s*\(.*?\)", "", n)
    n = n.replace(" ", "-").replace("/", "-").replace(".", "-").replace("'", "-")
    n = re.sub(r"-+", "-", n).strip("-")
    n = re.sub(r"[^a-z0-9-]", "", n)
    return n


def create_context(browser):
    """Create a fresh browser context with randomised fingerprint."""
    ua_variants = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    ]
    ctx = browser.new_context(
        user_agent=random.choice(ua_variants),
        locale="de-CH",
        viewport={"width": random.choice([1280, 1366, 1440, 1920]),
                  "height": random.choice([720, 768, 900, 1080])},
    )
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return ctx


def main():
    from playwright.sync_api import sync_playwright

    # Load data
    with open(PROCESSED_DIR / "municipalities.json") as f:
        municipalities = json.load(f)
    with open(PROCESSED_DIR / "prices.json") as f:
        current_prices = json.load(f)
    with open(PROCESSED_DIR / "prices_neho.json") as f:
        neho_raw = json.load(f)

    all_munis = {m["id"]: m for m in municipalities}
    have_price = set(current_prices.keys())
    missing_ids = set(all_munis.keys()) - have_price

    # Build slug→municipality mapping for missing ones
    slug_to_mids = {}
    for mid in missing_ids:
        m = all_munis[mid]
        slug = normalize(m["name"])
        slug_to_mids.setdefault(slug, []).append(mid)

    # Build URL list (shuffle to reduce pattern detection)
    urls = [(slug, f"https://neho.ch/de/quadratmeterpreis-{slug}")
            for slug in slug_to_mids.keys()]
    random.shuffle(urls)

    print(f"Missing prices: {len(missing_ids)} municipalities ({len(urls)} unique slugs)")
    sys.stdout.flush()

    if not urls:
        print("Nothing to fetch!")
        return

    with sync_playwright() as p:
        # Use system Chrome in headed mode — headless Chromium gets blocked by CF
        browser = p.chromium.launch(
            headless=False,
            channel="chrome",
        )

        ctx = create_context(browser)
        page = ctx.new_page()

        # Warm up with main page
        print("Warming up...")
        sys.stdout.flush()
        try:
            page.goto("https://neho.ch/de/immobilienpreise-schweiz",
                       timeout=30000, wait_until="domcontentloaded")
            time.sleep(3)
        except Exception:
            pass

        success = 0
        errors = 0
        not_found = 0
        consecutive_errors = 0

        for i, (slug, url) in enumerate(urls):
            # Periodic session refresh
            if i > 0 and i % SESSION_REFRESH_EVERY == 0:
                print(f"  Refreshing browser session...")
                sys.stdout.flush()
                try:
                    page.close()
                    ctx.close()
                except Exception:
                    pass
                ctx = create_context(browser)
                page = ctx.new_page()
                try:
                    page.goto("https://neho.ch/de/immobilienpreise-schweiz",
                               timeout=30000, wait_until="domcontentloaded")
                    time.sleep(3)
                except Exception:
                    pass

            try:
                resp = page.goto(url, timeout=20000, wait_until="domcontentloaded")
                status = resp.status if resp else 0

                if status == 404:
                    not_found += 1
                    consecutive_errors = 0
                elif status == 403:
                    errors += 1
                    consecutive_errors += 1
                elif status == 200:
                    title = page.title()
                    if "just a moment" in title.lower() or "cloudflare" in title.lower():
                        time.sleep(8)
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=10000)
                        except Exception:
                            pass

                    # Wait for JS to populate price elements
                    time.sleep(3)

                    price_data = extract_prices_from_page(page)
                    if price_data:
                        for mid in slug_to_mids.get(slug, []):
                            neho_raw[mid] = price_data
                            current_prices[mid] = price_data
                        success += 1
                        consecutive_errors = 0
                    else:
                        errors += 1
                        consecutive_errors += 1
                else:
                    errors += 1
                    consecutive_errors += 1

            except Exception:
                errors += 1
                consecutive_errors += 1

            # If too many consecutive errors, long pause + session refresh
            if consecutive_errors >= 15:
                print(f"  15 consecutive errors — pausing 60s + refreshing session...")
                sys.stdout.flush()
                time.sleep(60)
                consecutive_errors = 0
                try:
                    page.close()
                    ctx.close()
                except Exception:
                    pass
                ctx = create_context(browser)
                page = ctx.new_page()
                try:
                    page.goto("https://neho.ch/de/immobilienpreise-schweiz",
                               timeout=30000, wait_until="domcontentloaded")
                    time.sleep(5)
                except Exception:
                    pass

            done = i + 1
            if done % SAVE_EVERY == 0 or done == len(urls):
                real = {k: v for k, v in neho_raw.items() if not k.startswith("_slug_")}
                print(f"  Progress: {done}/{len(urls)} "
                      f"({success} ok, {not_found} 404, {errors} err) "
                      f"| total prices: {len(real)}")
                sys.stdout.flush()
                with open(PROCESSED_DIR / "prices_neho.json", "w") as f:
                    json.dump(neho_raw, f, indent=2, ensure_ascii=False)
                with open(PROCESSED_DIR / "prices.json", "w") as f:
                    json.dump({k: v for k, v in neho_raw.items() if not k.startswith("_slug_")},
                              f, indent=2, ensure_ascii=False)

            # Randomised delay
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        browser.close()

    # Final summary
    real = {k: v for k, v in neho_raw.items() if not k.startswith("_slug_")}
    print(f"\nDone: {success} new, {not_found} 404, {errors} errors")
    print(f"Total BFS-matched prices: {len(real)}/{len(municipalities)}")
    vals = [v["chf_per_m2"] for v in real.values() if v.get("chf_per_m2")]
    if vals:
        print(f"CHF/m²: min={min(vals)}, max={max(vals)}, median={sorted(vals)[len(vals)//2]}")


if __name__ == "__main__":
    main()
