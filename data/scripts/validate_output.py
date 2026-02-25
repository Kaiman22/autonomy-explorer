#!/usr/bin/env python3
"""
Validate the output GeoJSON for data quality.

Checks:
  - Feature count (>3000 settlements expected)
  - Score coverage (>40% should have compound score)
  - Price coverage (>30% should have price data)
  - Travel time coverage (>90% should have driving times)
  - Municipality coverage (>2000 unique municipalities)
  - Coordinate sanity (all points within Switzerland bounding box)
  - No NaN or infinity values in numeric fields
  - Score ranges (0-100 for normalized scores)

Usage:
  python validate_output.py                    # validate default output
  python validate_output.py path/to/file.geojson  # validate specific file

Exit code: 0 if all checks pass, 1 if any check fails.
"""
import json
import math
import sys

from config import FRONTEND_DATA_DIR

# Switzerland bounding box (generous)
CH_LAT_MIN, CH_LAT_MAX = 45.5, 48.0
CH_LON_MIN, CH_LON_MAX = 5.5, 10.8


def validate(path):
    """Run all validation checks. Returns (passed, failed) lists."""
    passed = []
    failed = []

    def check(name, condition, detail=""):
        if condition:
            passed.append(name)
            print(f"  PASS: {name}" + (f" ({detail})" if detail else ""))
        else:
            failed.append(name)
            print(f"  FAIL: {name}" + (f" ({detail})" if detail else ""))

    print(f"Validating: {path}")

    try:
        with open(path) as f:
            geojson = json.load(f)
    except Exception as e:
        print(f"  FAIL: Could not load file: {e}")
        return [], ["file_load"]

    features = geojson.get("features", [])
    n = len(features)

    # --- Feature count ---
    check("feature_count", n > 3000, f"{n} features")

    # --- Score coverage ---
    with_score_rel = sum(1 for f in features if f["properties"].get("autonomy_score_rel") is not None)
    with_score_abs = sum(1 for f in features if f["properties"].get("autonomy_score_abs") is not None)
    pct_rel = with_score_rel / n * 100 if n else 0
    check("score_coverage_rel", pct_rel > 40, f"{with_score_rel}/{n} ({pct_rel:.1f}%)")
    check("score_coverage_abs", with_score_abs > 0, f"{with_score_abs} with absolute score")

    # --- Price coverage ---
    with_price = sum(1 for f in features if f["properties"].get("chf_per_m2") is not None)
    ppct = with_price / n * 100 if n else 0
    check("price_coverage", ppct > 30, f"{with_price}/{n} ({ppct:.1f}%)")

    # --- Travel time coverage ---
    with_drive = sum(1 for f in features if f["properties"].get("min_drive_s") is not None)
    dpct = with_drive / n * 100 if n else 0
    check("drive_coverage", dpct > 90, f"{with_drive}/{n} ({dpct:.1f}%)")

    with_pt = sum(1 for f in features if f["properties"].get("min_pt_s") is not None)
    ptpct = with_pt / n * 100 if n else 0
    check("pt_coverage", ptpct > 50, f"{with_pt}/{n} ({ptpct:.1f}%)")

    # --- Municipality coverage ---
    munis = set(f["properties"].get("municipality_id") for f in features if f["properties"].get("municipality_id"))
    check("municipality_count", len(munis) > 2000, f"{len(munis)} municipalities")

    # --- Coordinate sanity ---
    bad_coords = 0
    for f in features:
        coords = f.get("geometry", {}).get("coordinates", [])
        if len(coords) >= 2:
            lon, lat = coords[0], coords[1]
            if not (CH_LAT_MIN <= lat <= CH_LAT_MAX and CH_LON_MIN <= lon <= CH_LON_MAX):
                bad_coords += 1
    check("coordinates_in_switzerland", bad_coords == 0, f"{bad_coords} outside CH")

    # --- No NaN/Infinity ---
    numeric_fields = [
        "autonomy_score_rel", "autonomy_score_abs", "chf_per_m2",
        "score_rel_gain", "score_abs_delta", "score_attractiveness",
        "min_drive_s", "min_pt_s", "status_quo_access",
    ]
    nan_count = 0
    for f in features:
        for field in numeric_fields:
            val = f["properties"].get(field)
            if val is not None and isinstance(val, float):
                if math.isnan(val) or math.isinf(val):
                    nan_count += 1
    check("no_nan_infinity", nan_count == 0, f"{nan_count} NaN/Inf values")

    # --- Score ranges ---
    score_fields = ["autonomy_score_rel", "autonomy_score_abs", "score_rel_gain", "score_abs_delta", "score_attractiveness"]
    out_of_range = 0
    for f in features:
        for field in score_fields:
            val = f["properties"].get(field)
            if val is not None and (val < 0 or val > 100):
                out_of_range += 1
    check("scores_in_range", out_of_range == 0, f"{out_of_range} scores outside 0-100")

    # --- Metadata ---
    meta = geojson.get("metadata", {})
    check("has_metadata", bool(meta.get("cities")), f"cities: {list(meta.get('cities', {}).keys())[:3]}...")
    check("has_generated_at", bool(meta.get("generated_at")), meta.get("generated_at", "missing"))

    return passed, failed


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = FRONTEND_DATA_DIR / "municipalities_scored.geojson"

    passed, failed = validate(path)

    print(f"\nResults: {len(passed)} passed, {len(failed)} failed")
    if failed:
        print(f"FAILED checks: {', '.join(failed)}")
        sys.exit(1)
    else:
        print("All checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
