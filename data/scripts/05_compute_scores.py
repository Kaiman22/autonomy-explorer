#!/usr/bin/env python3
"""
Step 5: Compute autonomy upside scores and export final GeoJSON for frontend.

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

    return municipalities, travel_times, prices, taxes


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
    best_today = min(manual_driving, PT_comfort)
    AV_comfort = drive_time × av_factor

    High gain = today's best option is much worse than AV would be.
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
    Lower value = better current accessibility.
    """
    if comfort is None:
        comfort = COMFORT

    times = []
    for city_id in CITIES:
        drive_s = driving_times.get(city_id)
        pt_s = pt_times.get(city_id)

        if drive_s is not None:
            drive_min = drive_s / 60.0  # manual driving = full burden
        else:
            drive_min = None

        if pt_s is not None:
            pt_min = (pt_s / 60.0) * comfort["oev_sitting_factor"]
        else:
            pt_min = None

        # Best of the two modes today
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


def compute_scores(municipalities, travel_times, prices, taxes):
    """Compute the autonomy upside score for each municipality."""
    muni_ids = sorted(municipalities.keys())
    driving = travel_times.get("driving", {})
    pt = travel_times.get("public_transport", {})

    # --- Sub-score 1: Accessibility Gain ---
    raw_gains = []
    for mid in muni_ids:
        d = driving.get(mid, {})
        p = pt.get(mid, {})
        gains = compute_accessibility_gain(d, p)
        valid_gains = [g for g in gains.values() if g is not None]
        if valid_gains:
            raw_gains.append(statistics.mean(valid_gains))
        else:
            raw_gains.append(None)

    norm_gains = normalize_values(raw_gains)

    # --- Sub-score 2: Inherent Attractiveness ---
    # = price_per_m2 / status_quo_accessibility
    # High value = expensive despite poor transport = inherently desirable
    raw_attractiveness = []
    raw_status_quo = []
    for mid in muni_ids:
        d = driving.get(mid, {})
        p = pt.get(mid, {})
        sq = compute_status_quo_access(d, p)
        raw_status_quo.append(sq)

        price_data = prices.get(mid)
        if price_data and price_data.get("chf_per_m2") and sq and sq > 0:
            raw_attractiveness.append(price_data["chf_per_m2"] / sq)
        else:
            raw_attractiveness.append(None)

    norm_attract = normalize_values(raw_attractiveness)

    # --- Combined Score ---
    w = SCORING_WEIGHTS
    scored = []
    for i, mid in enumerate(muni_ids):
        m = municipalities[mid]
        d = driving.get(mid, {})
        p = pt.get(mid, {})
        gains = compute_accessibility_gain(d, p)

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
        pt_times_list = [p.get(c) for c in CITIES if p.get(c) is not None]

        price_data = prices.get(mid)
        tax_data = taxes.get(mid)

        scored.append({
            "id": mid,
            "name": m["name"],
            "canton": m["canton"],
            "canton_code": m.get("canton_code", ""),
            "lat": m["lat"],
            "lon": m["lon"],
            # Travel times (seconds)
            "drive_times": {c: d.get(c) for c in CITIES},
            "pt_times": {c: p.get(c) for c in CITIES},
            # Min times
            "min_drive_s": min(drive_times_list) if drive_times_list else None,
            "min_pt_s": min(pt_times_list) if pt_times_list else None,
            # Accessibility gain per city (comfort-weighted minutes)
            "gain_per_city": {k: round(v, 1) if v else None for k, v in gains.items()},
            "best_city": best_city,
            # Price
            "chf_per_m2": price_data.get("chf_per_m2") if price_data else None,
            # Tax
            "tax_multiplier": tax_data.get("multiplier") if tax_data else None,
            # Status-quo accessibility (avg best travel time in minutes)
            "status_quo_access": round(raw_status_quo[i], 1) if raw_status_quo[i] else None,
            # Raw inherent attractiveness (CHF/m² per minute of accessibility)
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
    print(f"  {len(features)} features")

    scores = [s["autonomy_score"] for s in scored if s["autonomy_score"] is not None]
    if scores:
        print(f"  Score range: {min(scores):.1f} - {max(scores):.1f}")
        print(f"  Score median: {statistics.median(scores):.1f}")
        print(f"  Score mean: {statistics.mean(scores):.1f}")

    return geojson


def main():
    municipalities, travel_times, prices, taxes = load_data()
    print(f"Municipalities: {len(municipalities)}")
    print(f"Driving times: {len(travel_times.get('driving', {}))}")
    print(f"PT times: {len(travel_times.get('public_transport', {}))}")
    print(f"Prices: {len(prices)}")
    print(f"Taxes: {len(taxes)}")

    scored = compute_scores(municipalities, travel_times, prices, taxes)
    export_geojson(scored)


if __name__ == "__main__":
    main()
