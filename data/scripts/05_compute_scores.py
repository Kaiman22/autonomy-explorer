#!/usr/bin/env python3
"""
Step 5: Compute travel time metrics and export final GeoJSON for frontend.

Outputs settlement-level points (~3,966) using swissNAMES3D settlement locations.
These are actual village/town center points — much better than PLZ polygon centroids.
Each settlement has its own travel times but inherits municipality-level
prices and taxes.

Metrics (all recomputed dynamically in frontend based on user settings):
  - avg_car_access: mean driving time to all cities (minutes)
  - avg_pt_access: mean PT time to all cities (minutes, no walk deduction)
  - optimum_access: mean of min(car, pt) per city (minutes)
  - car_pt_delta_min / pct: car vs PT difference
  - av_upside: minutes saved by AV vs the current best option (PT comfort or manual drive)

Outputs: frontend/public/data/municipalities_scored.geojson
"""
import json
import statistics
from datetime import datetime, timezone

from config import (
    CITIES,
    COMFORT,
    FRONTEND_DATA_DIR,
    PROCESSED_DIR,
)


def load_data():
    """Load all preprocessed data files."""
    with open(PROCESSED_DIR / "municipalities.json") as f:
        municipalities = {m["id"]: m for m in json.load(f)}

    # Settlement points (swissNAMES3D, ~3,966 with >=100 inhabitants)
    settlements = []
    settlement_path = PROCESSED_DIR / "settlement_points.json"
    if settlement_path.exists():
        with open(settlement_path) as f:
            settlements = json.load(f)

    settlement_mapping = {}
    settlement_map_path = PROCESSED_DIR / "settlement_municipality_map.json"
    if settlement_map_path.exists():
        with open(settlement_map_path) as f:
            settlement_mapping = json.load(f)

    # Settlement-level travel times (UUID-keyed)
    settlement_drive = {}
    drive_path = PROCESSED_DIR / "settlement_travel_times_driving.json"
    if drive_path.exists():
        with open(drive_path) as f:
            settlement_drive = json.load(f)

    settlement_pt = {}
    pt_path = PROCESSED_DIR / "settlement_travel_times_pt.json"
    if pt_path.exists():
        with open(pt_path) as f:
            settlement_pt = json.load(f)

    # Municipality-level travel times as fallback
    travel_times = {"driving": {}, "public_transport": {}}
    tt_path = PROCESSED_DIR / "travel_times.json"
    if tt_path.exists():
        with open(tt_path) as f:
            travel_times = json.load(f)

    prices = {}
    price_path = PROCESSED_DIR / "prices.json"
    if price_path.exists():
        with open(price_path) as f:
            prices = json.load(f)

    taxes = {}
    tax_path = PROCESSED_DIR / "taxes.json"
    if tax_path.exists():
        with open(tax_path) as f:
            taxes = json.load(f)

    # PT breakdown (walk/wait/IVT from 02g) — partial coverage OK
    pt_breakdown = {}
    bd_path = PROCESSED_DIR / "settlement_pt_breakdown.json"
    if bd_path.exists():
        with open(bd_path) as f:
            pt_breakdown = json.load(f)

    return municipalities, settlements, settlement_mapping, settlement_drive, settlement_pt, travel_times, prices, taxes, pt_breakdown


def build_pt_breakdown(uuid, pt_breakdown):
    """Build compact PT breakdown dict for a settlement (or None if no data)."""
    bd = pt_breakdown.get(uuid)
    if not bd:
        return None
    result = {}
    for city_id, v in bd.items():
        if v is not None:
            result[city_id] = {
                "w": v["walk_s"],   # walk seconds
                "t": v["wait_s"],   # wait/transfer seconds
                "i": v["ivt_s"],    # in-vehicle seconds
            }
    return result if result else None


def compute_scores(municipalities, settlements, settlement_mapping, settlement_drive, settlement_pt, travel_times, prices, taxes, pt_breakdown):
    """Compute travel time metrics for each settlement point."""
    muni_driving = travel_times.get("driving", {})
    muni_pt = travel_times.get("public_transport", {})

    av_factor = COMFORT["av_factor"]
    pt_factor = COMFORT["oev_sitting_factor"]

    # Build settlement features
    features = []
    for s in settlements:
        uuid = s["uuid"]
        muni_id = s.get("municipality_id")

        # Travel times: use settlement-level if available, else fall back to municipality
        d = settlement_drive.get(uuid, {})
        pt = settlement_pt.get(uuid, {})

        if not d and muni_id:
            d = muni_driving.get(muni_id, {})
        if not pt and muni_id:
            pt = muni_pt.get(muni_id, {})

        # Municipality data (prices, taxes, name, canton)
        muni = municipalities.get(muni_id, {}) if muni_id else {}
        price_data = prices.get(muni_id) if muni_id else None
        tax_data = taxes.get(muni_id) if muni_id else None

        features.append({
            "uuid": uuid,
            "settlement_name": s["name"],
            "pop_category": s.get("pop_category", ""),
            "municipality_id": muni_id,
            "name": muni.get("name", s.get("municipality_name", s["name"])),
            "canton": muni.get("canton", s.get("canton", "")),
            "canton_code": muni.get("canton_code", ""),
            "lat": s["lat"],
            "lon": s["lon"],
            "driving": d,
            "pt": pt,
            "price_data": price_data,
            "tax_data": tax_data,
        })

    # Compute metrics for each settlement (defaults, all 10 cities)
    scored = []
    for i, sf in enumerate(features):
        d = sf["driving"]
        pt = sf["pt"]

        # Raw travel times across all 10 cities (minutes, no comfort weighting)
        car_times_min = []
        pt_times_min = []
        optimum_times = []
        # For AV upside: per-ref advantages, then averaged
        ref_upsides = []

        for city_id in CITIES:
            ds = d.get(city_id)
            ps = pt.get(city_id)

            car_min = ds / 60.0 if ds is not None else None
            pt_min = ps / 60.0 if ps is not None else None

            # Plausibility filter
            if car_min is not None and pt_min is not None and car_min > 0:
                ratio = pt_min / car_min
                if ratio > 3.5:
                    pt_min = None  # implausible PT
                elif ratio < 1 / 3.5:
                    car_min = None  # implausible car

            if car_min is not None:
                car_times_min.append(car_min)
            if pt_min is not None:
                pt_times_min.append(pt_min)

            # Optimum: min(car, pt)
            if car_min is not None and pt_min is not None:
                optimum_times.append(min(car_min, pt_min))
            elif car_min is not None:
                optimum_times.append(car_min)
            elif pt_min is not None:
                optimum_times.append(pt_min)

            # AV upside: compute per-ref, then average across refs.
            # Avoids averaging modes first, which mixes car-winning and
            # PT-winning refs and overstates the upside.
            if car_min is not None and pt_min is not None:
                bd_city = pt_breakdown.get(sf["uuid"], {}).get(city_id) if pt_breakdown else None
                if bd_city:
                    walk_min = bd_city["walk_s"] / 60.0
                    wait_min = bd_city["wait_s"] / 60.0
                    ivt_min = bd_city["ivt_s"] / 60.0
                    pt_comfort = ivt_min * pt_factor + walk_min + wait_min
                else:
                    pt_comfort = pt_min * pt_factor
                av_drive = car_min * av_factor
                best_current = min(pt_comfort, car_min)
                ref_upside = best_current - av_drive if av_drive < best_current else 0.0
                ref_upsides.append(ref_upside)

        avg_car = sum(car_times_min) / len(car_times_min) if car_times_min else None
        avg_pt = sum(pt_times_min) / len(pt_times_min) if pt_times_min else None
        optimum_access = sum(optimum_times) / len(optimum_times) if optimum_times else None

        # Delta
        if avg_car is not None and avg_pt is not None:
            delta_min = avg_car - avg_pt
        else:
            delta_min = None

        # AV upside: average of per-ref AV advantages (non-negative by construction)
        av_upside = None
        if ref_upsides:
            avg_upside = sum(ref_upsides) / len(ref_upsides)
            if avg_upside > 0:
                av_upside = avg_upside

        drive_times_list = [d.get(c) for c in CITIES if d.get(c) is not None]
        pt_times_list = [pt.get(c) for c in CITIES if pt.get(c) is not None]

        price_data = sf["price_data"]
        tax_data = sf["tax_data"]

        scored.append({
            "id": f"s_{i}",
            "settlement_name": sf["settlement_name"],
            "municipality_id": sf["municipality_id"],
            "name": sf["name"],
            "canton": sf["canton"],
            "canton_code": sf["canton_code"],
            "pop_category": sf["pop_category"],
            "lat": sf["lat"],
            "lon": sf["lon"],
            # Raw travel times (seconds) — frontend recomputes all metrics from these
            "drive_times": {c: d.get(c) for c in CITIES},
            "pt_times": {c: pt.get(c) for c in CITIES},
            # PT breakdown (walk/wait/IVT seconds) — from 02g, partial coverage
            "pt_breakdown": build_pt_breakdown(sf["uuid"], pt_breakdown),
            # Min times (seconds)
            "min_drive_s": min(drive_times_list) if drive_times_list else None,
            "min_pt_s": min(pt_times_list) if pt_times_list else None,
            # Price (from municipality)
            "chf_per_m2": price_data.get("chf_per_m2") if price_data else None,
            # Tax (from municipality)
            "tax_multiplier": tax_data.get("multiplier") if tax_data else None,
            # Precomputed metrics (default: all 10 cities, default comfort factors)
            # Frontend recomputes these dynamically based on user settings
            "avg_car_access": round(avg_car, 1) if avg_car is not None else None,
            "avg_pt_access": round(avg_pt, 1) if avg_pt is not None else None,
            "optimum_access": round(optimum_access, 1) if optimum_access is not None else None,
            "car_pt_delta_min": round(delta_min, 1) if delta_min is not None else None,
            "av_upside": round(av_upside, 1) if av_upside is not None else None,
        })

    return scored


def export_geojson(scored):
    """Export scored data as GeoJSON for frontend."""
    features = []
    for s in scored:
        features.append({
            "type": "Feature",
            "properties": {k: v for k, v in s.items() if k not in ("lat", "lon")},
            "geometry": {
                "type": "Point",
                "coordinates": [s["lon"], s["lat"]],
            },
        })

    # Read price file for freshness info
    price_freshness = None
    price_path = PROCESSED_DIR / "prices.json"
    if price_path.exists():
        with open(price_path) as pf:
            pdata = json.load(pf)
        timestamps = [v.get("fetched_at") for v in pdata.values() if isinstance(v, dict) and v.get("fetched_at")]
        if timestamps:
            oldest = min(timestamps)
            newest = max(timestamps)
            price_freshness = {"oldest": oldest, "newest": newest, "count": len(timestamps)}

    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "cities": {k: v["name"] for k, v in CITIES.items()},
            "comfort_factors": COMFORT,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_freshness": {
                "prices": price_freshness,
            },
        },
        "features": features,
    }

    out_path = FRONTEND_DATA_DIR / "municipalities_scored.geojson"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)
    print(f"Saved scored GeoJSON to {out_path}")
    print(f"  {len(features)} features (settlement-level points)")

    # Print metric stats
    avg_cars = [s["avg_car_access"] for s in scored if s["avg_car_access"] is not None]
    av_upsides = [s["av_upside"] for s in scored if s["av_upside"] is not None]
    if avg_cars:
        print(f"  Avg car access: {min(avg_cars):.1f} - {max(avg_cars):.1f}  median: {statistics.median(avg_cars):.1f} min")
    if av_upsides:
        print(f"  AV upside: {len(av_upsides)} settlements with upside, median: {statistics.median(av_upsides):.1f} min")

    munis = set(s["municipality_id"] for s in scored if s["municipality_id"])
    print(f"  Covering {len(munis)} municipalities")

    return geojson


def main():
    municipalities, settlements, settlement_mapping, settlement_drive, settlement_pt, travel_times, prices, taxes, pt_breakdown = load_data()
    print(f"Municipalities: {len(municipalities)}")
    print(f"Settlement points: {len(settlements)}")
    print(f"Settlement driving times: {len(settlement_drive)}")
    print(f"Settlement PT times: {len(settlement_pt)}")
    print(f"PT breakdown: {len(pt_breakdown)} settlements")
    print(f"Municipality driving (fallback): {len(travel_times.get('driving', {}))}")
    print(f"Municipality PT (fallback): {len(travel_times.get('public_transport', {}))}")
    print(f"Prices: {len(prices)}")
    print(f"Taxes: {len(taxes)}")

    scored = compute_scores(municipalities, settlements, settlement_mapping, settlement_drive, settlement_pt, travel_times, prices, taxes, pt_breakdown)
    export_geojson(scored)


if __name__ == "__main__":
    main()
