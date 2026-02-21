#!/usr/bin/env python3
"""
Stealth Neho retry: uses persistent Chrome profile + playwright-stealth
to bypass Cloudflare for the remaining ~470 blocked municipalities.

Key differences from the basic retry:
  - Persistent user data directory (cookies survive across runs)
  - playwright-stealth patches to avoid bot detection
  - Longer, more randomised delays to mimic human browsing
  - Visits a few random "real" pages between scrape targets
  - Saves progress frequently for resume
"""
import json
import random
import re
import sys
import time
from pathlib import Path

from config import PROCESSED_DIR

PROFILE_DIR = Path("/tmp/neho_chrome_profile")
DELAY_MIN = 3.0
DELAY_MAX = 7.0
SAVE_EVERY = 10
SESSION_REFRESH_EVERY = 40


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
        parsed = [int(n.replace("'", "").replace("\u2019", ""))
                  for n in nums if n.replace("'", "").replace("\u2019", "").isdigit()]
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


# Known Neho pages that we already scraped successfully — use them as decoy visits
DECOY_SLUGS = [
    "zuerich", "bern", "luzern", "basel", "geneve", "lausanne",
    "winterthur", "st-gallen", "biel-bienne", "thun", "aarau",
    "schaffhausen", "chur", "frauenfeld", "zug", "solothurn",
]


def human_scroll(page):
    """Simulate human-like scrolling."""
    for _ in range(random.randint(1, 3)):
        page.mouse.wheel(0, random.randint(200, 600))
        time.sleep(random.uniform(0.3, 0.8))


def visit_decoy(page):
    """Visit a random known-good page to look like a real user."""
    slug = random.choice(DECOY_SLUGS)
    url = f"https://neho.ch/de/quadratmeterpreis-{slug}"
    try:
        page.goto(url, timeout=20000, wait_until="domcontentloaded")
        time.sleep(random.uniform(2, 5))
        human_scroll(page)
    except Exception:
        pass


def main():
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth
    stealth = Stealth(
        navigator_languages_override=("de-CH", "de"),
        navigator_platform_override="MacIntel",
    )

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

    slug_to_mids = {}
    for mid in missing_ids:
        m = all_munis[mid]
        slug = normalize(m["name"])
        slug_to_mids.setdefault(slug, []).append(mid)

    urls = [(slug, f"https://neho.ch/de/quadratmeterpreis-{slug}")
            for slug in slug_to_mids.keys()]
    random.shuffle(urls)

    print(f"Missing: {len(missing_ids)} municipalities ({len(urls)} unique slugs)")
    sys.stdout.flush()

    if not urls:
        print("Nothing to fetch!")
        return

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # Launch persistent context with system Chrome
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            channel="chrome",
            locale="de-CH",
            viewport={"width": 1366, "height": 768},
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )
        page = context.pages[0] if context.pages else context.new_page()
        stealth.apply_stealth_sync(page)

        # Warm up — visit homepage, scroll around
        print("Warming up with homepage...")
        sys.stdout.flush()
        try:
            page.goto("https://neho.ch/de/immobilienpreise-schweiz",
                       timeout=30000, wait_until="domcontentloaded")
            time.sleep(3)
            human_scroll(page)
            time.sleep(2)
        except Exception:
            pass

        # Visit a couple of decoy pages first
        for _ in range(2):
            visit_decoy(page)

        success = 0
        errors = 0
        not_found = 0
        consecutive_errors = 0

        for i, (slug, url) in enumerate(urls):
            # Occasional decoy visit (every ~10-15 pages)
            if i > 0 and random.random() < 0.08:
                visit_decoy(page)

            # Session refresh: close & reopen page
            if i > 0 and i % SESSION_REFRESH_EVERY == 0:
                print(f"  Refreshing page...")
                sys.stdout.flush()
                page.close()
                page = context.new_page()
                stealth.apply_stealth_sync(page)
                try:
                    page.goto("https://neho.ch/de/immobilienpreise-schweiz",
                               timeout=30000, wait_until="domcontentloaded")
                    time.sleep(random.uniform(3, 6))
                    human_scroll(page)
                except Exception:
                    pass
                visit_decoy(page)

            try:
                resp = page.goto(url, timeout=25000, wait_until="domcontentloaded")
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
                        # Wait for CF challenge to auto-resolve
                        time.sleep(10)
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=15000)
                        except Exception:
                            pass

                    # Wait for JS rendering
                    time.sleep(random.uniform(3, 5))
                    human_scroll(page)

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

            # Long pause after many consecutive errors
            if consecutive_errors >= 10:
                print(f"  10 consecutive errors — pausing 90s...")
                sys.stdout.flush()
                time.sleep(90)
                consecutive_errors = 0
                # Visit homepage to reset
                try:
                    page.goto("https://neho.ch/de/immobilienpreise-schweiz",
                               timeout=30000, wait_until="domcontentloaded")
                    time.sleep(5)
                    human_scroll(page)
                except Exception:
                    pass
                visit_decoy(page)

            done = i + 1
            if done % SAVE_EVERY == 0 or done == len(urls):
                real = {k: v for k, v in neho_raw.items() if not k.startswith("_slug_")}
                print(f"  Progress: {done}/{len(urls)} "
                      f"({success} ok, {not_found} 404, {errors} err) "
                      f"| total: {len(real)}")
                sys.stdout.flush()
                with open(PROCESSED_DIR / "prices_neho.json", "w") as f:
                    json.dump(neho_raw, f, indent=2, ensure_ascii=False)
                with open(PROCESSED_DIR / "prices.json", "w") as f:
                    json.dump({k: v for k, v in neho_raw.items()
                               if not k.startswith("_slug_")},
                              f, indent=2, ensure_ascii=False)

            # Human-like random delay
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        context.close()

    real = {k: v for k, v in neho_raw.items() if not k.startswith("_slug_")}
    print(f"\nDone: {success} new, {not_found} 404, {errors} errors")
    print(f"Total BFS-matched prices: {len(real)}/{len(municipalities)}")
    vals = [v["chf_per_m2"] for v in real.values() if v.get("chf_per_m2")]
    if vals:
        print(f"CHF/m²: min={min(vals)}, max={max(vals)}, median={sorted(vals)[len(vals)//2]}")


if __name__ == "__main__":
    main()
