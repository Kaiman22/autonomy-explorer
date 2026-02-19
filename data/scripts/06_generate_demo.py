#!/usr/bin/env python3
"""
Step 6 (optional): Generate demo data for frontend development.
Creates municipalities_scored.geojson with synthetic travel times
when real travel time data is not yet available.

Scoring model (2 components):
  1. Accessibility Gain: how much AV improves connectivity vs status quo
  2. Inherent Attractiveness: price / status_quo_accessibility
     (expensive despite poor transport = people love it for other reasons)
"""
import json
import math
import random
import statistics

from config import CITIES, COMFORT, FRONTEND_DATA_DIR, PROCESSED_DIR, SCORING_WEIGHTS


def haversine_km(lat1, lon1, lat2, lon2):
    """Approximate distance in km between two points."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def estimate_drive_time(dist_km):
    """Estimate driving time from distance (rough Swiss roads)."""
    avg_speed = 70
    return dist_km / avg_speed * 3600  # seconds


def estimate_pt_time(dist_km, quality_factor):
    """Estimate PT time from distance with a quality factor."""
    base_time = estimate_drive_time(dist_km)
    return base_time * quality_factor


def normalize_values(values, invert=False):
    """Normalize a list of values to 0-100 scale."""
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


def main():
    with open(PROCESSED_DIR / "municipalities.json") as f:
        municipalities = json.load(f)

    taxes = {}
    tax_path = PROCESSED_DIR / "taxes.json"
    if tax_path.exists():
        with open(tax_path) as f:
            taxes = json.load(f)

    prices = {}
    price_path = PROCESSED_DIR / "prices.json"
    if price_path.exists():
        with open(price_path) as f:
            prices = json.load(f)

    # Generate synthetic prices if no real price data
    if not prices:
        print("No prices.json found — generating synthetic prices...")
        for m in municipalities:
            # Price correlated with proximity to major cities + random
            # Closer to Zurich/Geneva = more expensive
            min_dist = min(
                haversine_km(m["lat"], m["lon"], c["lat"], c["lon"])
                for c in CITIES.values()
            )
            # Base price: 12000 CHF/m² near city, drops to ~3000 at 100km
            base = max(3000, 12000 - min_dist * 90)
            # Add random variation ±30%
            price = base * random.uniform(0.7, 1.3)
            # Tax also affects price: lower tax cantons have higher prices
            t = taxes.get(m["id"])
            if t and t.get("multiplier"):
                # Low tax → price premium (inverse relationship)
                tax_factor = 1 + (200 - min(t["multiplier"], 200)) / 200 * 0.3
                price *= tax_factor
            prices[m["id"]] = {"chf_per_m2": round(price)}

    print(f"Generating demo data for {len(municipalities)} municipalities...")

    random.seed(42)

    # First pass: compute raw values
    raw_data = []
    all_gains = []
    all_attractiveness = []

    for m in municipalities:
        drive_times = {}
        pt_times = {}
        for city_id, city in CITIES.items():
            dist = haversine_km(m["lat"], m["lon"], city["lat"], city["lon"])

            drive_s = estimate_drive_time(dist)
            drive_s *= random.uniform(0.85, 1.15)
            drive_times[city_id] = round(drive_s)

            pt_factor = 1.3 + (dist / 100) * random.uniform(0.5, 1.5)
            pt_s = estimate_pt_time(dist, pt_factor)
            pt_times[city_id] = round(pt_s)

        # Accessibility gain per city: best_today - AV_comfort
        # best_today = min(human_driving, PT_comfort)
        gains = {}
        for city_id in CITIES:
            human_drive = drive_times[city_id] / 60  # full burden
            pt_comfort = pt_times[city_id] / 60 * COMFORT["oev_sitting_factor"]
            best_today = min(human_drive, pt_comfort)
            av_comfort = drive_times[city_id] / 60 * COMFORT["av_factor"]
            gains[city_id] = round(best_today - av_comfort, 1)

        avg_gain = statistics.mean(gains.values())
        all_gains.append(avg_gain)

        # Status-quo accessibility: average of best(drive, PT) across all cities
        # Lower = better accessibility today
        status_quo_times = []
        for city_id in CITIES:
            d = drive_times[city_id] / 60  # minutes
            p = pt_times[city_id] / 60
            # Effective accessibility = best of driving (full burden) and PT (comfort-weighted)
            best = min(d, p * COMFORT["oev_sitting_factor"])
            status_quo_times.append(best)
        # Mean of best times to all cities
        status_quo_access = statistics.mean(status_quo_times) if status_quo_times else None

        price_data = prices.get(m["id"])
        tax_data = taxes.get(m["id"])

        # Inherent attractiveness = price / status_quo_accessibility
        # High price despite poor accessibility = people love it for inherent reasons
        inherent_attract = None
        if price_data and price_data.get("chf_per_m2") and status_quo_access and status_quo_access > 0:
            inherent_attract = price_data["chf_per_m2"] / status_quo_access
        all_attractiveness.append(inherent_attract)

        raw_data.append({
            "m": m,
            "drive_times": drive_times,
            "pt_times": pt_times,
            "gains": gains,
            "avg_gain": avg_gain,
            "status_quo_access": status_quo_access,
            "inherent_attract": inherent_attract,
            "tax_data": tax_data,
            "price_data": price_data,
        })

    # Normalize
    norm_gains = normalize_values(all_gains)
    norm_attract = normalize_values(all_attractiveness)  # higher = more attractive = better

    # Second pass: compute scores
    features = []
    w = SCORING_WEIGHTS
    for i, rd in enumerate(raw_data):
        m = rd["m"]

        score_access = norm_gains[i] if norm_gains[i] is not None else 0
        score_attract = norm_attract[i] if norm_attract[i] is not None else 50

        # Combined
        total_w = w["accessibility_gain"] + w["inherent_attractiveness"]
        total = 0
        if total_w > 0:
            total = (
                score_access * w["accessibility_gain"]
                + score_attract * w["inherent_attractiveness"]
            ) / total_w

        drive_list = list(rd["drive_times"].values())
        pt_list = list(rd["pt_times"].values())
        best_city = max(rd["gains"], key=rd["gains"].get)

        features.append({
            "type": "Feature",
            "properties": {
                "id": m["id"],
                "name": m["name"],
                "canton": m["canton"],
                "canton_code": m.get("canton_code", ""),
                "drive_times": rd["drive_times"],
                "pt_times": rd["pt_times"],
                "min_drive_s": min(drive_list),
                "min_pt_s": min(pt_list),
                "gain_per_city": rd["gains"],
                "best_city": best_city,
                "chf_per_m2": rd["price_data"]["chf_per_m2"] if rd["price_data"] else None,
                "tax_multiplier": rd["tax_data"]["multiplier"] if rd["tax_data"] else None,
                "status_quo_access": round(rd["status_quo_access"], 1) if rd["status_quo_access"] else None,
                "inherent_attractiveness_raw": round(rd["inherent_attract"], 1) if rd["inherent_attract"] else None,
                # Sub-scores (0-100)
                "score_accessibility": round(score_access, 1),
                "score_attractiveness": round(score_attract, 1),
                # Final score (0-100)
                "autonomy_score": round(total, 1),
            },
            "geometry": {
                "type": "Point",
                "coordinates": [m["lon"], m["lat"]],
            },
        })

    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "cities": {k: v["name"] for k, v in CITIES.items()},
            "scoring_weights": SCORING_WEIGHTS,
            "comfort_factors": COMFORT,
            "demo": True,
        },
        "features": features,
    }

    out_path = FRONTEND_DATA_DIR / "municipalities_scored.geojson"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)

    scores = [f["properties"]["autonomy_score"] for f in features]
    attract_scores = [f["properties"]["score_attractiveness"] for f in features if f["properties"]["score_attractiveness"] is not None]
    print(f"Saved {len(features)} features to {out_path}")
    print(f"Autonomy score range: {min(scores):.1f} - {max(scores):.1f}")
    print(f"Autonomy score median: {statistics.median(scores):.1f}")
    if attract_scores:
        print(f"Attractiveness range: {min(attract_scores):.1f} - {max(attract_scores):.1f}")
        print(f"Attractiveness median: {statistics.median(attract_scores):.1f}")
    print(f"File size: {out_path.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
