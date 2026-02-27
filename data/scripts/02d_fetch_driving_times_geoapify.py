#!/usr/bin/env python3
"""
Step 2d: Fetch driving times using Geoapify Route Matrix API (with traffic).

Replaces OSRM (which gives free-flow times without traffic, systematically
15-30% faster than real-world). Geoapify uses approximated traffic data from
OpenStreetMap + traffic patterns, giving more realistic times.

API: POST https://api.geoapify.com/v1/routematrix
  - Up to 100 sources x 10 targets per request (1,000 elements)
  - Credits: Max(S,T) * Min(S,T,10) per call = 1,000 credits for 100x10
  - Free tier: 3,000 credits/day = 3 batches/day
  - Traffic: "approximated" (uses time-of-day patterns)
  - Coords: [lon, lat] order (GeoJSON style)

For ~3,966 settlements x 10 cities:
  - 40 batches x 1,000 credits = 40,000 credits total
  - Free tier: ~14 days (3 batches/day)
  - Paid ($49/mo): 30,000 credits/day = 2 days

Resumable: saves checkpoint after each batch. Re-run to continue.

Outputs:
  - data/processed/settlement_travel_times_driving.json (settlement-level)
  - data/processed/travel_times.json (aggregated municipality-level, driving only)

Usage:
  export GEOAPIFY_API_KEY=your_key_here
  python3 02d_fetch_driving_times_geoapify.py
  python3 02d_fetch_driving_times_geoapify.py --dry-run    # estimate credits only
  python3 02d_fetch_driving_times_geoapify.py --max-batches 3  # limit batches (daily quota)
"""
import argparse
import json
import sys
import time as time_mod
from datetime import datetime, timezone

import requests

from config import (
    CITIES,
    GEOAPIFY_API_KEY,
    GEOAPIFY_BATCH_SOURCES,
    GEOAPIFY_DAILY_CREDITS,
    GEOAPIFY_MATRIX_URL,
    PROCESSED_DIR,
)

CHECKPOINT_PATH = PROCESSED_DIR / "geoapify_driving_checkpoint.json"


def validate_geoapify_key():
    """Validate the Geoapify API key with a minimal test request."""
    if not GEOAPIFY_API_KEY:
        print("ERROR: GEOAPIFY_API_KEY environment variable not set.")
        print("  Sign up free at https://myprojects.geoapify.com/")
        print("  Then: export GEOAPIFY_API_KEY=your_key_here")
        return False

    print(f"  Validating Geoapify API key ({GEOAPIFY_API_KEY[:8]}...)...")

    # Minimal test: 2 sources x 1 target = 2 credits
    test_payload = {
        "mode": "drive",
        "traffic": "approximated",
        "sources": [
            {"location": [8.5417, 47.3769]},  # Zürich
            {"location": [7.4395, 46.9490]},  # Bern
        ],
        "targets": [
            {"location": [7.5891, 47.5476]},  # Basel
        ],
    }

    try:
        resp = requests.post(
            f"{GEOAPIFY_MATRIX_URL}?apiKey={GEOAPIFY_API_KEY}",
            json=test_payload,
            timeout=30,
        )
        if resp.status_code == 401:
            print("ERROR: Invalid API key (401 Unauthorized).")
            return False
        elif resp.status_code == 403:
            print("ERROR: API key forbidden (403). Check your Geoapify plan.")
            return False
        elif resp.status_code == 429:
            print("ERROR: Daily quota exceeded (429). Try again tomorrow.")
            return False
        elif resp.status_code >= 400:
            print(f"ERROR: Geoapify returned {resp.status_code}: {resp.text[:200]}")
            return False

        data = resp.json()
        # Check we got valid results
        if "sources_to_targets" in data:
            t = data["sources_to_targets"][0][0].get("time")
            print(f"  API key valid. Test route Zürich→Basel: {t}s ({t//60} min)")
            return True
        else:
            print(f"  Unexpected response format: {json.dumps(data)[:200]}")
            return False

    except Exception as e:
        print(f"  Error validating key: {e}")
        return False


def load_checkpoint():
    """Load existing checkpoint (partially completed results)."""
    if CHECKPOINT_PATH.exists():
        with open(CHECKPOINT_PATH) as f:
            data = json.load(f)
        print(f"  Loaded checkpoint: {data['completed_batches']} batches done, "
              f"{len(data['results'])} settlements with times")
        return data
    return {"results": {}, "completed_batches": 0, "batch_order": []}


def save_checkpoint(checkpoint):
    """Save checkpoint for resumability."""
    checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(checkpoint, f)


def fetch_matrix_batch(settlements_batch, city_targets):
    """
    Fetch one batch of driving times from Geoapify Route Matrix.

    Args:
        settlements_batch: list of settlement dicts with lat, lon, uuid
        city_targets: list of (city_id, lat, lon) tuples

    Returns:
        dict: { uuid: { city_id: seconds } }
    """
    # Build sources (settlements) — [lon, lat] order
    sources = [{"location": [s["lon"], s["lat"]]} for s in settlements_batch]

    # Build targets (cities) — [lon, lat] order
    targets = [{"location": [lon, lat]} for _, lat, lon in city_targets]

    payload = {
        "mode": "drive",
        "traffic": "approximated",
        "sources": sources,
        "targets": targets,
    }

    url = f"{GEOAPIFY_MATRIX_URL}?apiKey={GEOAPIFY_API_KEY}"

    # Retry with backoff
    for attempt in range(5):
        try:
            resp = requests.post(url, json=payload, timeout=120)

            if resp.status_code == 429:
                if attempt < 4:
                    wait = min(60 * (2 ** attempt), 300)
                    print(f"    Rate limited (429), waiting {wait}s (attempt {attempt + 1}/5)...")
                    sys.stdout.flush()
                    time_mod.sleep(wait)
                    continue
                else:
                    print("    Daily quota likely exceeded. Save and retry tomorrow.")
                    return None  # Signal to stop

            resp.raise_for_status()
            data = resp.json()

            # Parse results
            results = {}
            s2t = data.get("sources_to_targets", [])

            for src_idx, source_row in enumerate(s2t):
                uuid = settlements_batch[src_idx]["uuid"]
                times = {}
                for tgt_idx, cell in enumerate(source_row):
                    city_id = city_targets[tgt_idx][0]
                    t = cell.get("time")  # seconds, or None if unreachable
                    times[city_id] = round(t) if t is not None else None
                results[uuid] = times

            return results

        except requests.exceptions.Timeout:
            wait = 30 * (attempt + 1)
            print(f"    Timeout, retrying in {wait}s...")
            time_mod.sleep(wait)
        except Exception as e:
            if attempt < 4:
                wait = 30 * (attempt + 1)
                print(f"    Error: {e}, retrying in {wait}s...")
                time_mod.sleep(wait)
            else:
                print(f"    Failed after 5 attempts: {e}")
                return {}

    return {}


def fetch_all_driving_times(settlements, max_batches=None, dry_run=False):
    """
    Fetch driving times for all settlements to all cities using Geoapify.

    Resumable: loads checkpoint and skips completed batches.
    Quota-aware: stops after max_batches (default: 3 for free tier daily limit).
    """
    city_targets = [(cid, c["lat"], c["lon"]) for cid, c in CITIES.items()]
    n_cities = len(city_targets)
    batch_size = GEOAPIFY_BATCH_SOURCES  # 100 sources per batch

    # Credits per batch: Max(batch_size, n_cities) * Min(batch_size, n_cities, 10)
    credits_per_batch = max(batch_size, n_cities) * min(batch_size, n_cities, 10)
    n_batches = (len(settlements) + batch_size - 1) // batch_size
    total_credits = n_batches * credits_per_batch
    daily_batches = GEOAPIFY_DAILY_CREDITS // credits_per_batch
    days_needed = (n_batches + daily_batches - 1) // daily_batches

    print(f"\nGeoapify Route Matrix Plan:")
    print(f"  Settlements: {len(settlements)}")
    print(f"  Cities (targets): {n_cities}")
    print(f"  Batch size: {batch_size} sources x {n_cities} targets")
    print(f"  Credits per batch: {credits_per_batch:,}")
    print(f"  Total batches: {n_batches}")
    print(f"  Total credits: {total_credits:,}")
    print(f"  Free tier daily limit: {GEOAPIFY_DAILY_CREDITS:,} credits = {daily_batches} batches/day")
    print(f"  Estimated days (free tier): {days_needed}")

    if dry_run:
        print("\n  --dry-run: Not making any API calls.")
        return None

    # Load checkpoint
    checkpoint = load_checkpoint()
    completed = checkpoint["completed_batches"]

    if completed >= n_batches:
        print(f"\n  All {n_batches} batches already completed!")
        return checkpoint["results"]

    remaining = n_batches - completed
    if max_batches is None:
        max_batches = daily_batches  # Default: one day's worth

    batches_to_run = min(remaining, max_batches)
    credits_to_use = batches_to_run * credits_per_batch

    print(f"\n  Already completed: {completed}/{n_batches} batches")
    print(f"  Batches to run now: {batches_to_run} ({credits_to_use:,} credits)")
    print(f"  Remaining after this run: {remaining - batches_to_run}")
    sys.stdout.flush()

    batches_run = 0
    for batch_idx in range(completed, n_batches):
        if batches_run >= batches_to_run:
            print(f"\n  Reached batch limit ({max_batches}). Run again to continue.")
            break

        batch_start = batch_idx * batch_size
        batch_end = min(batch_start + batch_size, len(settlements))
        batch = settlements[batch_start:batch_end]

        pct = batch_end / len(settlements) * 100
        print(f"\n  Batch {batch_idx + 1}/{n_batches}: settlements {batch_start}-{batch_end} ({pct:.0f}%)")
        sys.stdout.flush()

        result = fetch_matrix_batch(batch, city_targets)

        if result is None:
            # Quota exceeded signal
            print("  Stopping due to quota. Run again tomorrow.")
            break

        if result:
            checkpoint["results"].update(result)

        checkpoint["completed_batches"] = batch_idx + 1
        save_checkpoint(checkpoint)
        batches_run += 1

        # Rate limiting between batches
        if batches_run < batches_to_run:
            time_mod.sleep(1.0)

    print(f"\n  Completed {batches_run} batches this run.")
    print(f"  Total settlements with driving times: {len(checkpoint['results'])}")

    return checkpoint["results"]


def aggregate_to_municipalities(settlement_times, muni_to_settlements):
    """
    Aggregate settlement-level driving times to municipality level.
    For each municipality, take the MINIMUM driving time across all its settlements.
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
            muni_result[city_id] = min(times) if times else None
        muni_times[muni_id] = muni_result

    return muni_times


def spot_check(results):
    """Compare a few key routes against known travel times for sanity check."""
    print("\n  Spot check (Geoapify vs expected Google Maps times):")

    # Find settlements by name for spot checking
    name_to_uuid = {}
    settlements_path = PROCESSED_DIR / "settlement_points.json"
    if settlements_path.exists():
        with open(settlements_path) as f:
            for s in json.load(f):
                name_to_uuid[s["name"].lower()] = s["uuid"]

    checks = [
        # (settlement_name, city_id, expected_min_google_maps)
        ("rothenthurm", "zurich", 52),
        ("cham", "zurich", 30),
        ("rapperswil (be)", "zurich", 40),  # Rapperswil near Zürich
        ("interlaken", "bern", 55),
        ("zermatt", "bern", 140),
    ]

    for name, city, expected in checks:
        uuid = name_to_uuid.get(name.lower())
        if uuid and uuid in results:
            actual_s = results[uuid].get(city)
            if actual_s is not None:
                actual_min = actual_s / 60
                ratio = actual_min / expected if expected else 0
                status = "OK" if 0.8 <= ratio <= 1.3 else "WARNING"
                print(f"    {status}: {name} → {city}: "
                      f"Geoapify {actual_min:.0f} min, expected ~{expected} min "
                      f"(ratio {ratio:.2f})")
            else:
                print(f"    SKIP: {name} → {city}: unreachable in Geoapify")
        else:
            print(f"    SKIP: {name} not found in results")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch driving times using Geoapify Route Matrix (with traffic)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Only estimate credits, don't make API calls",
    )
    parser.add_argument(
        "--max-batches", type=int, default=None,
        help="Max batches to run (default: 1 day's free quota = 3)",
    )
    parser.add_argument(
        "--no-aggregate", action="store_true",
        help="Skip municipality-level aggregation",
    )
    parser.add_argument(
        "--clear-checkpoint", action="store_true",
        help="Clear checkpoint and start fresh",
    )
    args = parser.parse_args()

    if args.clear_checkpoint and CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        print("Cleared checkpoint.")

    # Load settlement data
    with open(PROCESSED_DIR / "settlement_points.json") as f:
        settlements = json.load(f)
    print(f"Loaded {len(settlements)} settlement points")

    # Validate API key
    if not args.dry_run:
        if not validate_geoapify_key():
            sys.exit(1)

    # Fetch driving times
    results = fetch_all_driving_times(
        settlements,
        max_batches=args.max_batches,
        dry_run=args.dry_run,
    )

    if args.dry_run or results is None:
        return

    # Check if all batches are complete
    n_batches = (len(settlements) + GEOAPIFY_BATCH_SOURCES - 1) // GEOAPIFY_BATCH_SOURCES
    checkpoint = load_checkpoint()
    all_complete = checkpoint["completed_batches"] >= n_batches

    if not all_complete:
        print(f"\n  {checkpoint['completed_batches']}/{n_batches} batches complete. "
              f"Run again to continue ({n_batches - checkpoint['completed_batches']} remaining).")

    # Save settlement-level driving times
    drive_path = PROCESSED_DIR / "settlement_travel_times_driving.json"
    with open(drive_path, "w") as f:
        json.dump(results, f)
    print(f"\nSaved settlement-level driving times to {drive_path}")
    print(f"  {len(results)} settlements with driving times")

    # Spot check
    spot_check(results)

    # Aggregate to municipality level
    if not args.no_aggregate:
        with open(PROCESSED_DIR / "settlement_municipality_map.json") as f:
            mapping = json.load(f)
        muni_to_settlements = mapping["municipality_to_settlements"]

        muni_times = aggregate_to_municipalities(results, muni_to_settlements)

        # Load existing travel_times.json and update driving section
        tt_path = PROCESSED_DIR / "travel_times.json"
        if tt_path.exists():
            with open(tt_path) as f:
                travel_times = json.load(f)
        else:
            travel_times = {"driving": {}, "public_transport": {}}

        travel_times["driving"] = muni_times
        travel_times["driving_source"] = "geoapify_with_traffic"

        with open(tt_path, "w") as f:
            json.dump(travel_times, f)
        print(f"Saved aggregated travel times to {tt_path}")
        print(f"  {len(muni_times)} municipalities with driving times")

        # Stats
        all_times = [v for m in muni_times.values() for v in m.values() if v is not None]
        if all_times:
            avg_min = sum(all_times) / len(all_times) / 60
            min_min = min(all_times) / 60
            max_min = max(all_times) / 60
            print(f"  Driving time stats: min={min_min:.0f} min, avg={avg_min:.0f} min, max={max_min:.0f} min")


if __name__ == "__main__":
    main()
