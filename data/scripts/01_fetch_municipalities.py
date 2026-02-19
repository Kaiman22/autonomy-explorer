#!/usr/bin/env python3
"""
Step 1: Fetch Swiss municipality centroids from Opendatasoft.
Outputs: data/processed/municipalities.json
"""
import json
import requests
from config import PROCESSED_DIR, FRONTEND_DATA_DIR

OPENDATASOFT_URL = (
    "https://public.opendatasoft.com/api/explore/v2.1/catalog/datasets/"
    "georef-switzerland-gemeinde/records"
)


def fetch_municipalities():
    """Fetch all Swiss municipalities with centroids from Opendatasoft."""
    all_records = []
    offset = 0
    limit = 100

    print("Fetching municipalities from Opendatasoft...")
    while True:
        params = {
            "limit": limit,
            "offset": offset,
            "select": (
                "gem_name,gem_code,kan_name,kan_code,"
                "geo_point_2d,bez_name"
            ),
        }
        resp = requests.get(OPENDATASOFT_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            break
        all_records.extend(results)
        offset += limit
        print(f"  Fetched {len(all_records)} records...")

    print(f"Total municipalities fetched: {len(all_records)}")

    # Helper: API returns lists for text fields, unwrap them
    def unwrap(val):
        if isinstance(val, list):
            return val[0] if val else ""
        return val

    # Deduplicate by gem_code (BFS number)
    seen = {}
    for rec in all_records:
        code = unwrap(rec.get("gem_code", ""))
        if code and code not in seen:
            seen[code] = rec

    municipalities = []
    for code, rec in sorted(seen.items()):
        geo = rec.get("geo_point_2d", {})
        if not geo:
            continue
        municipalities.append({
            "id": str(code),
            "name": unwrap(rec.get("gem_name", "")),
            "canton": unwrap(rec.get("kan_name", "")),
            "canton_code": unwrap(rec.get("kan_code", "")),
            "district": unwrap(rec.get("bez_name", "")),
            "lat": geo.get("lat"),
            "lon": geo.get("lon"),
        })

    print(f"Unique municipalities with coordinates: {len(municipalities)}")
    return municipalities


def main():
    municipalities = fetch_municipalities()

    # Save processed JSON
    out_path = PROCESSED_DIR / "municipalities.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(municipalities, f, ensure_ascii=False, indent=2)
    print(f"Saved to {out_path}")

    # Also create a GeoJSON for the frontend
    features = []
    for m in municipalities:
        features.append({
            "type": "Feature",
            "properties": {
                "id": m["id"],
                "name": m["name"],
                "canton": m["canton"],
                "canton_code": m["canton_code"],
                "district": m["district"],
            },
            "geometry": {
                "type": "Point",
                "coordinates": [m["lon"], m["lat"]],
            },
        })

    geojson = {"type": "FeatureCollection", "features": features}
    geo_path = PROCESSED_DIR / "municipalities.geojson"
    with open(geo_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)
    print(f"Saved GeoJSON to {geo_path}")


if __name__ == "__main__":
    main()
