#!/usr/bin/env python3
"""
Step 3b: Fetch real estate prices from Neho.ch using Playwright.

Neho publishes average CHF/m² prices for apartments and houses for
all ~2,200 Swiss municipalities, updated monthly. Uses Cloudflare,
so we need a real browser.

Strategy:
  1. Fetch sitemap via Playwright to get all municipality URLs (authoritative)
  2. Fetch each page, extract prices from DOM (.js-* CSS selectors)
  3. Match slugs to our municipality BFS IDs
  4. Output: data/processed/prices_neho.json → copied to prices.json

Resumable: saves progress every 25 pages. Re-run to continue.
"""
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from config import PROCESSED_DIR

SITEMAP_URL = "https://neho.ch/sitemap/seo/price"
DELAY = 1.0  # seconds between pages — be polite to avoid Cloudflare blocks
SAVE_EVERY = 25
MAX_RETRIES = 2


def parse_chf(text):
    """Parse "CHF 19'265" → 19265."""
    if not text:
        return None
    cleaned = text.replace("CHF", "").strip()
    cleaned = cleaned.replace("'", "").replace("\u2019", "").replace(" ", "")
    match = re.search(r"(\d+)", cleaned)
    return int(match.group(1)) if match else None


def extract_prices_from_page(page):
    """Extract price data from a loaded Neho page."""
    def safe_text(selector):
        el = page.query_selector(selector)
        return el.text_content().strip() if el else None

    avg_apt = parse_chf(safe_text(".js-priceAverageApartments"))
    avg_house = parse_chf(safe_text(".js-priceAverageHouses"))

    # Overall average from info paragraph
    info = safe_text(".js-pageSearchInfo") or ""
    m = re.search(r"CHF\s+([\d'\u2019]+)", info)
    avg_overall = parse_chf(m.group(1)) if m else None

    # Range data
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


def slug_from_url(url):
    """Extract slug from Neho URL: .../quadratmeterpreis-zuerich → zuerich"""
    return url.rsplit("/", 1)[-1].replace("quadratmeterpreis-", "")


def normalize_for_match(name):
    """Normalize a Swiss municipality name for matching to Neho slugs."""
    n = name.lower().strip()
    n = n.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    n = n.replace("à", "a").replace("â", "a").replace("é", "e").replace("è", "e")
    n = n.replace("ê", "e").replace("ë", "e").replace("ô", "o").replace("î", "i")
    n = n.replace("ï", "i").replace("û", "u").replace("ù", "u").replace("ç", "c")
    n = re.sub(r"\s*\(.*?\)", "", n)  # remove (BE), (ZH) etc
    n = n.replace(" ", "-").replace("/", "-").replace(".", "-").replace("'", "-")
    n = re.sub(r"-+", "-", n).strip("-")
    n = re.sub(r"[^a-z0-9-]", "", n)
    return n


def main():
    from playwright.sync_api import sync_playwright

    # Load our municipalities
    with open(PROCESSED_DIR / "municipalities.json") as f:
        municipalities = json.load(f)
    print(f"Loaded {len(municipalities)} municipalities")

    # Load existing prices for resume
    prices_path = PROCESSED_DIR / "prices_neho.json"
    existing = {}
    if prices_path.exists():
        with open(prices_path) as f:
            existing = json.load(f)
        print(f"Resuming: {len(existing)} already fetched")

    # Build matching: normalized_name → [municipalities]
    name_to_munis = {}
    for m in municipalities:
        key = normalize_for_match(m["name"])
        name_to_munis.setdefault(key, []).append(m)

    sys.stdout.flush()

    with sync_playwright() as p:
        # Launch with headed=False but with full browser args to pass Cloudflare
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="de-CH",
            viewport={"width": 1280, "height": 720},
        )
        # Remove webdriver flag
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        # Step 1: Get URLs from sitemap
        print("Fetching sitemap via browser...")
        sys.stdout.flush()
        try:
            page.goto(SITEMAP_URL, timeout=30000)
            sitemap_text = page.content()
            # The browser renders XML as HTML — extract the raw text
            sitemap_text = page.evaluate("document.querySelector('body')?.innerText || document.documentElement.outerHTML")
        except Exception as e:
            print(f"Sitemap fetch failed: {e}")
            print("Falling back to constructing URLs from municipality names...")
            sitemap_text = None

        urls = []
        if sitemap_text and "<loc>" in sitemap_text:
            # Parse as XML if we got raw sitemap
            try:
                root = ET.fromstring(sitemap_text)
                ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                for loc in root.findall(".//sm:url/sm:loc", ns):
                    urls.append(loc.text.strip())
            except ET.ParseError:
                pass

        if not urls:
            # Try extracting URLs from rendered page text
            found = re.findall(r"https://neho\.ch/de/quadratmeterpreis-[a-z0-9-]+", sitemap_text or "")
            urls = list(set(found))

        if urls:
            # Filter to municipality-level German pages
            urls = [u for u in urls
                    if "/de/quadratmeterpreis-" in u
                    and "-kanton" not in u
                    and not re.search(r"-\d{4}-\d+$", u)]
            print(f"  Got {len(urls)} municipality URLs from sitemap")
        else:
            # Fallback: construct from municipality names
            print("  Constructing URLs from municipality names...")
            seen_slugs = set()
            for m in municipalities:
                slug = normalize_for_match(m["name"])
                if slug not in seen_slugs:
                    urls.append(f"https://neho.ch/de/quadratmeterpreis-{slug}")
                    seen_slugs.add(slug)
            print(f"  Generated {len(urls)} URLs")

        # Filter out already-fetched URLs
        already_done = set()
        for slug, munis in name_to_munis.items():
            if any(m["id"] in existing for m in munis):
                already_done.add(slug)

        remaining_urls = []
        for url in urls:
            slug = slug_from_url(url)
            if slug not in already_done:
                remaining_urls.append(url)

        print(f"  {len(remaining_urls)} remaining to fetch")
        sys.stdout.flush()

        if not remaining_urls:
            print("All done!")
            browser.close()
            return

        # Step 2: Fetch each page
        # First, visit the homepage to get cookies/CF clearance
        print("Warming up browser session...")
        try:
            page.goto("https://neho.ch/de/immobilienpreise-schweiz", timeout=20000, wait_until="domcontentloaded")
            time.sleep(2)
        except Exception:
            pass

        success = 0
        errors = 0
        not_found = 0
        blocked = 0
        consecutive_errors = 0

        for i, url in enumerate(remaining_urls):
            slug = slug_from_url(url)
            fetched = False

            for attempt in range(MAX_RETRIES + 1):
                try:
                    resp = page.goto(url, timeout=20000, wait_until="domcontentloaded")
                    status = resp.status if resp else 0

                    if status == 404:
                        not_found += 1
                        consecutive_errors = 0
                        fetched = True
                        break
                    elif status == 403:
                        blocked += 1
                        if attempt < MAX_RETRIES:
                            time.sleep(3 * (attempt + 1))
                            continue
                        fetched = True
                        break
                    elif status == 200:
                        # Check if we got a Cloudflare challenge page
                        title = page.title()
                        if "just a moment" in title.lower() or "cloudflare" in title.lower():
                            # Wait for challenge to resolve
                            time.sleep(5)
                            page.wait_for_load_state("domcontentloaded", timeout=10000)

                        prices = extract_prices_from_page(page)
                        if prices:
                            # Match slug to municipalities
                            matched = name_to_munis.get(slug, [])
                            if matched:
                                for m in matched:
                                    existing[m["id"]] = prices
                            else:
                                # Store by slug for later matching
                                existing[f"_slug_{slug}"] = prices
                            success += 1
                            consecutive_errors = 0
                        else:
                            errors += 1
                        fetched = True
                        break
                    else:
                        errors += 1
                        fetched = True
                        break

                except Exception as e:
                    if attempt < MAX_RETRIES:
                        time.sleep(2 * (attempt + 1))
                        continue
                    errors += 1
                    consecutive_errors += 1
                    fetched = True
                    break

            # If too many consecutive errors, take a longer break
            if consecutive_errors >= 10:
                print(f"  10 consecutive errors — pausing 30s...")
                sys.stdout.flush()
                time.sleep(30)
                consecutive_errors = 0
                # Refresh session
                try:
                    page.goto("https://neho.ch/de/immobilienpreise-schweiz", timeout=20000, wait_until="domcontentloaded")
                    time.sleep(3)
                except Exception:
                    pass

            total_done = i + 1
            if total_done % SAVE_EVERY == 0 or total_done == len(remaining_urls):
                print(f"  Progress: {total_done}/{len(remaining_urls)} "
                      f"({success} ok, {not_found} 404, {blocked} blocked, {errors} err)")
                sys.stdout.flush()
                with open(prices_path, "w") as f:
                    json.dump(existing, f, indent=2, ensure_ascii=False)

            time.sleep(DELAY)

        browser.close()

    # Final save
    with open(prices_path, "w") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

    # Count real municipality matches (exclude _slug_ entries)
    real_prices = {k: v for k, v in existing.items() if not k.startswith("_slug_")}
    slug_only = {k: v for k, v in existing.items() if k.startswith("_slug_")}

    print(f"\nDone: {success} fetched, {not_found} not found, {blocked} blocked, {errors} errors")
    print(f"Matched to BFS IDs: {len(real_prices)}/{len(municipalities)}")
    if slug_only:
        print(f"Unmatched (stored by slug): {len(slug_only)}")

    # Copy to prices.json if we have enough data
    if len(real_prices) > 500:
        final_path = PROCESSED_DIR / "prices.json"
        with open(final_path, "w") as f:
            json.dump(real_prices, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(real_prices)} prices to {final_path}")
    else:
        print(f"Only {len(real_prices)} matches — not overwriting prices.json (need >500)")

    vals = [v["chf_per_m2"] for v in real_prices.values() if v.get("chf_per_m2")]
    if vals:
        print(f"  CHF/m²: min={min(vals)}, max={max(vals)}, median={sorted(vals)[len(vals)//2]}")


if __name__ == "__main__":
    main()
