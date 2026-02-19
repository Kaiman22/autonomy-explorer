#!/usr/bin/env python3
"""
Step 2: Fetch travel times from municipalities to reference cities.

Approaches (tried in order):
  1. TravelTime API (driving + PT, needs TRAVELTIME_APP_ID/TRAVELTIME_API_KEY)
  2. OSRM for driving (--osrm flag: local or public demo server)
  3. Public OSRM demo server (--osrm-public flag, auto-batches, rate-limited)

For public transport, if TravelTime is unavailable, PT times are estimated
from driving times using a Swiss-calibrated model (PT/drive ratio varies
by distance: ~1.2× near cities, ~2.0× for remote areas).

Outputs: data/processed/travel_times.json
"""
import argparse
import json
import math
import time

import requests

from config import (
    ARRIVAL_TIME,
    CITIES,
    MAX_TRAVEL_TIME,
    OSRM_BASE_URL,
    PROCESSED_DIR,
    TRAVELTIME_API_KEY,
    TRAVELTIME_APP_ID,
    TRAVELTIME_BASE_URL,
)

TRAVELTIME_MAX_LOCATIONS = 2000
OSRM_PUBLIC_URL = "https://router.project-osrm.org"
OSRM_BATCH_SIZE = 90  # Public OSRM has ~100 coord limit per request


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


def load_municipalities():
    path = PROCESSED_DIR / "municipalities.json"
    with open(path) as f:
        return json.load(f)


# ─────────────── TravelTime API ───────────────


def build_traveltime_request(municipalities, mode, batch_start, batch_end):
    """Build a TravelTime time-filter request for one batch."""
    batch = municipalities[batch_start:batch_end]

    locations = []
    for city_id, city in CITIES.items():
        locations.append({
            "id": city_id,
            "coords": {"lat": city["lat"], "lng": city["lon"]},
        })
    for m in batch:
        locations.append({
            "id": f"m_{m['id']}",
            "coords": {"lat": m["lat"], "lng": m["lon"]},
        })

    departure_ids = [f"m_{m['id']}" for m in batch]

    searches = []
    for city_id in CITIES:
        searches.append({
            "id": f"to_{city_id}_{batch_start}",
            "arrival_location_id": city_id,
            "departure_location_ids": departure_ids,
            "transportation": {"type": mode},
            "arrival_time": ARRIVAL_TIME,
            "travel_time": MAX_TRAVEL_TIME,
            "properties": ["travel_time"],
        })

    return {"locations": locations, "arrival_searches": searches}


def call_traveltime(payload):
    """Call TravelTime time-filter endpoint."""
    headers = {
        "Content-Type": "application/json",
        "X-Application-Id": TRAVELTIME_APP_ID,
        "X-Api-Key": TRAVELTIME_API_KEY,
    }
    url = f"{TRAVELTIME_BASE_URL}/time-filter"
    resp = requests.post(url, json=payload, headers=headers, timeout=120)
    if resp.status_code == 429:
        print("  Rate limited, waiting 60s...")
        time.sleep(60)
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
    resp.raise_for_status()
    return resp.json()


def fetch_traveltime_mode(municipalities, mode):
    """Fetch travel times for one mode via TravelTime API."""
    results = {}
    n = len(municipalities)

    for batch_start in range(0, n, TRAVELTIME_MAX_LOCATIONS):
        batch_end = min(batch_start + TRAVELTIME_MAX_LOCATIONS, n)
        print(f"  TravelTime {mode}: batch {batch_start}-{batch_end} of {n}")

        payload = build_traveltime_request(municipalities, mode, batch_start, batch_end)
        data = call_traveltime(payload)

        for search_result in data.get("results", []):
            search_id = search_result["search_id"]
            city_id = search_id.split("_")[1]

            for loc in search_result.get("locations", []):
                muni_id = loc["id"].replace("m_", "")
                tt = loc["properties"][0]["travel_time"]
                results.setdefault(muni_id, {})[city_id] = tt

            for unreachable_id in search_result.get("unreachable", []):
                muni_id = unreachable_id.replace("m_", "")
                results.setdefault(muni_id, {})[city_id] = None

        time.sleep(1)

    return results


# ─────────────── OSRM (local or public) ───────────────


def fetch_osrm_batch(munis_batch, city_list, base_url):
    """Fetch one batch of driving times from OSRM Table API."""
    coords_parts = []
    for m in munis_batch:
        coords_parts.append(f"{m['lon']},{m['lat']}")
    for city_id in city_list:
        city = CITIES[city_id]
        coords_parts.append(f"{city['lon']},{city['lat']}")

    coords_str = ";".join(coords_parts)
    n_munis = len(munis_batch)

    sources = ";".join(str(i) for i in range(n_munis))
    destinations = ";".join(str(n_munis + i) for i in range(len(city_list)))

    url = (
        f"{base_url}/table/v1/driving/{coords_str}"
        f"?sources={sources}&destinations={destinations}"
    )

    resp = requests.get(url, timeout=300)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM error: {data.get('message', data.get('code'))}")

    return data["durations"]


def fetch_osrm_driving(municipalities, base_url, batch_size=None):
    """Fetch driving times using OSRM Table API with batching."""
    is_public = "project-osrm.org" in base_url
    if batch_size is None:
        batch_size = OSRM_BATCH_SIZE if is_public else len(municipalities)

    print(f"  Fetching driving times from OSRM ({base_url})...")
    city_list = list(CITIES.keys())
    results = {}
    n = len(municipalities)

    for batch_start in range(0, n, batch_size):
        batch_end = min(batch_start + batch_size, n)
        batch = municipalities[batch_start:batch_end]

        pct = batch_end / n * 100
        print(f"  OSRM batch {batch_start}-{batch_end} of {n} ({pct:.0f}%)")

        try:
            durations = fetch_osrm_batch(batch, city_list, base_url)
        except Exception as e:
            print(f"    ERROR in batch: {e}")
            # Mark all as None for this batch
            for m in batch:
                results[m["id"]] = {c: None for c in city_list}
            if is_public:
                time.sleep(5)
            continue

        for i, m in enumerate(batch):
            muni_times = {}
            for j, city_id in enumerate(city_list):
                val = durations[i][j]
                muni_times[city_id] = round(val) if val is not None else None
            results[m["id"]] = muni_times

        # Rate limit for public server
        if is_public and batch_end < n:
            time.sleep(1.0)

    return results


# ─────────────── PT estimation from driving times ───────────────


def estimate_pt_times(municipalities, driving_times):
    """
    Estimate public transport times from driving times.

    Uses a Swiss-calibrated model where PT/drive ratio depends on:
    - Distance to city (closer = better PT, ratio closer to 1.2)
    - Municipality size (larger = likely better connected)

    Swiss PT is excellent near cities (often faster than driving due to
    traffic/parking) but degrades for remote mountain regions.
    """
    print("  Estimating PT times from driving data (Swiss model)...")
    results = {}

    for m in municipalities:
        mid = m["id"]
        drive = driving_times.get(mid, {})
        pt_times = {}

        for city_id, city in CITIES.items():
            drive_s = drive.get(city_id)
            if drive_s is None:
                pt_times[city_id] = None
                continue

            dist_km = haversine_km(m["lat"], m["lon"], city["lat"], city["lon"])

            # PT/drive ratio model:
            # - Very close (<20km): ratio ~1.1 (PT often competitive, S-Bahn)
            # - Medium (20-60km): ratio ~1.3-1.5 (regional trains)
            # - Far (60-120km): ratio ~1.4-1.8 (IC trains, some transfers)
            # - Very far (>120km): ratio ~1.6-2.2 (more transfers, less direct)
            if dist_km < 20:
                base_ratio = 1.1
            elif dist_km < 60:
                base_ratio = 1.2 + (dist_km - 20) / 40 * 0.3
            elif dist_km < 120:
                base_ratio = 1.5 + (dist_km - 60) / 60 * 0.3
            else:
                base_ratio = 1.8 + min((dist_km - 120) / 100 * 0.4, 0.4)

            # Adjust: municipalities near multiple cities tend to have better PT
            # (they're in the "golden belt" / Mittelland)
            nearby_cities = sum(
                1 for c in CITIES.values()
                if haversine_km(m["lat"], m["lon"], c["lat"], c["lon"]) < 80
            )
            if nearby_cities >= 3:
                base_ratio *= 0.92  # well-connected Mittelland corridor
            elif nearby_cities >= 2:
                base_ratio *= 0.96

            pt_s = round(drive_s * base_ratio)
            pt_times[city_id] = pt_s

        results[mid] = pt_times

    return results


# ─────────────── Main ───────────────


def main():
    parser = argparse.ArgumentParser(description="Fetch travel times")
    parser.add_argument(
        "--osrm",
        action="store_true",
        help="Use local OSRM for driving times",
    )
    parser.add_argument(
        "--osrm-public",
        action="store_true",
        help="Use public OSRM demo server (auto-batches, slower)",
    )
    parser.add_argument(
        "--mode",
        choices=["both", "driving", "pt"],
        default="both",
        help="Which modes to fetch",
    )
    parser.add_argument(
        "--estimate-pt",
        action="store_true",
        help="Estimate PT times from driving times (when no PT API available)",
    )
    args = parser.parse_args()

    municipalities = load_municipalities()
    print(f"Loaded {len(municipalities)} municipalities")

    # Load existing if present (for incremental updates)
    out_path = PROCESSED_DIR / "travel_times.json"
    travel_times = {"driving": {}, "public_transport": {}}

    # ── Driving times ──
    if args.mode in ("both", "driving"):
        if args.osrm_public:
            travel_times["driving"] = fetch_osrm_driving(
                municipalities, OSRM_PUBLIC_URL
            )
        elif args.osrm:
            travel_times["driving"] = fetch_osrm_driving(
                municipalities, OSRM_BASE_URL, batch_size=len(municipalities)
            )
        elif TRAVELTIME_APP_ID and TRAVELTIME_API_KEY:
            travel_times["driving"] = fetch_traveltime_mode(municipalities, "driving")
        else:
            print("ERROR: No driving time source available.")
            print("  Options: --osrm-public, --osrm, or set TRAVELTIME_APP_ID/KEY")
            return

    # ── Public transport times ──
    if args.mode in ("both", "pt"):
        if TRAVELTIME_APP_ID and TRAVELTIME_API_KEY and not args.estimate_pt:
            travel_times["public_transport"] = fetch_traveltime_mode(
                municipalities, "public_transport"
            )
        elif travel_times["driving"]:
            # Estimate PT from driving times
            travel_times["public_transport"] = estimate_pt_times(
                municipalities, travel_times["driving"]
            )
        else:
            print("WARNING: No PT data source and no driving data to estimate from.")

    with open(out_path, "w") as f:
        json.dump(travel_times, f)
    print(f"\nSaved travel times to {out_path}")

    # Print stats
    for mode, data in travel_times.items():
        if not data:
            continue
        total = len(data)
        reachable = sum(
            1
            for m in data.values()
            if any(v is not None for v in m.values())
        )
        all_times = [
            v for m in data.values()
            for v in m.values()
            if v is not None
        ]
        if all_times:
            avg_min = sum(all_times) / len(all_times) / 60
            print(f"  {mode}: {reachable}/{total} reachable, avg {avg_min:.0f} min")
        else:
            print(f"  {mode}: {reachable}/{total} reachable")


if __name__ == "__main__":
    main()
