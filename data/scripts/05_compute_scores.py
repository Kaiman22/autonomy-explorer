#!/usr/bin/env python3
"""
Step 5: Compute autonomy upside scores and export final GeoJSON for frontend.

Now outputs PLZ-level points (3,181) instead of municipality centroids (2,128).
Each PLZ point has its own travel times but inherits municipality-level
prices and taxes. This gives a finer-grained visualization where points
sit closer to where people actually live.

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

    with open(PROCESSED_DIR / "plz_points.json") as f:
        plz_points = json.load(f)

    with open(PROCESSED_DIR / "plz_municipality_map.json") as f:
        mapping = json.load(f)

    plz_drive = {}
    plz_drive_path = PROCESSED_DIR / "plz_travel_times_driving.json"
    if plz_drive_path.exists():
        with open(plz_drive_path) as f:
            plz_drive = json.load(f)

    plz_pt = {}
    plz_pt_path = PROCESSED_DIR / "plz_travel_times_pt.json"
    if plz_pt_path.exists():
        with open(plz_pt_path) as f:
            plz_pt = json.load(f)

    # Also load municipality-level travel times as fallback
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

    return municipalities, plz_points, mapping, plz_drive, plz_pt, travel_times, prices, taxes


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


def compute_scores(municipalities, plz_points, mapping, plz_drive, plz_pt, travel_times, prices, taxes):
    """Compute the autonomy upside score for each PLZ point."""
    plz_to_munis = mapping["plz_to_municipalities"]
    muni_driving = travel_times.get("driving", {})
    muni_pt = travel_times.get("public_transport", {})

    # Build list of PLZ features: each PLZ gets its own travel times
    # but inherits municipality-level prices/taxes from its PRIMARY municipality
    plz_features = []
    for p in plz_points:
        plz_code = p["plz"]

        # Travel times: use PLZ-level if available, else fall back to municipality
        d = plz_drive.get(plz_code, {})
        pt = plz_pt.get(plz_code, {})

        # Primary municipality for this PLZ (first in the mapping)
        muni_ids = plz_to_munis.get(plz_code, [])
        primary_muni = muni_ids[0] if muni_ids else None

        # If PLZ-level times are missing, fall back to municipality-level
        if not d and primary_muni:
            d = muni_driving.get(primary_muni, {})
        if not pt and primary_muni:
            pt = muni_pt.get(primary_muni, {})

        # Municipality data (prices, taxes, name, canton)
        muni = municipalities.get(primary_muni, {}) if primary_muni else {}
        price_data = prices.get(primary_muni) if primary_muni else None
        tax_data = taxes.get(primary_muni) if primary_muni else None

        plz_features.append({
            "plz": plz_code,
            "municipality_id": primary_muni,
            "all_municipality_ids": muni_ids,
            "name": muni.get("name", p.get("name", "")),
            "canton": muni.get("canton", p.get("canton", "")),
            "canton_code": muni.get("canton_code", p.get("canton_code", "")),
            "lat": p["lat"],
            "lon": p["lon"],
            "driving": d,
            "pt": pt,
            "price_data": price_data,
            "tax_data": tax_data,
        })

    # --- Sub-score 1: Accessibility Gain ---
    raw_gains = []
    for pf in plz_features:
        gains = compute_accessibility_gain(pf["driving"], pf["pt"])
        valid_gains = [g for g in gains.values() if g is not None]
        if valid_gains:
            raw_gains.append(statistics.mean(valid_gains))
        else:
            raw_gains.append(None)

    norm_gains = normalize_values(raw_gains)

    # --- Sub-score 2: Inherent Attractiveness ---
    raw_attractiveness = []
    raw_status_quo = []
    for pf in plz_features:
        sq = compute_status_quo_access(pf["driving"], pf["pt"])
        raw_status_quo.append(sq)

        pd = pf["price_data"]
        if pd and pd.get("chf_per_m2") and sq and sq > 0:
            raw_attractiveness.append(pd["chf_per_m2"] / sq)
        else:
            raw_attractiveness.append(None)

    norm_attract = normalize_values(raw_attractiveness)

    # --- Combined Score ---
    w = SCORING_WEIGHTS
    scored = []
    for i, pf in enumerate(plz_features):
        d = pf["driving"]
        pt = pf["pt"]
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

        price_data = pf["price_data"]
        tax_data = pf["tax_data"]

        scored.append({
            "id": f"plz_{pf['plz']}",
            "plz": pf["plz"],
            "municipality_id": pf["municipality_id"],
            "name": pf["name"],
            "canton": pf["canton"],
            "canton_code": pf["canton_code"],
            "lat": pf["lat"],
            "lon": pf["lon"],
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
    print(f"  {len(features)} features (PLZ-level points)")

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
    municipalities, plz_points, mapping, plz_drive, plz_pt, travel_times, prices, taxes = load_data()
    print(f"Municipalities: {len(municipalities)}")
    print(f"PLZ points: {len(plz_points)}")
    print(f"PLZ driving times: {len(plz_drive)}")
    print(f"PLZ PT times: {len(plz_pt)}")
    print(f"Municipality driving (fallback): {len(travel_times.get('driving', {}))}")
    print(f"Municipality PT (fallback): {len(travel_times.get('public_transport', {}))}")
    print(f"Prices: {len(prices)}")
    print(f"Taxes: {len(taxes)}")

    scored = compute_scores(municipalities, plz_points, mapping, plz_drive, plz_pt, travel_times, prices, taxes)
    export_geojson(scored)


if __name__ == "__main__":
    main()
