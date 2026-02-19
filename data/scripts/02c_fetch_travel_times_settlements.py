#!/usr/bin/env python3
"""
Step 2c: Fetch travel times at settlement level and aggregate to municipalities.

Uses swissNAMES3D settlement points (~3,966 with >=100 inhabitants) instead of
PLZ polygon centroids. These are actual village/town center points — much closer
to PT stops and road infrastructure.

For each municipality, travel time = min across all settlement points in that municipality.

Approach:
  - Driving: OSRM public server (batched, ~3,966 points)
  - PT: TravelTime API (batched, with walking_time=1200)

Outputs:
  - data/processed/settlement_travel_times_driving.json (raw settlement-level)
  - data/processed/settlement_travel_times_pt.json (raw settlement-level)
  - data/processed/travel_times.json (aggregated municipality-level)
"""
import argparse
import json
import sys
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


def load_settlement_data():
    """Load settlement points and municipality mapping."""
    with open(PROCESSED_DIR / "settlement_points.json") as f:
        settlements = json.load(f)

    with open(PROCESSED_DIR / "settlement_municipality_map.json") as f:
        mapping = json.load(f)

    return settlements, mapping


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


def fetch_osrm_driving(settlements, base_url, batch_size=None):
    """Fetch driving times for all settlement points using OSRM."""
    is_public = "project-osrm.org" in base_url
    if batch_size is None:
        batch_size = OSRM_BATCH_SIZE if is_public else len(settlements)

    print(f"  Fetching driving times from OSRM for {len(settlements)} settlements...")
    sys.stdout.flush()
    city_list = list(CITIES.keys())
    results = {}  # uuid → { city_id: seconds }
    n = len(settlements)

    for batch_start in range(0, n, batch_size):
        batch_end = min(batch_start + batch_size, n)
        batch = settlements[batch_start:batch_end]

        pct = batch_end / n * 100
        print(f"  OSRM batch {batch_start}-{batch_end} of {n} ({pct:.0f}%)")
        sys.stdout.flush()

        try:
            durations = fetch_osrm_batch(batch, city_list, base_url)
        except Exception as e:
            print(f"    ERROR in batch: {e}")
            for p in batch:
                results[p["uuid"]] = {c: None for c in city_list}
            if is_public:
                time_mod.sleep(5)
            continue

        for i, p in enumerate(batch):
            times = {}
            for j, city_id in enumerate(city_list):
                val = durations[i][j]
                times[city_id] = round(val) if val is not None else None
            results[p["uuid"]] = times

        if is_public and batch_end < n:
            time_mod.sleep(1.0)

    return results


# ─────────────── TravelTime PT ───────────────


def build_traveltime_request(batch, mode, batch_start, idx_to_uuid):
    """Build a TravelTime time-filter request for one batch of settlement points."""
    locations = []
    for city_id, city in CITIES.items():
        locations.append({
            "id": city_id,
            "coords": {"lat": city["lat"], "lng": city["lon"]},
        })
    for i, p in enumerate(batch):
        loc_id = f"s{batch_start + i}"  # index-based, guaranteed unique
        idx_to_uuid[loc_id] = p["uuid"]
        locations.append({
            "id": loc_id,
            "coords": {"lat": p["lat"], "lng": p["lon"]},
        })

    departure_ids = [f"s{batch_start + i}" for i in range(len(batch))]

    searches = []
    for city_id in CITIES:
        searches.append({
            "id": f"to_{city_id}_{batch_start}",
            "arrival_location_id": city_id,
            "departure_location_ids": departure_ids,
            "transportation": {
                "type": mode,
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
        sys.stdout.flush()
        time_mod.sleep(60)
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
    resp.raise_for_status()
    return resp.json()


def fetch_traveltime_pt(settlements):
    """Fetch PT travel times for all settlement points via TravelTime API."""
    results = {}  # uuid → { city_id: seconds }
    n = len(settlements)

    for batch_start in range(0, n, TRAVELTIME_MAX_LOCATIONS):
        batch_end = min(batch_start + TRAVELTIME_MAX_LOCATIONS, n)
        batch = settlements[batch_start:batch_end]
        print(f"  TravelTime PT: batch {batch_start}-{batch_end} of {n}")
        sys.stdout.flush()

        idx_to_uuid = {}  # loc_id → uuid (filled by build_traveltime_request)
        payload = build_traveltime_request(batch, "public_transport", batch_start, idx_to_uuid)
        data = call_traveltime(payload)

        for search_result in data.get("results", []):
            search_id = search_result["search_id"]
            city_id = search_id.split("_")[1]

            for loc in search_result.get("locations", []):
                loc_id = loc["id"]
                uuid = idx_to_uuid.get(loc_id)
                if uuid:
                    tt = loc["properties"][0]["travel_time"]
                    results.setdefault(uuid, {})[city_id] = tt

            for unreachable_id in search_result.get("unreachable", []):
                uuid = idx_to_uuid.get(unreachable_id)
                if uuid:
                    results.setdefault(uuid, {})[city_id] = None

        time_mod.sleep(1)

    return results


# ─────────────── Aggregation ───────────────


def aggregate_to_municipalities(settlement_times, muni_to_settlements):
    """
    Aggregate settlement-level travel times to municipality level.
    For each municipality, take the MINIMUM travel time across all its settlements.
    """
    city_list = list(CITIES.keys())
    muni_times = {}

    for muni_id, settlement_uuids in muni_to_settlements.items():
        muni_result = {}
        for city_id in city_list:
            times = []
            for uuid in settlement_uuids:
                t = settlement_times.get(uuid, {}).get(city_id)
                if t is not None:
                    times.append(t)

            if times:
                muni_result[city_id] = min(times)
            else:
                muni_result[city_id] = None

        muni_times[muni_id] = muni_result

    return muni_times


# ─────────────── Main ───────────────


def main():
    parser = argparse.ArgumentParser(
        description="Fetch travel times at settlement level and aggregate to municipalities"
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
        help="Use public OSRM demo server for driving",
    )
    args = parser.parse_args()

    settlements, mapping = load_settlement_data()
    muni_to_settlements = mapping["municipality_to_settlements"]
    print(f"Loaded {len(settlements)} settlements, {len(muni_to_settlements)} municipalities")

    # Load existing travel times (preserves data for modes not being fetched)
    out_path = PROCESSED_DIR / "travel_times.json"
    if out_path.exists():
        with open(out_path) as f:
            travel_times = json.load(f)
        print(f"Loaded existing travel times")
    else:
        travel_times = {"driving": {}, "public_transport": {}}

    # ── Driving times ──
    if args.mode in ("both", "driving"):
        if args.osrm_public:
            drive_times = fetch_osrm_driving(settlements, OSRM_PUBLIC_URL)
        else:
            drive_times = fetch_osrm_driving(settlements, OSRM_BASE_URL, batch_size=len(settlements))

        # Save raw settlement-level driving times
        drive_path = PROCESSED_DIR / "settlement_travel_times_driving.json"
        with open(drive_path, "w") as f:
            json.dump(drive_times, f)
        print(f"  Saved settlement-level driving times to {drive_path}")

        # Aggregate to municipality level
        travel_times["driving"] = aggregate_to_municipalities(drive_times, muni_to_settlements)
        print(f"  Aggregated driving to {len(travel_times['driving'])} municipalities")

    # ── Public transport times ──
    if args.mode in ("both", "pt"):
        if not TRAVELTIME_APP_ID or not TRAVELTIME_API_KEY:
            print("ERROR: TravelTime API keys needed for PT. Set TRAVELTIME_APP_ID/KEY")
            return

        pt_times = fetch_traveltime_pt(settlements)

        # Save raw settlement-level PT times
        pt_path = PROCESSED_DIR / "settlement_travel_times_pt.json"
        with open(pt_path, "w") as f:
            json.dump(pt_times, f)
        print(f"  Saved settlement-level PT times to {pt_path}")

        # Aggregate to municipality level
        travel_times["public_transport"] = aggregate_to_municipalities(pt_times, muni_to_settlements)
        print(f"  Aggregated PT to {len(travel_times['public_transport'])} municipalities")

    # Save final municipality-level travel times
    with open(out_path, "w") as f:
        json.dump(travel_times, f)
    print(f"\nSaved aggregated travel times to {out_path}")

    # Print stats
    for mode_name, data in travel_times.items():
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
            print(f"  {mode_name}: {reachable}/{total} reachable, avg {avg_min:.0f} min")
        else:
            print(f"  {mode_name}: {reachable}/{total} reachable")


if __name__ == "__main__":
    main()
