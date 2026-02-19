#!/usr/bin/env python3
"""
Step 5: Compute autonomy upside scores and export final GeoJSON for frontend.

Outputs settlement-level points (~3,966) using swissNAMES3D settlement locations.
These are actual village/town center points — much better than PLZ polygon centroids.
Each settlement has its own travel times but inherits municipality-level
prices and taxes.

Scoring model (2 components):
  1. Accessibility Gain: how much AV improves connectivity vs status quo
     (PT comfort time - AV comfort time, averaged across enabled cities)
  2. Inherent Attractiveness: price / status_quo_accessibility
     (expensive despite poor current transport = people love it for
      inherent reasons like nature, tax, culture, safety — all priced in)

Outputs: frontend/public/data/municipalities_scored.geojson
"""
import json
import statistics

from config import (
    CITIES,
    COMFORT,
    FRONTEND_DATA_DIR,
    PROCESSED_DIR,
    SCORING_WEIGHTS,
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

    return municipalities, settlements, settlement_mapping, settlement_drive, settlement_pt, travel_times, prices, taxes


def compute_comfort_time(raw_seconds, mode, comfort=None):
    """Apply comfort weighting to raw travel time."""
    if raw_seconds is None:
        return None
    if comfort is None:
        comfort = COMFORT

    minutes = raw_seconds / 60.0

    if mode == "driving_av":
        return minutes * comfort["av_factor"]
    elif mode == "driving_manual":
        return minutes
    elif mode == "public_transport":
        return minutes * comfort["oev_sitting_factor"]
    return minutes


def compute_accessibility_gain(driving_times, pt_times, comfort=None):
    """
    Compute accessibility gain per city.
    Gain = best_today - AV_comfort
    """
    if comfort is None:
        comfort = COMFORT

    gains = {}
    for city_id in CITIES:
        drive_s = driving_times.get(city_id)
        pt_s = pt_times.get(city_id)

        if drive_s is None or pt_s is None:
            gains[city_id] = None
            continue

        human_drive = compute_comfort_time(drive_s, "driving_manual", comfort)
        pt_comfort = compute_comfort_time(pt_s, "public_transport", comfort)
        best_today = min(human_drive, pt_comfort)
        av_comfort = compute_comfort_time(drive_s, "driving_av", comfort)
        gains[city_id] = best_today - av_comfort

    return gains


def compute_status_quo_access(driving_times, pt_times, comfort=None):
    """
    Compute status-quo accessibility (without AV).
    = average of best(manual_drive, PT_comfort) across all cities.
    """
    if comfort is None:
        comfort = COMFORT

    times = []
    for city_id in CITIES:
        drive_s = driving_times.get(city_id)
        pt_s = pt_times.get(city_id)

        if drive_s is not None:
            drive_min = drive_s / 60.0
        else:
            drive_min = None

        if pt_s is not None:
            pt_min = (pt_s / 60.0) * comfort["oev_sitting_factor"]
        else:
            pt_min = None

        valid = [t for t in [drive_min, pt_min] if t is not None]
        if valid:
            times.append(min(valid))

    return statistics.mean(times) if times else None


def normalize_values(values, invert=False):
    """Normalize a list of values to 0-100 scale. Higher = better."""
    valid = [v for v in values if v is not None]
    if not valid or max(valid) == min(valid):
        return [50 if v is not None else None for v in values]

    lo, hi = min(valid), max(valid)
    result = []
    for v in values:
        if v is None:
            result.append(None)
        else:
            normalized = (v - lo) / (hi - lo) * 100
            if invert:
                normalized = 100 - normalized
            result.append(round(normalized, 1))
    return result


def compute_scores(municipalities, settlements, settlement_mapping, settlement_drive, settlement_pt, travel_times, prices, taxes):
    """Compute the autonomy upside score for each settlement point."""
    muni_driving = travel_times.get("driving", {})
    muni_pt = travel_times.get("public_transport", {})

    # Build settlement features: each settlement gets its own travel times
    # but inherits municipality-level prices/taxes
    features = []
    for s in settlements:
        uuid = s["uuid"]
        muni_id = s.get("municipality_id")

        # Travel times: use settlement-level if available, else fall back to municipality
        d = settlement_drive.get(uuid, {})
        pt = settlement_pt.get(uuid, {})

        # If settlement-level times are missing, fall back to municipality-level
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

    # --- Sub-score 1: Accessibility Gain ---
    raw_gains = []
    for sf in features:
        gains = compute_accessibility_gain(sf["driving"], sf["pt"])
        valid_gains = [g for g in gains.values() if g is not None]
        if valid_gains:
            raw_gains.append(statistics.mean(valid_gains))
        else:
            raw_gains.append(None)

    norm_gains = normalize_values(raw_gains)

    # --- Sub-score 2: Inherent Attractiveness ---
    raw_attractiveness = []
    raw_status_quo = []
    for sf in features:
        sq = compute_status_quo_access(sf["driving"], sf["pt"])
        raw_status_quo.append(sq)

        pd = sf["price_data"]
        if pd and pd.get("chf_per_m2") and sq and sq > 0:
            raw_attractiveness.append(pd["chf_per_m2"] / sq)
        else:
            raw_attractiveness.append(None)

    norm_attract = normalize_values(raw_attractiveness)

    # --- Combined Score ---
    w = SCORING_WEIGHTS
    scored = []
    for i, sf in enumerate(features):
        d = sf["driving"]
        pt = sf["pt"]
        gains = compute_accessibility_gain(d, pt)

        components = {
            "accessibility_gain": norm_gains[i],
            "inherent_attractiveness": norm_attract[i],
        }

        valid_components = {
            k: v for k, v in components.items() if v is not None
        }

        if valid_components:
            total_weight = sum(w[k] for k in valid_components)
            if total_weight > 0:
                score = sum(
                    v * w[k] / total_weight
                    for k, v in valid_components.items()
                )
            else:
                score = None
        else:
            score = None

        # Find best city (highest gain)
        best_city = None
        best_gain = -float("inf")
        for city_id, gain in gains.items():
            if gain is not None and gain > best_gain:
                best_gain = gain
                best_city = city_id

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
            # Travel times (seconds)
            "drive_times": {c: d.get(c) for c in CITIES},
            "pt_times": {c: pt.get(c) for c in CITIES},
            # Min times
            "min_drive_s": min(drive_times_list) if drive_times_list else None,
            "min_pt_s": min(pt_times_list) if pt_times_list else None,
            # Accessibility gain per city (comfort-weighted minutes)
            "gain_per_city": {k: round(v, 1) if v else None for k, v in gains.items()},
            "best_city": best_city,
            # Price (from municipality)
            "chf_per_m2": price_data.get("chf_per_m2") if price_data else None,
            # Tax (from municipality)
            "tax_multiplier": tax_data.get("multiplier") if tax_data else None,
            # Status-quo accessibility
            "status_quo_access": round(raw_status_quo[i], 1) if raw_status_quo[i] else None,
            # Raw inherent attractiveness
            "inherent_attractiveness_raw": round(raw_attractiveness[i], 1) if raw_attractiveness[i] else None,
            # Sub-scores (0-100)
            "score_accessibility": components["accessibility_gain"],
            "score_attractiveness": components["inherent_attractiveness"],
            # Final score (0-100)
            "autonomy_score": round(score, 1) if score is not None else None,
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

    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "cities": {k: v["name"] for k, v in CITIES.items()},
            "scoring_weights": SCORING_WEIGHTS,
            "comfort_factors": COMFORT,
        },
        "features": features,
    }

    out_path = FRONTEND_DATA_DIR / "municipalities_scored.geojson"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)
    print(f"Saved scored GeoJSON to {out_path}")
    print(f"  {len(features)} features (settlement-level points)")

    scores = [s["autonomy_score"] for s in scored if s["autonomy_score"] is not None]
    if scores:
        print(f"  Score range: {min(scores):.1f} - {max(scores):.1f}")
        print(f"  Score median: {statistics.median(scores):.1f}")
        print(f"  Score mean: {statistics.mean(scores):.1f}")

    # Print municipality coverage
    munis = set(s["municipality_id"] for s in scored if s["municipality_id"])
    print(f"  Covering {len(munis)} municipalities")

    return geojson


def main():
    municipalities, settlements, settlement_mapping, settlement_drive, settlement_pt, travel_times, prices, taxes = load_data()
    print(f"Municipalities: {len(municipalities)}")
    print(f"Settlement points: {len(settlements)}")
    print(f"Settlement driving times: {len(settlement_drive)}")
    print(f"Settlement PT times: {len(settlement_pt)}")
    print(f"Municipality driving (fallback): {len(travel_times.get('driving', {}))}")
    print(f"Municipality PT (fallback): {len(travel_times.get('public_transport', {}))}")
    print(f"Prices: {len(prices)}")
    print(f"Taxes: {len(taxes)}")

    scored = compute_scores(municipalities, settlements, settlement_mapping, settlement_drive, settlement_pt, travel_times, prices, taxes)
    export_geojson(scored)


if __name__ == "__main__":
    main()
