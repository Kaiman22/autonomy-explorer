#!/usr/bin/env python3
"""
Step 5: Compute autonomy upside scores and export final GeoJSON for frontend.

Outputs settlement-level points (~3,966) using swissNAMES3D settlement locations.
These are actual village/town center points — much better than PLZ polygon centroids.
Each settlement has its own travel times but inherits municipality-level
prices and taxes.

Scoring model (2 components):
  1. Accessibility Gain: how much AV improves connectivity vs status quo
     Bottleneck (worst city) aggregation; both relative (%) and absolute (min).
  2. Inherent Attractiveness: peer-group price percentile
     Among places with similar commute time, how expensive is this place?
     High = desirable for non-transport reasons (nature, prestige, culture).

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
    PT_WALK_DEDUCTION_S,
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


def deduct_pt_walking(pt_seconds, pop_category=None):
    """
    Subtract the estimated origin walking time from a PT travel time.
    Walking to the first PT stop is noise (depends on centroid placement)
    and disproportionately inflates PT times because walking is slow.
    Deduction varies by population density: urban areas have nearby stops.
    Returns the adjusted PT time in seconds, floored at 0.
    """
    if pt_seconds is None:
        return None
    deduction = PT_WALK_DEDUCTION_S.get(pop_category, PT_WALK_DEDUCTION_S["default"])
    return max(0, pt_seconds - deduction)


def compute_accessibility_gain(driving_times, pt_times, pop_category=None, comfort=None):
    """
    Compute accessibility gain per city.
    Gain = best_today - best_with_AV

    best_today  = min(manual_drive, PT×comfort)
    best_with_AV = min(AV_drive, PT×comfort)  — PT remains an option post-AV

    PT times have the walking segment deducted before comfort weighting.
    This matches the frontend recomputeScores logic exactly.
    """
    if comfort is None:
        comfort = COMFORT

    gains = {}
    for city_id in CITIES:
        drive_s = driving_times.get(city_id)
        pt_s = deduct_pt_walking(pt_times.get(city_id), pop_category)

        # Need at least one mode for each scenario
        today_candidates = []
        av_candidates = []

        if drive_s is not None:
            today_candidates.append(compute_comfort_time(drive_s, "driving_manual", comfort))
            av_candidates.append(compute_comfort_time(drive_s, "driving_av", comfort))
        if pt_s is not None:
            pt_comfort = compute_comfort_time(pt_s, "public_transport", comfort)
            today_candidates.append(pt_comfort)
            av_candidates.append(pt_comfort)

        if not today_candidates or not av_candidates:
            gains[city_id] = None
            continue

        best_today = min(today_candidates)
        best_with_av = min(av_candidates)
        gains[city_id] = best_today - best_with_av

    return gains


def compute_status_quo_access(driving_times, pt_times, pop_category=None, comfort=None):
    """
    Compute status-quo accessibility (without AV).
    = MAX of best(manual_drive, PT×comfort) across all cities.

    Uses bottleneck (worst city) so accessibility = ability to reach ALL targets.
    Adding a city can only make the score worse, shrinking the high-accessibility
    region to the intersection rather than growing it as a union.

    PT times have walking deducted before comfort weighting.
    Matches frontend recomputeScores logic: manual drive factor = 1.0.
    """
    if comfort is None:
        comfort = COMFORT

    times = []
    for city_id in CITIES:
        drive_s = driving_times.get(city_id)
        pt_s = deduct_pt_walking(pt_times.get(city_id), pop_category)

        candidates = []
        if drive_s is not None:
            candidates.append(drive_s / 60.0)  # manual drive: factor 1.0
        if pt_s is not None:
            candidates.append((pt_s / 60.0) * comfort["oev_sitting_factor"])

        if candidates:
            times.append(min(candidates))

    return max(times) if times else None


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

    # --- Sub-score 1: Accessibility Gain (relative %) ---
    # Relative gain = (SQ - AV) / SQ × 100  — what % of commute pain does AV eliminate?
    # This doesn't bias towards remote areas like absolute gain does.
    # Uses bottleneck (worst city) for both SQ and AV.
    raw_rel_gains = []
    raw_abs_gains = []
    raw_post_av = []
    for sf in features:
        pop_cat = sf.get("pop_category")
        sq = compute_status_quo_access(sf["driving"], sf["pt"], pop_cat)
        av_times = []
        for city_id in CITIES:
            drive_s = sf["driving"].get(city_id)
            pt_s = deduct_pt_walking(sf["pt"].get(city_id), pop_cat)
            av_candidates = []
            if drive_s is not None:
                av_candidates.append(compute_comfort_time(drive_s, "driving_av"))
            if pt_s is not None:
                av_candidates.append(compute_comfort_time(pt_s, "public_transport"))
            if av_candidates:
                av_times.append(min(av_candidates))
        av_bottleneck = max(av_times) if av_times else None
        raw_post_av.append(av_bottleneck)
        if sq is not None and av_bottleneck is not None and sq > 0:
            raw_abs_gains.append(sq - av_bottleneck)
            raw_rel_gains.append(((sq - av_bottleneck) / sq) * 100)
        else:
            raw_abs_gains.append(None)
            raw_rel_gains.append(None)

    norm_rel_gains = normalize_values(raw_rel_gains)
    norm_abs_gains = normalize_values(raw_abs_gains)

    # --- Sub-score 2: Inherent Attractiveness (peer-group percentile) ---
    # For each municipality: find all places with similar status-quo accessibility
    # (within ±15% of commute time, min ±5 min), then ask "what % of peers are
    # cheaper?" High percentile = expensive among peers = inherently desirable.
    # Matches the frontend recomputeScores logic exactly.
    raw_attractiveness = []
    raw_status_quo = []
    sq_price_pairs = []  # (index, sq, price)

    for i, sf in enumerate(features):
        pop_cat = sf.get("pop_category")
        sq = compute_status_quo_access(sf["driving"], sf["pt"], pop_cat)
        raw_status_quo.append(sq)

        pd = sf["price_data"]
        price = pd.get("chf_per_m2") if pd else None
        if price and sq and price > 0:
            sq_price_pairs.append((i, sq, price))

    # Compute peer-group percentile
    raw_attractiveness = [None] * len(features)
    if len(sq_price_pairs) > 10:
        sorted_pairs = sorted(sq_price_pairs, key=lambda x: x[1])
        for idx, sq, price in sq_price_pairs:
            # Adaptive bandwidth: start at ±15%, widen if too few peers
            peer_prices = []
            pct = 0.15
            while pct <= 0.50:
                margin = max(5, sq * pct)
                lo, hi = sq - margin, sq + margin
                peer_prices = [p for _, s, p in sorted_pairs if lo <= s <= hi]
                if len(peer_prices) >= 10:
                    break
                pct += 0.10
            if len(peer_prices) >= 5:
                cheaper = sum(1 for p in peer_prices if p < price)
                pctile = (cheaper / len(peer_prices)) * 100
                raw_attractiveness[idx] = pctile

    norm_attract = normalize_values(raw_attractiveness)

    # Normalize SQ and post-AV (inverted: lower minutes = better = higher score)
    # Use shared min/max for visual comparability (same as frontend)
    all_access = [v for v in raw_status_quo + raw_post_av if v is not None]
    if all_access:
        access_lo, access_hi = min(all_access), max(all_access)
        access_range = access_hi - access_lo if access_hi != access_lo else 1
        norm_sq = [round(((access_hi - v) / access_range) * 100, 1) if v is not None else None for v in raw_status_quo]
        norm_post_av = [round(((access_hi - v) / access_range) * 100, 1) if v is not None else None for v in raw_post_av]
    else:
        norm_sq = [None] * len(features)
        norm_post_av = [None] * len(features)

    # --- Combined Score ---
    w = SCORING_WEIGHTS
    scored = []
    for i, sf in enumerate(features):
        d = sf["driving"]
        pt = sf["pt"]
        gains = compute_accessibility_gain(d, pt, sf.get("pop_category"))

        # Two compound scores: one with relative gain, one with absolute
        price_data = sf["price_data"]
        has_price = price_data and price_data.get("chf_per_m2") is not None

        def weighted_score(gain_val, attract_val):
            comps = {}
            if gain_val is not None:
                comps["accessibility_gain"] = gain_val
            if attract_val is not None:
                comps["inherent_attractiveness"] = attract_val
            if not has_price or not comps:
                return None
            tw = sum(w[k] for k in comps)
            return sum(v * w[k] / tw for k, v in comps.items()) if tw > 0 else None

        score_rel = weighted_score(norm_rel_gains[i], norm_attract[i])
        score_abs = weighted_score(norm_abs_gains[i], norm_attract[i])

        # Find best city (highest gain)
        best_city = None
        best_gain = -float("inf")
        for city_id, gain in gains.items():
            if gain is not None and gain > best_gain:
                best_gain = gain
                best_city = city_id

        drive_times_list = [d.get(c) for c in CITIES if d.get(c) is not None]
        pt_times_list = [pt.get(c) for c in CITIES if pt.get(c) is not None]

        # Average raw car and PT access across all 10 cities (minutes, no comfort)
        pop_cat = sf.get("pop_category")
        car_times_min = []
        pt_times_min = []
        for city_id in CITIES:
            ds = d.get(city_id)
            ps = deduct_pt_walking(pt.get(city_id), pop_cat)
            if ds is not None:
                car_times_min.append(ds / 60.0)
            if ps is not None:
                pt_times_min.append(ps / 60.0)

        avg_car = sum(car_times_min) / len(car_times_min) if car_times_min else None
        avg_pt = sum(pt_times_min) / len(pt_times_min) if pt_times_min else None

        if avg_car is not None and avg_pt is not None:
            delta_min = avg_car - avg_pt
            avg_mid = (avg_car + avg_pt) / 2
            delta_pct = (delta_min / avg_mid) * 100 if avg_mid > 0 else None
        else:
            delta_min = None
            delta_pct = None

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
            "gain_per_city": {k: round(v, 1) if v is not None else None for k, v in gains.items()},
            "best_city": best_city,
            # Price (from municipality)
            "chf_per_m2": price_data.get("chf_per_m2") if price_data else None,
            # Tax (from municipality)
            "tax_multiplier": tax_data.get("multiplier") if tax_data else None,
            # Status-quo accessibility (raw minutes, bottleneck)
            "status_quo_access": round(raw_status_quo[i], 1) if raw_status_quo[i] else None,
            # Post-AV accessibility (raw minutes, bottleneck)
            "post_av_access": round(raw_post_av[i], 1) if raw_post_av[i] else None,
            # Absolute delta and relative gain (raw values)
            "delta_accessibility": round(raw_abs_gains[i], 1) if raw_abs_gains[i] is not None else None,
            "relative_gain_pct": round(raw_rel_gains[i], 1) if raw_rel_gains[i] is not None else None,
            # Raw inherent attractiveness
            "inherent_attractiveness_raw": round(raw_attractiveness[i], 1) if raw_attractiveness[i] else None,
            # Average raw travel times (minutes, across all 10 cities)
            "avg_car_access": round(avg_car, 1) if avg_car is not None else None,
            "avg_pt_access": round(avg_pt, 1) if avg_pt is not None else None,
            "car_pt_delta_min": round(delta_min, 1) if delta_min is not None else None,
            "car_pt_delta_pct": round(delta_pct, 1) if delta_pct is not None else None,
            # Sub-scores (0-100)
            "score_rel_gain": norm_rel_gains[i],
            "score_abs_delta": norm_abs_gains[i],
            "score_attractiveness": norm_attract[i],
            "score_status_quo": norm_sq[i],
            "score_post_av": norm_post_av[i],
            # Final scores (0-100) — two variants
            "autonomy_score_rel": round(score_rel, 1) if score_rel is not None else None,
            "autonomy_score_abs": round(score_abs, 1) if score_abs is not None else None,
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

    # Compute data freshness from price timestamps
    price_timestamps = []
    for s in scored:
        # price_data has fetched_at if scraped with updated script
        pass  # timestamps are in the raw price files, not in scored output

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
            "scoring_weights": SCORING_WEIGHTS,
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

    scores_rel = [s["autonomy_score_rel"] for s in scored if s["autonomy_score_rel"] is not None]
    scores_abs = [s["autonomy_score_abs"] for s in scored if s["autonomy_score_abs"] is not None]
    if scores_rel:
        print(f"  Score (rel) range: {min(scores_rel):.1f} - {max(scores_rel):.1f}  median: {statistics.median(scores_rel):.1f}")
    if scores_abs:
        print(f"  Score (abs) range: {min(scores_abs):.1f} - {max(scores_abs):.1f}  median: {statistics.median(scores_abs):.1f}")

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
