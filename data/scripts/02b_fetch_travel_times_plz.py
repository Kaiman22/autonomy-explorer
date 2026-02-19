#!/usr/bin/env python3
"""
Step 2b: Fetch travel times at PLZ (postal code) level and aggregate to municipalities.

Instead of querying from municipality polygon centroids (which can be in forests/fields),
this queries from PLZ centroids (postal hubs near where people actually live).

For each municipality, travel time = min across all PLZ points in that municipality.
Using min (not average) because:
  - A municipality's "best" accessible point determines its real-world connectivity
  - If one PLZ in Zürich is 5 min from HB and another is 15 min, the effective
    commute is 5 min (people would live near the station)

Approach:
  - Driving: OSRM public server (batched, ~3,181 points)
  - PT: TravelTime API (batched, ~3,181 points with walking_time=1200)

Outputs: data/processed/travel_times.json (same format, municipality-keyed)
"""
import argparse
import json
import math
import time as time_mod

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
OSRM_BATCH_SIZE = 90


def load_plz_data():
    """Load PLZ points and municipality mapping."""
    with open(PROCESSED_DIR / "plz_points.json") as f:
        plz_points = json.load(f)

    with open(PROCESSED_DIR / "plz_municipality_map.json") as f:
        mapping = json.load(f)

    return plz_points, mapping


# ─────────────── OSRM Driving ───────────────


def fetch_osrm_batch(points_batch, city_list, base_url):
    """Fetch one batch of driving times from OSRM Table API."""
    coords_parts = []
    for p in points_batch:
        coords_parts.append(f"{p['lon']},{p['lat']}")
    for city_id in city_list:
        city = CITIES[city_id]
        coords_parts.append(f"{city['lon']},{city['lat']}")

    coords_str = ";".join(coords_parts)
    n_points = len(points_batch)

    sources = ";".join(str(i) for i in range(n_points))
    destinations = ";".join(str(n_points + i) for i in range(len(city_list)))

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


def fetch_osrm_driving(plz_points, base_url, batch_size=None):
    """Fetch driving times for all PLZ points using OSRM."""
    is_public = "project-osrm.org" in base_url
    if batch_size is None:
        batch_size = OSRM_BATCH_SIZE if is_public else len(plz_points)

    print(f"  Fetching driving times from OSRM ({base_url}) for {len(plz_points)} PLZ points...")
    city_list = list(CITIES.keys())
    results = {}  # plz_code → { city_id: seconds }
    n = len(plz_points)

    for batch_start in range(0, n, batch_size):
        batch_end = min(batch_start + batch_size, n)
        batch = plz_points[batch_start:batch_end]

        pct = batch_end / n * 100
        print(f"  OSRM batch {batch_start}-{batch_end} of {n} ({pct:.0f}%)")

        try:
            durations = fetch_osrm_batch(batch, city_list, base_url)
        except Exception as e:
            print(f"    ERROR in batch: {e}")
            for p in batch:
                results[p["plz"]] = {c: None for c in city_list}
            if is_public:
                time_mod.sleep(5)
            continue

        for i, p in enumerate(batch):
            plz_times = {}
            for j, city_id in enumerate(city_list):
                val = durations[i][j]
                plz_times[city_id] = round(val) if val is not None else None
            results[p["plz"]] = plz_times

        if is_public and batch_end < n:
            time_mod.sleep(1.0)

    return results


# ─────────────── TravelTime PT ───────────────


def build_traveltime_request(plz_batch, mode, batch_start):
    """Build a TravelTime time-filter request for one batch of PLZ points."""
    locations = []
    for city_id, city in CITIES.items():
        locations.append({
            "id": city_id,
            "coords": {"lat": city["lat"], "lng": city["lon"]},
        })
    for p in plz_batch:
        locations.append({
            "id": f"plz_{p['plz']}",
            "coords": {"lat": p["lat"], "lng": p["lon"]},
        })

    departure_ids = [f"plz_{p['plz']}" for p in plz_batch]

    searches = []
    for city_id in CITIES:
        searches.append({
            "id": f"to_{city_id}_{batch_start}",
            "arrival_location_id": city_id,
            "departure_location_ids": departure_ids,
            "transportation": {
                "type": mode,
                # Allow up to 20 min walking to reach the nearest PT stop.
                **({"walking_time": 1200} if mode == "public_transport" else {}),
            },
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
        time_mod.sleep(60)
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
    resp.raise_for_status()
    return resp.json()


def fetch_traveltime_pt(plz_points):
    """Fetch PT travel times for all PLZ points via TravelTime API."""
    results = {}  # plz_code → { city_id: seconds }
    n = len(plz_points)

    for batch_start in range(0, n, TRAVELTIME_MAX_LOCATIONS):
        batch_end = min(batch_start + TRAVELTIME_MAX_LOCATIONS, n)
        batch = plz_points[batch_start:batch_end]
        print(f"  TravelTime PT: batch {batch_start}-{batch_end} of {n}")

        payload = build_traveltime_request(batch, "public_transport", batch_start)
        data = call_traveltime(payload)

        for search_result in data.get("results", []):
            search_id = search_result["search_id"]
            city_id = search_id.split("_")[1]

            for loc in search_result.get("locations", []):
                plz_code = loc["id"].replace("plz_", "")
                tt = loc["properties"][0]["travel_time"]
                results.setdefault(plz_code, {})[city_id] = tt

            for unreachable_id in search_result.get("unreachable", []):
                plz_code = unreachable_id.replace("plz_", "")
                results.setdefault(plz_code, {})[city_id] = None

        time_mod.sleep(1)

    return results


# ─────────────── Aggregation ───────────────


def aggregate_to_municipalities(plz_times, muni_to_plz):
    """
    Aggregate PLZ-level travel times to municipality level.

    For each municipality, take the MINIMUM travel time across all its PLZ points.
    This represents the best-connected point in the municipality (where commuters
    would actually live).
    """
    city_list = list(CITIES.keys())
    muni_times = {}

    for muni_id, plz_codes in muni_to_plz.items():
        muni_result = {}
        for city_id in city_list:
            # Collect all valid times for this city from all PLZ in this municipality
            times = []
            for plz in plz_codes:
                t = plz_times.get(plz, {}).get(city_id)
                if t is not None:
                    times.append(t)

            if times:
                muni_result[city_id] = min(times)  # best PLZ wins
            else:
                muni_result[city_id] = None

        muni_times[muni_id] = muni_result

    return muni_times


# ─────────────── Main ───────────────


def main():
    parser = argparse.ArgumentParser(
        description="Fetch travel times at PLZ level and aggregate to municipalities"
    )
    parser.add_argument(
        "--mode",
        choices=["both", "driving", "pt"],
        default="both",
        help="Which modes to fetch",
    )
    parser.add_argument(
        "--osrm-public",
        action="store_true",
        help="Use public OSRM demo server for driving (slower, rate-limited)",
    )
    parser.add_argument(
        "--osrm-local",
        action="store_true",
        help="Use local OSRM for driving",
    )
    parser.add_argument(
        "--aggregate",
        choices=["min", "avg"],
        default="min",
        help="How to aggregate PLZ times to municipalities (default: min)",
    )
    args = parser.parse_args()

    plz_points, mapping = load_plz_data()
    muni_to_plz = mapping["municipality_to_plz"]
    print(f"Loaded {len(plz_points)} PLZ points, {len(muni_to_plz)} municipalities")

    # Load existing travel times (preserves data for modes not being fetched)
    out_path = PROCESSED_DIR / "travel_times.json"
    if out_path.exists():
        with open(out_path) as f:
            travel_times = json.load(f)
        print(f"Loaded existing travel times ({len(travel_times.get('driving', {}))} driving, "
              f"{len(travel_times.get('public_transport', {}))} PT)")
    else:
        travel_times = {"driving": {}, "public_transport": {}}

    # ── Driving times ──
    if args.mode in ("both", "driving"):
        if args.osrm_public:
            plz_drive = fetch_osrm_driving(plz_points, OSRM_PUBLIC_URL)
        elif args.osrm_local:
            plz_drive = fetch_osrm_driving(
                plz_points, OSRM_BASE_URL, batch_size=len(plz_points)
            )
        elif TRAVELTIME_APP_ID and TRAVELTIME_API_KEY:
            print("  NOTE: Using TravelTime for driving (OSRM recommended for speed)")
            # TravelTime driving uses same mechanism as PT
            plz_drive = {}
            n = len(plz_points)
            for batch_start in range(0, n, TRAVELTIME_MAX_LOCATIONS):
                batch_end = min(batch_start + TRAVELTIME_MAX_LOCATIONS, n)
                batch = plz_points[batch_start:batch_end]
                print(f"  TravelTime driving: batch {batch_start}-{batch_end} of {n}")
                payload = build_traveltime_request(batch, "driving", batch_start)
                data = call_traveltime(payload)
                for search_result in data.get("results", []):
                    search_id = search_result["search_id"]
                    city_id = search_id.split("_")[1]
                    for loc in search_result.get("locations", []):
                        plz_code = loc["id"].replace("plz_", "")
                        tt = loc["properties"][0]["travel_time"]
                        plz_drive.setdefault(plz_code, {})[city_id] = tt
                    for uid in search_result.get("unreachable", []):
                        plz_code = uid.replace("plz_", "")
                        plz_drive.setdefault(plz_code, {})[city_id] = None
                time_mod.sleep(1)
        else:
            print("ERROR: No driving source. Use --osrm-public, --osrm-local, or set TRAVELTIME_APP_ID/KEY")
            return

        # Save raw PLZ-level driving times
        plz_drive_path = PROCESSED_DIR / "plz_travel_times_driving.json"
        with open(plz_drive_path, "w") as f:
            json.dump(plz_drive, f)
        print(f"  Saved PLZ-level driving times to {plz_drive_path}")

        # Aggregate to municipality level
        travel_times["driving"] = aggregate_to_municipalities(plz_drive, muni_to_plz)
        print(f"  Aggregated driving to {len(travel_times['driving'])} municipalities")

    # ── Public transport times ──
    if args.mode in ("both", "pt"):
        if TRAVELTIME_APP_ID and TRAVELTIME_API_KEY:
            plz_pt = fetch_traveltime_pt(plz_points)
        else:
            print("ERROR: TravelTime API keys needed for PT. Set TRAVELTIME_APP_ID/KEY")
            return

        # Save raw PLZ-level PT times
        plz_pt_path = PROCESSED_DIR / "plz_travel_times_pt.json"
        with open(plz_pt_path, "w") as f:
            json.dump(plz_pt, f)
        print(f"  Saved PLZ-level PT times to {plz_pt_path}")

        # Aggregate to municipality level
        travel_times["public_transport"] = aggregate_to_municipalities(plz_pt, muni_to_plz)
        print(f"  Aggregated PT to {len(travel_times['public_transport'])} municipalities")

    # Save final municipality-level travel times
    with open(out_path, "w") as f:
        json.dump(travel_times, f)
    print(f"\nSaved aggregated travel times to {out_path}")

    # Print stats
    for mode, data in travel_times.items():
        if not data:
            continue
        total = len(data)
        reachable = sum(
            1 for m in data.values()
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
