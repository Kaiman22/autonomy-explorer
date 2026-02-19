#!/usr/bin/env python3
"""
Step 1c: Extract settlement points from swissNAMES3D and map to municipalities.

swissNAMES3D contains ~34,000 "Ort" (settlement) entries with population categories.
We filter for settlements with >= 100 inhabitants (~4,000 points).

These are actual village/town center points — much better than PLZ polygon centroids
which can fall in forests/fields far from any train station.

Example: Galgenen PLZ centroid is 2.2 km from the station (26 min walk),
but the swissNAMES3D settlement point is only 826m away (10 min walk).

Municipality assignment via geo.admin.ch identify API (reverse geocoding),
using concurrent requests for speed.

Outputs:
  - data/processed/settlement_points.json  (list of settlement dicts)
  - data/processed/settlement_municipality_map.json (bidirectional mapping)
"""
import csv
import json
import sys
import time as time_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from config import PROCESSED_DIR, RAW_DIR

# swissNAMES3D CSV file (polygon layer contains settlements)
SWISSNAMES_PLY = RAW_DIR / "csv_LV95_LN02" / "swissNAMES3D_PLY.csv"

# Minimum population category to include
MIN_POP_CATEGORIES = {
    "100 bis 999",
    "1'000 bis 1'999",
    "2'000 bis 9'999",
    "10'000 bis 49'999",
    "50'000 bis 100'000",
    "> 100'000",
}

# geo.admin.ch identify API for municipality lookup
GEO_ADMIN_URL = "https://api3.geo.admin.ch/rest/services/api/MapServer/identify"
MUNICIPALITY_LAYER = "ch.swisstopo.swissboundaries3d-gemeinde-flaeche.fill"

# Concurrency settings
MAX_WORKERS = 20  # concurrent API requests
SAVE_INTERVAL = 500  # save progress every N settlements


def lv95_to_wgs84(e, n):
    """Convert Swiss LV95 (E, N) to WGS84 (lat, lon)."""
    y = (e - 2_600_000) / 1_000_000
    x = (n - 1_200_000) / 1_000_000
    lon_sec = (
        2.6779094 + 4.728982 * y + 0.791484 * y * x
        + 0.1306 * y * x * x - 0.0436 * y * y * y
    )
    lat_sec = (
        16.9023892 + 3.238272 * x - 0.270978 * y * y
        - 0.002528 * x * x - 0.0447 * y * y * x - 0.0140 * x * x * x
    )
    return lat_sec * 100 / 36, lon_sec * 100 / 36


def extract_settlements():
    """Extract settlements with >= 100 inhabitants from swissNAMES3D."""
    settlements = []
    with open(SWISSNAMES_PLY, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            if row["OBJEKTART"] != "Ort":
                continue
            if row["EINWOHNERKATEGORIE"] not in MIN_POP_CATEGORIES:
                continue

            e, n = float(row["E"]), float(row["N"])
            lat, lon = lv95_to_wgs84(e, n)

            settlements.append({
                "uuid": row["UUID"],
                "name": row["NAME"],
                "pop_category": row["EINWOHNERKATEGORIE"],
                "lat": lat,
                "lon": lon,
                "e_lv95": e,
                "n_lv95": n,
            })

    print(f"Extracted {len(settlements)} settlements with >= 100 inhabitants")
    return settlements


def _lookup_one(session, settlement):
    """Look up municipality for a single settlement. Returns updated settlement dict."""
    params = {
        "geometry": f"{settlement['lon']},{settlement['lat']}",
        "geometryType": "esriGeometryPoint",
        "layers": f"all:{MUNICIPALITY_LAYER}",
        "tolerance": "0",
        "sr": "4326",
        "returnGeometry": "false",
    }

    try:
        r = session.get(GEO_ADMIN_URL, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])

        # Find the current municipality entry
        best = None
        for res in results:
            attrs = res.get("attributes", {})
            if attrs.get("is_current_jahr"):
                best = attrs
                break
        if not best and results:
            best = results[-1].get("attributes", {})

        if best:
            # Zero-pad to 4 digits to match municipality data format
            settlement["municipality_id"] = str(best["gde_nr"]).zfill(4)
            settlement["municipality_name"] = best.get("gemname", "")
            settlement["canton"] = best.get("kanton", "")
            return settlement, True
    except Exception as ex:
        pass

    return settlement, False


def lookup_municipalities_concurrent(settlements):
    """Look up municipalities using concurrent requests."""
    print(f"\nLooking up municipalities for {len(settlements)} settlements (concurrent)...")
    sys.stdout.flush()

    session = requests.Session()
    found = 0
    failed = 0
    t0 = time_mod.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_lookup_one, session, s): i
            for i, s in enumerate(settlements)
        }

        for future in as_completed(futures):
            idx = futures[future]
            s, success = future.result()
            settlements[idx] = s
            if success:
                found += 1
            else:
                failed += 1

            total = found + failed
            if total % 200 == 0:
                elapsed = time_mod.time() - t0
                rate = total / elapsed if elapsed > 0 else 0
                print(f"  {total}/{len(settlements)} ({found} found, {failed} failed) [{rate:.0f}/s]")
                sys.stdout.flush()

    elapsed = time_mod.time() - t0
    print(f"\nDone in {elapsed:.1f}s: {found} found, {failed} failed")
    sys.stdout.flush()
    return settlements


def build_mapping(settlements):
    """Build bidirectional settlement ↔ municipality mapping."""
    settlement_to_muni = {}
    muni_to_settlements = {}

    for s in settlements:
        muni_id = s.get("municipality_id")
        if not muni_id:
            continue

        sid = s["uuid"]
        settlement_to_muni[sid] = muni_id

        if muni_id not in muni_to_settlements:
            muni_to_settlements[muni_id] = []
        muni_to_settlements[muni_id].append(sid)

    return {
        "settlement_to_municipality": settlement_to_muni,
        "municipality_to_settlements": muni_to_settlements,
    }


def main():
    # Step 1: Extract settlements from swissNAMES3D
    settlements = extract_settlements()

    # Step 2: Look up municipality for each settlement (concurrent)
    settlements = lookup_municipalities_concurrent(settlements)

    # Step 3: Filter out settlements without municipality assignment
    valid = [s for s in settlements if s.get("municipality_id")]
    print(f"\n{len(valid)} settlements with valid municipality assignment")

    # Step 4: Count unique municipalities covered
    munis = set(s["municipality_id"] for s in valid)
    print(f"Covering {len(munis)} unique municipalities")

    # Step 5: Save outputs
    out_points = PROCESSED_DIR / "settlement_points.json"
    with open(out_points, "w") as f:
        json.dump(valid, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(valid)} settlement points to {out_points}")

    mapping = build_mapping(valid)
    out_map = PROCESSED_DIR / "settlement_municipality_map.json"
    with open(out_map, "w") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)
    print(f"Saved mapping to {out_map}")

    # Step 6: Summary stats
    muni_counts = {}
    for s in valid:
        mid = s["municipality_id"]
        muni_counts[mid] = muni_counts.get(mid, 0) + 1

    avg_per_muni = sum(muni_counts.values()) / max(len(muni_counts), 1)
    if muni_counts:
        max_count = max(muni_counts.values())
        max_muni = [k for k, v in muni_counts.items() if v == max_count][0]
        max_muni_name = next(s["municipality_name"] for s in valid if s["municipality_id"] == max_muni)
        print(f"\nAvg settlements per municipality: {avg_per_muni:.1f}")
        print(f"Max: {max_muni_name} with {max_count} settlements")

    # Spot-check known places
    print("\nSpot checks:")
    for name in ["Galgenen", "Zürich", "Bern", "Lugano", "Dübendorf", "Küsnacht ZH"]:
        for s in valid:
            if s["name"] == name:
                print(f"  {name}: → {s['municipality_name']} (BFS {s['municipality_id']}, {s['canton']})")
                break


if __name__ == "__main__":
    main()
