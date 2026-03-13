#!/usr/bin/env python3
"""
Step 2h: Fetch driving times using Google Maps Routes API (with real traffic).

Replaces Geoapify (which uses free_flow + flat 1.25× correction, no time-of-day
traffic modeling). Google uses historical + real-time traffic patterns keyed to
departure time, giving the most accurate peak-hour commute times available.

Designed for FREE-TIER cycling: processes city-by-city, stops at --max-requests
(default 5,000 = Pro free tier). Swap API key and re-run to continue.

API: POST https://routes.googleapis.com/directions/v2:computeRoutes
  - Headers: X-Goog-Api-Key, X-Goog-FieldMask: routes.duration
  - Body: origin, destination, travelMode=DRIVE, routingPreference=TRAFFIC_AWARE,
          departureTime (RFC 3339 UTC)
  - Response: routes[0].duration = "1234s" (string, seconds with traffic)
  - Rate limit: 3,000 QPM = 50 QPS

Pricing (post-March 2025):
  Pro tier (TRAFFIC_AWARE): $10/1,000 requests, 5,000 free/month per billing account

For ~3,966 settlements × 10 cities = ~39,660 route requests:
  - 8 API keys × 5,000 free/month = 40,000 → $0 total
  - At ~30 req/s → ~3 min per key

Resumable: checkpoint tracks every (settlement, city) pair completed.
Re-run with a new --api-key to continue from where it stopped.

Outputs:
  - data/processed/settlement_travel_times_driving.json (settlement-level)
  - data/processed/travel_times.json (aggregated municipality-level, driving only)

Usage:
  python3 02h_fetch_driving_times_google.py --api-key AIzaSy... --max-requests 5000
  python3 02h_fetch_driving_times_google.py --dry-run           # show plan only
  python3 02h_fetch_driving_times_google.py --status            # show progress
  python3 02h_fetch_driving_times_google.py --clear-checkpoint  # start fresh
"""
import argparse
import json
import re
import sys
import threading
import time as time_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from config import (
    CITIES,
    GOOGLE_MAPS_API_KEY,
    GOOGLE_ROUTES_URL,
    PROCESSED_DIR,
)

CHECKPOINT_PATH = PROCESSED_DIR / "google_driving_checkpoint.json"

# Peak-hour departure for traffic modeling.
# Tuesday 7:30 AM CET = 06:30 UTC — representative weekday morning commute.
DEPARTURE_TIME_UTC = "2026-03-17T06:30:00Z"

# City processing order (matches CITIES dict order)
CITY_ORDER = list(CITIES.keys())

# Concurrency / rate-limiting
DEFAULT_WORKERS = 10
RATE_LIMIT_RPS = 30
CHECKPOINT_EVERY = 100  # save checkpoint every N settlement-city pairs


class RateLimiter:
    """Token-bucket rate limiter for concurrent workers."""

    def __init__(self, rps):
        self.interval = 1.0 / rps
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self):
        with self.lock:
            now = time_mod.monotonic()
            wait_until = self.last + self.interval
            if now < wait_until:
                time_mod.sleep(wait_until - now)
            self.last = time_mod.monotonic()


def parse_duration(duration_str):
    """Parse Google Routes API duration string "1234s" to integer seconds."""
    if not duration_str:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)s$", duration_str)
    return round(float(m.group(1))) if m else None


def validate_google_key(api_key):
    """Validate the Google Maps API key with a test route request."""
    if not api_key:
        print("ERROR: No API key provided.")
        print("  Use: --api-key YOUR_KEY")
        print("  Or:  export GOOGLE_MAPS_API_KEY=YOUR_KEY")
        return False

    print(f"  Validating API key ({api_key[:12]}...)...")

    body = {
        "origin": {
            "location": {
                "latLng": {"latitude": 47.3769, "longitude": 8.5417}  # Zürich
            }
        },
        "destination": {
            "location": {
                "latLng": {"latitude": 47.5476, "longitude": 7.5891}  # Basel
            }
        },
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "departureTime": DEPARTURE_TIME_UTC,
    }

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "routes.duration",
    }

    try:
        resp = requests.post(GOOGLE_ROUTES_URL, json=body, headers=headers, timeout=30)

        if resp.status_code in (401, 403):
            err = resp.json().get("error", {})
            msg = err.get("message", resp.text[:300])
            print(f"ERROR: Auth failed ({resp.status_code}): {msg}")
            return False
        elif resp.status_code == 429:
            print("ERROR: Rate limit / quota exceeded (429).")
            return False
        elif resp.status_code >= 400:
            err = resp.json().get("error", {})
            msg = err.get("message", resp.text[:300])
            print(f"ERROR: Google returned {resp.status_code}: {msg}")
            return False

        data = resp.json()
        routes = data.get("routes", [])
        if not routes:
            print(f"ERROR: No routes returned: {json.dumps(data)[:300]}")
            return False

        duration = parse_duration(routes[0].get("duration"))
        if duration:
            print(f"  OK! Zürich→Basel: {duration}s ({duration // 60} min) [TRAFFIC_AWARE]")
            # This validation request counts toward the quota!
            return True
        else:
            print(f"  Unexpected duration format: {routes[0].get('duration')}")
            return False

    except Exception as e:
        print(f"  Error: {e}")
        return False


# ── Checkpoint ────────────────────────────────────────────────────────────

def load_checkpoint():
    """
    Load checkpoint.  Structure:
    {
        "results": { uuid: { city_id: seconds|null } },
        "requests_made": 12345,        # lifetime across all keys
        "updated_at": "...",
    }
    """
    if CHECKPOINT_PATH.exists():
        with open(CHECKPOINT_PATH) as f:
            return json.load(f)
    return {"results": {}, "requests_made": 0}


def save_checkpoint(checkpoint):
    checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(checkpoint, f)


def get_progress(checkpoint, settlements, cities=None):
    """
    Compute detailed progress from checkpoint.

    Returns dict with per-city counts and overall stats.
    """
    if cities is None:
        cities = CITY_ORDER

    results = checkpoint["results"]
    n_settlements = len(settlements)

    progress = {"cities": {}, "total_pairs": n_settlements * len(cities)}
    total_done = 0

    for city_id in cities:
        done = sum(
            1 for s in settlements
            if s["uuid"] in results and city_id in results[s["uuid"]]
        )
        remaining = n_settlements - done
        progress["cities"][city_id] = {"done": done, "remaining": remaining}
        total_done += done

    progress["total_done"] = total_done
    progress["total_remaining"] = progress["total_pairs"] - total_done
    progress["requests_made"] = checkpoint.get("requests_made", 0)
    return progress


def print_status(checkpoint, settlements):
    """Print a nice status table."""
    prog = get_progress(checkpoint, settlements)

    print(f"\n{'='*60}")
    print(f"  Google Driving Times — Progress Report")
    print(f"{'='*60}")
    print(f"  Total lifetime API requests: {prog['requests_made']:,}")
    print(f"  Pairs completed: {prog['total_done']:,} / {prog['total_pairs']:,} "
          f"({prog['total_done']/prog['total_pairs']*100:.1f}%)")
    print(f"  Pairs remaining: {prog['total_remaining']:,}")
    print(f"\n  {'City':<15} {'Done':>6} {'Remaining':>10} {'Status':>10}")
    print(f"  {'-'*15} {'-'*6} {'-'*10} {'-'*10}")

    for city_id in CITY_ORDER:
        c = prog["cities"][city_id]
        n = len(settlements)
        if c["done"] == n:
            status = "DONE"
        elif c["done"] > 0:
            status = f"{c['done']/n*100:.0f}%"
        else:
            status = "pending"
        print(f"  {city_id:<15} {c['done']:>6} {c['remaining']:>10} {status:>10}")

    keys_needed = (prog["total_remaining"] + 4999) // 5000  # 5000 free per key
    print(f"\n  API keys still needed (~5,000 free/key): {keys_needed}")
    print(f"{'='*60}\n")


# ── Single route fetch ────────────────────────────────────────────────────

def fetch_single_route(origin_lat, origin_lon, dest_lat, dest_lon,
                       api_key, rate_limiter):
    """
    Fetch one driving route. Returns seconds (int) or None.
    """
    rate_limiter.wait()

    body = {
        "origin": {
            "location": {
                "latLng": {"latitude": origin_lat, "longitude": origin_lon}
            }
        },
        "destination": {
            "location": {
                "latLng": {"latitude": dest_lat, "longitude": dest_lon}
            }
        },
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "departureTime": DEPARTURE_TIME_UTC,
    }

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "routes.duration",
    }

    for attempt in range(4):
        try:
            resp = requests.post(
                GOOGLE_ROUTES_URL, json=body, headers=headers, timeout=30
            )

            if resp.status_code == 429:
                wait = min(10 * (2 ** attempt), 60)
                time_mod.sleep(wait)
                continue

            if resp.status_code >= 400:
                return None  # no route (car-free, island, etc.)

            data = resp.json()
            routes = data.get("routes", [])
            if not routes:
                return None

            return parse_duration(routes[0].get("duration"))

        except requests.exceptions.Timeout:
            time_mod.sleep(5 * (attempt + 1))
        except Exception:
            if attempt < 3:
                time_mod.sleep(5 * (attempt + 1))
            else:
                return None

    return None


# ── Main fetch loop (city-by-city) ────────────────────────────────────────

def fetch_driving_times(settlements, api_key, max_requests=5000,
                        max_workers=DEFAULT_WORKERS, dry_run=False):
    """
    Fetch driving times city-by-city, stopping at max_requests.

    Processes cities in CITY_ORDER. For each city, fetches all settlements
    that don't have a result yet. Stops as soon as request budget is exhausted.
    """
    checkpoint = load_checkpoint()
    results = checkpoint["results"]
    n_settle = len(settlements)

    # Build work list: (city_id, settlement) pairs still needed
    work = []
    for city_id in CITY_ORDER:
        city_info = CITIES[city_id]
        for s in settlements:
            uuid = s["uuid"]
            if uuid in results and city_id in results[uuid]:
                continue  # already done
            work.append((city_id, city_info, s))

    total_remaining = len(work)
    to_process = min(total_remaining, max_requests)

    # Summarize plan
    print(f"\nGoogle Maps Routes API — Batch Plan:")
    print(f"  Settlements: {n_settle}")
    print(f"  Cities: {len(CITY_ORDER)}")
    print(f"  Total pairs remaining: {total_remaining:,}")
    print(f"  Budget this run: {max_requests:,} requests")
    print(f"  Will process: {to_process:,} pairs")
    print(f"  Estimated time: ~{to_process / RATE_LIMIT_RPS / 60:.1f} minutes")

    # Show which cities will be covered this run
    budget_left = max_requests
    print(f"\n  City breakdown for this run:")
    for city_id in CITY_ORDER:
        city_remaining = sum(1 for c, _, _ in work if c == city_id)
        if city_remaining == 0:
            print(f"    {city_id:<15} DONE")
            continue
        will_do = min(city_remaining, budget_left)
        budget_left -= will_do
        print(f"    {city_id:<15} {will_do:>5} of {city_remaining} remaining")
        if budget_left <= 0:
            break

    if dry_run:
        print(f"\n  --dry-run: No API calls made.")
        return None

    if to_process == 0:
        print(f"\n  All pairs already completed!")
        return results

    # Fetch
    rate_limiter = RateLimiter(RATE_LIMIT_RPS)
    requests_this_run = 0
    errors = 0
    start_time = time_mod.monotonic()

    work_to_do = work[:to_process]

    # Process in chunks for checkpointing
    chunk_start = 0
    while chunk_start < len(work_to_do):
        chunk_end = min(chunk_start + CHECKPOINT_EVERY, len(work_to_do))
        chunk = work_to_do[chunk_start:chunk_end]

        # Submit chunk to thread pool
        futures = {}
        for city_id, city_info, s in chunk:
            future = executor_submit_route(
                city_id, city_info, s, api_key, rate_limiter,
                futures, max_workers
            )

        # Wait — process one chunk at a time with a local executor
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {}
            for city_id, city_info, s in chunk:
                f = executor.submit(
                    fetch_single_route,
                    s["lat"], s["lon"],
                    city_info["lat"], city_info["lon"],
                    api_key, rate_limiter,
                )
                future_map[f] = (s["uuid"], city_id)

            for future in as_completed(future_map):
                uuid, city_id = future_map[future]
                try:
                    duration = future.result()
                    if uuid not in results:
                        results[uuid] = {}
                    results[uuid][city_id] = duration
                    requests_this_run += 1
                except Exception as e:
                    errors += 1
                    if uuid not in results:
                        results[uuid] = {}
                    results[uuid][city_id] = None  # mark as attempted
                    requests_this_run += 1
                    if errors <= 5:
                        print(f"    Error {uuid}→{city_id}: {e}")

        # Save checkpoint
        checkpoint["results"] = results
        checkpoint["requests_made"] = checkpoint.get("requests_made", 0) + (chunk_end - chunk_start)
        save_checkpoint(checkpoint)

        # Progress
        elapsed = time_mod.monotonic() - start_time
        rate = requests_this_run / elapsed if elapsed > 0 else 0
        remaining_in_run = len(work_to_do) - chunk_end
        eta = remaining_in_run / rate / 60 if rate > 0 else 0

        # Figure out current city being processed
        current_city = chunk[-1][0] if chunk else "?"

        print(f"  [{requests_this_run:,}/{to_process:,}] "
              f"city={current_city} | {rate:.1f} req/s | "
              f"ETA {eta:.1f} min | errors={errors}")
        sys.stdout.flush()

        chunk_start = chunk_end

    elapsed_total = time_mod.monotonic() - start_time
    print(f"\n  Done! {requests_this_run:,} requests in {elapsed_total / 60:.1f} min")
    print(f"  Lifetime requests: {checkpoint['requests_made']:,}")

    return results


def executor_submit_route(*args, **kwargs):
    """Placeholder — actual submission is inline in fetch_driving_times."""
    pass


# ── Aggregation & spot checks ────────────────────────────────────────────

def aggregate_to_municipalities(settlement_times, muni_to_settlements):
    """
    Aggregate settlement-level driving times to municipality level.
    For each municipality, take the MINIMUM driving time across all its settlements.
    Only includes cities that have data.
    """
    # Figure out which cities have data
    cities_with_data = set()
    for uuid_times in settlement_times.values():
        for city_id, t in uuid_times.items():
            if t is not None:
                cities_with_data.add(city_id)

    muni_times = {}
    for muni_id, settlement_uuids in muni_to_settlements.items():
        muni_result = {}
        for city_id in CITY_ORDER:
            if city_id not in cities_with_data:
                continue
            times = []
            for uuid in settlement_uuids:
                t = settlement_times.get(uuid, {}).get(city_id)
                if t is not None:
                    times.append(t)
            muni_result[city_id] = min(times) if times else None
        muni_times[muni_id] = muni_result

    return muni_times


def spot_check(results):
    """Compare key routes against known Google Maps travel times."""
    print("\n  Spot check (Google Routes API vs expected peak-hour times):")

    name_to_uuid = {}
    settlements_path = PROCESSED_DIR / "settlement_points.json"
    if settlements_path.exists():
        with open(settlements_path) as f:
            for s in json.load(f):
                name_to_uuid[s["name"].lower()] = s["uuid"]

    checks = [
        ("rothenthurm", "zurich", 52), ("cham", "zurich", 30),
        ("aarau", "zurich", 40), ("rapperswil (be)", "zurich", 40),
        ("interlaken", "bern", 55), ("thun", "bern", 30),
        ("zermatt", "bern", 140), ("olten", "basel", 40),
        ("sion", "lausanne", 80),
    ]

    for name, city, expected in checks:
        uuid = name_to_uuid.get(name.lower())
        if uuid and uuid in results and city in results[uuid]:
            actual_s = results[uuid][city]
            if actual_s is not None:
                actual_min = actual_s / 60
                ratio = actual_min / expected if expected else 0
                status = "OK" if 0.7 <= ratio <= 1.4 else "WARNING"
                print(f"    {status}: {name} → {city}: "
                      f"{actual_min:.0f} min (expected ~{expected}, ratio {ratio:.2f})")
            else:
                print(f"    SKIP: {name} → {city}: unreachable")
        else:
            print(f"    SKIP: {name} → {city}: not yet fetched")


def compare_with_geoapify(google_results):
    """Compare Google results with existing Geoapify results."""
    geo_path = PROCESSED_DIR / "settlement_travel_times_driving.json"
    if not geo_path.exists():
        return

    with open(geo_path) as f:
        geo_results = json.load(f)

    name_to_uuid = {}
    with open(PROCESSED_DIR / "settlement_points.json") as f:
        for s in json.load(f):
            name_to_uuid[s["name"].lower()] = s["uuid"]

    print("\n  Google vs Geoapify (key routes):")
    print(f"    {'Route':<30} {'Geoapify':>10} {'Google':>10} {'Diff':>8}")
    print(f"    {'-'*30} {'-'*10} {'-'*10} {'-'*8}")

    routes = [
        ("aarau", "zurich"), ("cham", "zurich"), ("thun", "bern"),
        ("interlaken", "bern"), ("olten", "basel"), ("rothenthurm", "zurich"),
        ("sion", "lausanne"), ("zermatt", "bern"),
    ]

    diffs = []
    for name, city in routes:
        uuid = name_to_uuid.get(name.lower())
        if not uuid:
            continue
        geo_s = geo_results.get(uuid, {}).get(city)
        goo_s = google_results.get(uuid, {}).get(city)
        if geo_s and goo_s:
            gm, gom = geo_s / 60, goo_s / 60
            d = (gom - gm) / gm * 100
            diffs.append(d)
            print(f"    {name}→{city:<12} {gm:>8.0f}m {gom:>8.0f}m {d:>+7.1f}%")

    if diffs:
        print(f"\n    Avg diff: {sum(diffs)/len(diffs):+.1f}% "
              f"(positive = Google slower = more traffic)")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch driving times using Google Maps Routes API (with traffic). "
                    "Processes city-by-city; stops at --max-requests for free-tier cycling."
    )
    parser.add_argument(
        "--api-key", type=str, default=None,
        help="Google Maps API key (overrides GOOGLE_MAPS_API_KEY env var)",
    )
    parser.add_argument(
        "--max-requests", type=int, default=5000,
        help="Max requests this run (default: 5000 = Pro free tier)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show plan without making API calls",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show current progress and exit",
    )
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS,
        help=f"Concurrent workers (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--no-aggregate", action="store_true",
        help="Skip municipality-level aggregation",
    )
    parser.add_argument(
        "--clear-checkpoint", action="store_true",
        help="Clear all progress and start fresh",
    )
    parser.add_argument(
        "--no-compare", action="store_true",
        help="Skip Geoapify comparison",
    )
    args = parser.parse_args()

    api_key = args.api_key or GOOGLE_MAPS_API_KEY

    if args.clear_checkpoint and CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        print("Cleared checkpoint.")

    # Load settlements
    with open(PROCESSED_DIR / "settlement_points.json") as f:
        settlements = json.load(f)
    print(f"Loaded {len(settlements)} settlement points")

    # Status mode
    if args.status:
        checkpoint = load_checkpoint()
        print_status(checkpoint, settlements)
        return

    # Validate API key (costs 1 request!)
    if not args.dry_run:
        if not validate_google_key(api_key):
            sys.exit(1)
        # Account for validation request
        # (we won't subtract from max_requests since it's just 1)

    # Fetch
    results = fetch_driving_times(
        settlements,
        api_key=api_key,
        max_requests=args.max_requests,
        max_workers=args.workers,
        dry_run=args.dry_run,
    )

    if args.dry_run or results is None:
        return

    # Show status after run
    checkpoint = load_checkpoint()
    print_status(checkpoint, settlements)

    # Spot check
    spot_check(results)

    # Compare with Geoapify
    if not args.no_compare:
        compare_with_geoapify(results)

    # Save settlement-level results
    # IMPORTANT: merge with existing Geoapify data for cities not yet fetched
    drive_path = PROCESSED_DIR / "settlement_travel_times_driving.json"
    if drive_path.exists():
        with open(drive_path) as f:
            existing = json.load(f)
        # Overlay Google results on top of existing Geoapify data
        for uuid, city_times in results.items():
            if uuid not in existing:
                existing[uuid] = {}
            for city_id, t in city_times.items():
                if t is not None:  # only overwrite with actual data
                    existing[uuid][city_id] = t
        merged = existing
    else:
        merged = results

    with open(drive_path, "w") as f:
        json.dump(merged, f)
    print(f"\nSaved settlement-level driving times to {drive_path}")

    # Aggregate to municipality level
    if not args.no_aggregate:
        with open(PROCESSED_DIR / "settlement_municipality_map.json") as f:
            mapping = json.load(f)
        muni_to_settlements = mapping["municipality_to_settlements"]

        muni_times = aggregate_to_municipalities(merged, muni_to_settlements)

        tt_path = PROCESSED_DIR / "travel_times.json"
        if tt_path.exists():
            with open(tt_path) as f:
                travel_times = json.load(f)
        else:
            travel_times = {"driving": {}, "public_transport": {}}

        travel_times["driving"] = muni_times
        travel_times["driving_source"] = "google_traffic_aware"

        with open(tt_path, "w") as f:
            json.dump(travel_times, f)
        print(f"Saved aggregated travel times to {tt_path}")
        print(f"  {len(muni_times)} municipalities")

        all_times = [v for m in muni_times.values() for v in m.values() if v is not None]
        if all_times:
            print(f"  Driving stats: min={min(all_times)/60:.0f}m, "
                  f"avg={sum(all_times)/len(all_times)/60:.0f}m, "
                  f"max={max(all_times)/60:.0f}m")


if __name__ == "__main__":
    main()
