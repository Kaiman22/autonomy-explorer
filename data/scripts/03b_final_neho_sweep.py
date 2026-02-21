#!/usr/bin/env python3
"""
Final Neho sweep: tries every missing municipality with persistent
Chrome profile + stealth. Dismisses cookie banner. Classifies results
precisely: price found / "Nicht genug Daten" / 404 / error.
"""
import json
import random
import re
import sys
import time
from pathlib import Path

from config import PROCESSED_DIR

DELAY_MIN = 1.5
DELAY_MAX = 3.5
SAVE_EVERY = 25


def parse_chf(text):
    if not text:
        return None
    cleaned = text.replace("CHF", "").strip()
    cleaned = cleaned.replace("'", "").replace("\u2019", "").replace(" ", "")
    match = re.search(r"(\d+)", cleaned)
    val = int(match.group(1)) if match else None
    # Filter out placeholder values
    if val is not None and val <= 0:
        return None
    return val


def extract_prices_from_page(page):
    def safe_text(selector):
        el = page.query_selector(selector)
        return el.text_content().strip() if el else None

    apt_text = safe_text(".js-priceAverageApartments") or ""
    house_text = safe_text(".js-priceAverageHouses") or ""

    # Check for "Nicht genug Daten"
    if "nicht genug" in apt_text.lower() and "nicht genug" in house_text.lower():
        return "no_data"

    avg_apt = parse_chf(apt_text)
    avg_house = parse_chf(house_text)

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
        return "no_data"

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


def main():
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth

    stealth = Stealth(
        navigator_languages_override=("de-CH", "de"),
        navigator_platform_override="MacIntel",
    )

    with open(PROCESSED_DIR / "municipalities.json") as f:
        municipalities = json.load(f)
    with open(PROCESSED_DIR / "prices.json") as f:
        current_prices = json.load(f)
    with open(PROCESSED_DIR / "prices_neho.json") as f:
        neho_raw = json.load(f)

    all_munis = {m["id"]: m for m in municipalities}
    missing_ids = set(all_munis.keys()) - set(current_prices.keys())

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

    profile_dir = Path("/tmp/neho_chrome_final")
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
            page.goto("https://neho.ch/de/immobilienpreise-schweiz",
                       timeout=30000, wait_until="domcontentloaded")
            time.sleep(3)
            for btn in page.query_selector_all("button"):
                if "akzeptieren" in btn.text_content().strip().lower():
                    btn.click()
                    time.sleep(1)
                    break
        except Exception:
            pass

        stats = {"ok": 0, "no_data": 0, "404": 0, "cf_block": 0, "error": 0}

        for i, (slug, url) in enumerate(urls):
            try:
                resp = page.goto(url, timeout=20000, wait_until="domcontentloaded")
                status = resp.status if resp else 0

                if status == 404:
                    stats["404"] += 1
                elif status == 403:
                    stats["cf_block"] += 1
                elif status == 200:
                    title = page.title()
                    if "just a moment" in title.lower() or "cloudflare" in title.lower():
                        time.sleep(8)
                        stats["cf_block"] += 1
                        continue

                    time.sleep(3)
                    result = extract_prices_from_page(page)

                    if result == "no_data":
                        stats["no_data"] += 1
                    elif isinstance(result, dict):
                        for mid in slug_to_mids.get(slug, []):
                            neho_raw[mid] = result
                            current_prices[mid] = result
                        stats["ok"] += 1
                    else:
                        stats["error"] += 1
                else:
                    stats["error"] += 1

            except Exception:
                stats["error"] += 1

            done = i + 1
            if done % SAVE_EVERY == 0 or done == len(urls):
                real = {k: v for k, v in neho_raw.items() if not k.startswith("_slug_")}
                print(f"  {done}/{len(urls)}: {stats} | total prices: {len(real)}")
                sys.stdout.flush()
                with open(PROCESSED_DIR / "prices_neho.json", "w") as f:
                    json.dump(neho_raw, f, indent=2, ensure_ascii=False)
                with open(PROCESSED_DIR / "prices.json", "w") as f:
                    json.dump({k: v for k, v in neho_raw.items()
                               if not k.startswith("_slug_")},
                              f, indent=2, ensure_ascii=False)

            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        context.close()

    real = {k: v for k, v in neho_raw.items() if not k.startswith("_slug_")}
    print(f"\nFinal: {stats}")
    print(f"Total BFS-matched prices: {len(real)}/{len(municipalities)}")
    vals = [v["chf_per_m2"] for v in real.values() if v.get("chf_per_m2")]
    if vals:
        print(f"CHF/m²: min={min(vals)}, max={max(vals)}, median={sorted(vals)[len(vals)//2]}")


if __name__ == "__main__":
    main()
