#!/usr/bin/env python3
"""
Merge prices from multiple sources into a single prices.json.

Priority:
  1. Neho (hedonic model — more reliable per-m² estimates)
  2. Homegate (median of listing prices — good fallback)

Output: data/processed/prices.json
"""
import json
import statistics

from config import PROCESSED_DIR


def main():
    with open(PROCESSED_DIR / "municipalities.json") as f:
        municipalities = json.load(f)
    all_ids = set(m["id"] for m in municipalities)

    # Load Neho prices
    with open(PROCESSED_DIR / "prices_neho.json") as f:
        neho_raw = json.load(f)
    neho = {k: v for k, v in neho_raw.items()
            if not k.startswith("_slug_") and v.get("chf_per_m2")}

    # Load Homegate prices
    with open(PROCESSED_DIR / "prices_homegate.json") as f:
        homegate_raw = json.load(f)
    homegate = {k: v for k, v in homegate_raw.items()
                if v.get("chf_per_m2")}

    # Merge: Neho primary, Homegate fills gaps
    merged = {}
    neho_used = 0
    homegate_used = 0
    both_available = 0

    for mid in all_ids:
        n = neho.get(mid)
        h = homegate.get(mid)

        if n and h:
            both_available += 1
            # Use Neho as primary, but store homegate for reference
            entry = dict(n)
            entry["homegate_chf_per_m2"] = h["chf_per_m2"]
            entry["homegate_n_listings"] = h.get("n_listings", 0)
            merged[mid] = entry
            neho_used += 1
        elif n:
            merged[mid] = dict(n)
            neho_used += 1
        elif h:
            merged[mid] = dict(h)
            homegate_used += 1

    # Stats
    print(f"Total municipalities: {len(all_ids)}")
    print(f"Neho prices available: {len(neho)}")
    print(f"Homegate prices available: {len(homegate)}")
    print(f"\nMerged result:")
    print(f"  Neho used (primary): {neho_used}")
    print(f"  Homegate used (fills gaps): {homegate_used}")
    print(f"  Both available: {both_available}")
    print(f"  Total with price: {len(merged)} ({100*len(merged)/len(all_ids):.1f}%)")
    print(f"  Still missing: {len(all_ids) - len(merged)}")

    vals = [v["chf_per_m2"] for v in merged.values()]
    print(f"\nCHF/m² stats:")
    print(f"  Min: {min(vals):,}")
    print(f"  Max: {max(vals):,}")
    print(f"  Median: {statistics.median(vals):,.0f}")
    print(f"  Mean: {statistics.mean(vals):,.0f}")

    # Source breakdown
    neho_vals = [v["chf_per_m2"] for v in merged.values() if v.get("type") == "neho"]
    hg_vals = [v["chf_per_m2"] for v in merged.values() if v.get("type") == "homegate"]
    if neho_vals:
        print(f"\nNeho: median={statistics.median(neho_vals):,.0f}, "
              f"mean={statistics.mean(neho_vals):,.0f}")
    if hg_vals:
        print(f"Homegate: median={statistics.median(hg_vals):,.0f}, "
              f"mean={statistics.mean(hg_vals):,.0f}")

    # Save
    out_path = PROCESSED_DIR / "prices.json"
    with open(out_path, "w") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
