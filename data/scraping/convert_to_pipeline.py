#!/usr/bin/env python3
"""
Convert OpenClaw scraped prices (prices_scraped.json) into the format
expected by the autonomy-explorer pipeline (data/processed/prices.json).

Usage:
    python3 convert_to_pipeline.py

Reads:  data/scraping/prices_scraped.json
Writes: data/processed/prices.json
Then:   Run `python3 data/scripts/05_compute_scores.py` to rebuild the GeoJSON.
"""
import json
from pathlib import Path

SCRAPING_DIR = Path(__file__).parent
PROCESSED_DIR = SCRAPING_DIR.parent / "processed"


def main():
    # Load scraped prices
    scraped_path = SCRAPING_DIR / "prices_scraped.json"
    if not scraped_path.exists():
        print(f"ERROR: {scraped_path} not found. Run OpenClaw scraper first.")
        return

    with open(scraped_path) as f:
        scraped = json.load(f)

    print(f"Loaded {len(scraped)} scraped prices")

    # Convert to pipeline format
    # Pipeline expects: { "municipality_id": { "chf_per_m2": number, ... } }
    prices = {}
    used = 0
    skipped = 0

    for muni_id, data in scraped.items():
        # Primary: buy apartment price
        price = data.get("buy_apartment_chf_m2")

        # Fallback: buy house price
        if price is None:
            price = data.get("buy_house_chf_m2")

        # Fallback: Homegate-specific field if both sources were scraped
        if price is None:
            price = data.get("homegate_buy_apartment_chf_m2")
        if price is None:
            price = data.get("immoscout24_buy_apartment_chf_m2")

        if price is not None and price > 0:
            prices[muni_id] = {
                "chf_per_m2": int(round(price)),
                "source": data.get("source", "scraped"),
                "type": "real",
                # Include rent if available
                "rent_chf_m2": data.get("rent_apartment_chf_m2"),
                "buy_min": data.get("buy_apartment_min"),
                "buy_max": data.get("buy_apartment_max"),
            }
            used += 1
        else:
            skipped += 1

    print(f"Converted: {used} with valid prices, {skipped} skipped (no price)")

    # Load existing prices to fill gaps
    existing_path = PROCESSED_DIR / "prices.json"
    if existing_path.exists():
        with open(existing_path) as f:
            existing = json.load(f)

        # Count how many we're upgrading vs keeping
        upgraded = sum(1 for k in prices if k in existing)
        kept = 0
        for k, v in existing.items():
            if k not in prices:
                prices[k] = v
                kept += 1

        print(f"Upgraded {upgraded} existing estimates with real prices")
        print(f"Kept {kept} existing estimates (no scraped data available)")

    # Save
    out_path = PROCESSED_DIR / "prices.json"
    with open(out_path, "w") as f:
        json.dump(prices, f, indent=2)

    print(f"\nSaved {len(prices)} prices to {out_path}")
    print(f"  Real (scraped): {used}")
    print(f"  Estimated (fallback): {len(prices) - used}")
    print(f"\nNext step: Run 'python3 data/scripts/05_compute_scores.py' to rebuild GeoJSON")


if __name__ == "__main__":
    main()
