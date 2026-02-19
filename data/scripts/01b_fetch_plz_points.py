#!/usr/bin/env python3
"""
Step 1b: Fetch Swiss PLZ (postal code) centroids for higher-resolution travel times.

Downloads ~3,200 PLZ records from Opendatasoft with:
  - PLZ code and locality name
  - Point coordinates (centroid of PLZ polygon)
  - Municipality (BFS) code mapping (one PLZ can serve multiple municipalities)

Each PLZ is expanded into one record per municipality it covers. Travel times
are queried from PLZ centroids (closer to where people live than municipality
polygon centroids), then aggregated back to municipality level.

Outputs:
  - data/processed/plz_points.json (unique PLZ points for travel time queries)
  - data/processed/plz_municipality_map.json (PLZ → municipality mapping)
"""
import json

import requests

from config import PROCESSED_DIR

OPENDATASOFT_PLZ_URL = (
    "https://public.opendatasoft.com/api/explore/v2.1/catalog/datasets/"
    "georef-switzerland-postleitzahl/records"
)


def fetch_plz_points():
    """Fetch all Swiss PLZ points with coordinates and municipality mapping."""
    all_records = []
    offset = 0
    limit = 100

    print("Fetching PLZ points from Opendatasoft...")
    while True:
        params = {
            "limit": limit,
            "offset": offset,
            "select": (
                "plz_code,kan_code,kan_name,"
                "bez_code,bez_name,gem_code,gem_name,"
                "geo_point_2d"
            ),
        }
        resp = requests.get(OPENDATASOFT_PLZ_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            break
        all_records.extend(results)
        offset += limit
        if offset % 500 == 0:
            print(f"  Fetched {len(all_records)} records...")

    print(f"Total PLZ records fetched: {len(all_records)}")

    # Helper: ensure value is a list
    def as_list(val):
        if isinstance(val, list):
            return val
        if val is None or val == "":
            return []
        return [val]

    # Build two outputs:
    # 1. Unique PLZ points (for travel time queries) — one per PLZ
    # 2. PLZ → municipality mapping (one PLZ can serve multiple municipalities)
    plz_points = {}  # plz_code → { plz, name, lat, lon }
    plz_muni_map = {}  # plz_code → [municipality_id, ...]
    muni_plz_map = {}  # municipality_id → [plz_code, ...]

    for rec in all_records:
        geo = rec.get("geo_point_2d", {})
        if not geo or not geo.get("lat") or not geo.get("lon"):
            continue

        plz_codes = as_list(rec.get("plz_code", ""))
        gem_codes = as_list(rec.get("gem_code", ""))
        gem_names = as_list(rec.get("gem_name", ""))

        if not plz_codes or not gem_codes:
            continue

        plz = str(plz_codes[0])

        # Store the unique PLZ point
        if plz not in plz_points:
            plz_points[plz] = {
                "plz": plz,
                "name": gem_names[0] if gem_names else "",
                "lat": geo["lat"],
                "lon": geo["lon"],
            }

        # Map PLZ → all municipalities it covers
        if plz not in plz_muni_map:
            plz_muni_map[plz] = set()
        for gc in gem_codes:
            gc_str = str(gc)
            plz_muni_map[plz].add(gc_str)
            if gc_str not in muni_plz_map:
                muni_plz_map[gc_str] = set()
            muni_plz_map[gc_str].add(plz)

    # Convert sets to sorted lists
    plz_muni_map = {k: sorted(v) for k, v in plz_muni_map.items()}
    muni_plz_map = {k: sorted(v) for k, v in muni_plz_map.items()}

    # Convert plz_points dict to sorted list
    plz_list = sorted(plz_points.values(), key=lambda p: p["plz"])

    print(f"\nResults:")
    print(f"  {len(plz_list)} unique PLZ points (for travel time queries)")
    print(f"  {len(muni_plz_map)} municipalities covered")
    print(f"  PLZ per municipality distribution:")

    counts = [len(v) for v in muni_plz_map.values()]
    for n in [1, 2, 3, 5, 10]:
        c = sum(1 for x in counts if x >= n)
        print(f"    >= {n} PLZ: {c} municipalities")

    avg_plz = sum(counts) / len(counts) if counts else 0
    print(f"    Average: {avg_plz:.1f} PLZ per municipality")

    return plz_list, plz_muni_map, muni_plz_map


def main():
    plz_list, plz_muni_map, muni_plz_map = fetch_plz_points()

    # Check coverage against municipalities.json
    muni_path = PROCESSED_DIR / "municipalities.json"
    if muni_path.exists():
        with open(muni_path) as f:
            municipalities = json.load(f)
        all_muni_ids = set(m["id"] for m in municipalities)
        covered = all_muni_ids & set(muni_plz_map.keys())
        missing = all_muni_ids - covered
        print(f"\nCoverage check vs municipalities.json:")
        print(f"  {len(covered)}/{len(all_muni_ids)} municipalities have PLZ data")
        print(f"  {len(missing)} municipalities missing PLZ mapping")
        if missing:
            # For missing municipalities, they'll fall back to their original centroid
            muni_by_id = {m["id"]: m for m in municipalities}
            print(f"  Missing examples: {[muni_by_id[m]['name'] for m in sorted(missing)[:5]]}")

    # Save PLZ points (for travel time fetching)
    out_path = PROCESSED_DIR / "plz_points.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(plz_list, f, ensure_ascii=False, indent=2)
    print(f"\nSaved {len(plz_list)} PLZ points to {out_path}")

    # Save mapping files
    mapping = {
        "plz_to_municipalities": plz_muni_map,
        "municipality_to_plz": muni_plz_map,
    }
    map_path = PROCESSED_DIR / "plz_municipality_map.json"
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    print(f"Saved mapping to {map_path}")


if __name__ == "__main__":
    main()
