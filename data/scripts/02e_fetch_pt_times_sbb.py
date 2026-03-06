#!/usr/bin/env python3
"""
Step 2e: Fetch public transport travel times via Swiss Open Transport API (SBB).

Replaces TravelTime API with real SBB/PostBus timetable data via
transport.opendata.ch. Advantages over TravelTime:
  - No travel time cap (TravelTime caps at ~3 hours)
  - Includes cross-border routes (e.g. Brig → Domodossola → Lugano via Simplon)
  - Uses actual SBB timetable data, not approximated routes

For each settlement × city pair, queries the /v1/connections endpoint
for an arrival at the city at the specified time. Averages across
multiple departure dates for schedule robustness.

Supports resume: saves a checkpoint after every batch of settlements.
If interrupted, re-running picks up where it left off.

Usage:
  python 02e_fetch_pt_times_sbb.py                          # full run
  python 02e_fetch_pt_times_sbb.py --resume                 # resume from checkpoint
  python 02e_fetch_pt_times_sbb.py --vpn protonvpn          # auto-rotate IP via ProtonVPN
  python 02e_fetch_pt_times_sbb.py --vpn protonvpn --resume # resume with VPN rotation
  python 02e_fetch_pt_times_sbb.py --dry-run                # test with 5 settlements

Outputs:
  - data/processed/settlement_travel_times_pt_sbb.json (raw settlement-level)
  - data/processed/settlement_travel_times_pt.json (copied for compatibility)
  - data/processed/travel_times.json (updated municipality-level)
"""
import argparse
import json
import re
import subprocess
import sys
import time as time_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from config import CITIES, PROCESSED_DIR

# --- Configuration ---

SBB_API_BASE = "https://transport.opendata.ch/v1"

# Commuter arrival times (arrive at destination by this time).
# Using fewer times than TravelTime since SBB data is deterministic
# (same timetable every week), so variance is lower.
ARRIVAL_TIMES = [
    ("2026-03-02", "08:00"),  # Monday 8:00
    ("2026-03-03", "08:00"),  # Tuesday 8:00
    ("2026-03-04", "08:30"),  # Wednesday 8:30 (flex start)
]

# SBB API station names for target cities (more reliable than coords)
CITY_STATION_NAMES = {
    "zurich": "Zürich HB",
    "bern": "Bern",
    "basel": "Basel SBB",
    "luzern": "Luzern",
    "geneve": "Genève",
    "lausanne": "Lausanne",
    "stgallen": "St. Gallen",
    "lugano": "Lugano",
    "winterthur": "Winterthur",
    "biel": "Biel/Bienne",
}

CHECKPOINT_PATH = PROCESSED_DIR / "sbb_checkpoint.json"
OUTPUT_PATH = PROCESSED_DIR / "settlement_travel_times_pt_sbb.json"

# Rate limiting: timetable.search.ch has a DAILY request quota (undocumented).
# Once exceeded, all requests return 429 with "Too many requests today".
# The limit appears to be ~1000-2000 requests/day per IP.
# Strategy: 5s between requests, detect daily limit and pause until reset.
MAX_RPS = 1
REQUEST_INTERVAL = 5.0  # seconds between requests (conservative)
DAILY_LIMIT_PAUSE = 3600  # 1 hour pause when daily limit detected
MAX_CONSECUTIVE_429 = 5   # consecutive 429s before assuming daily limit hit


def parse_duration(duration_str):
    """Parse SBB duration string like '00d03:24:00' to seconds."""
    if not duration_str:
        return None
    # Format: DDdHH:MM:SS
    m = re.match(r"(\d+)d(\d+):(\d+):(\d+)", duration_str)
    if m:
        days, hours, mins, secs = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        return days * 86400 + hours * 3600 + mins * 60 + secs
    return None


# Global state for daily-limit detection and VPN rotation
_consecutive_429s = 0
_used_ips = set()
_vpn_provider = "none"  # Set via --vpn flag


def _get_current_ip():
    """Get current public IP address."""
    try:
        return requests.get("https://api.ipify.org", timeout=10).text.strip()
    except Exception:
        return None


def _rotate_vpn():
    """Rotate VPN to get a fresh IP. Returns True on success."""
    global _used_ips
    if _vpn_provider != "protonvpn":
        return False

    # Record current IP as used/exhausted
    current_ip = _get_current_ip()
    if current_ip:
        _used_ips.add(current_ip)

    for attempt in range(20):  # Try up to 20 different servers
        print(f"    VPN rotation attempt {attempt + 1}...", flush=True)
        subprocess.run(
            ["protonvpn-cli", "disconnect"],
            capture_output=True, timeout=30,
        )
        time_mod.sleep(2)
        result = subprocess.run(
            ["protonvpn-cli", "connect", "--random"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            print(f"    VPN connect failed: {result.stderr.strip()}", flush=True)
            time_mod.sleep(5)
            continue

        time_mod.sleep(5)  # Wait for connection to stabilize
        new_ip = _get_current_ip()

        if new_ip and new_ip not in _used_ips:
            _used_ips.add(new_ip)
            print(f"    New IP: {new_ip} ({len(_used_ips)} IPs used total)", flush=True)
            return True
        elif new_ip:
            print(f"    Duplicate IP: {new_ip} (already rate-limited), trying another...", flush=True)

    print(f"    All {len(_used_ips)} VPN IPs exhausted for today.", flush=True)
    return False


def _handle_daily_limit():
    """Handle daily rate limit: rotate VPN or pause."""
    global _consecutive_429s
    import datetime
    now = datetime.datetime.now()
    print(f"\n    *** DAILY RATE LIMIT DETECTED at {now.strftime('%H:%M:%S')} ***", flush=True)

    # Try VPN rotation first
    if _vpn_provider != "none" and _rotate_vpn():
        print("    VPN rotated successfully, continuing with new IP...", flush=True)
        _consecutive_429s = 0
        return

    # Fallback: sleep and retry later
    print(f"    Pausing for {DAILY_LIMIT_PAUSE // 60} minutes before retrying...", flush=True)
    time_mod.sleep(DAILY_LIMIT_PAUSE)
    _consecutive_429s = 0
    # After sleeping, clear used IPs (daily limits may have reset)
    _used_ips.clear()
    print(f"    Resuming after pause at {datetime.datetime.now().strftime('%H:%M:%S')}", flush=True)


def fetch_connection(from_coords, to_station, date, time_str, timeout=30):
    """
    Fetch a single connection from coordinates to a station.
    Returns travel time in seconds, or None if no connection found.
    Retries with exponential backoff on 429/5xx errors.
    Detects daily rate limits and pauses accordingly.
    """
    global _consecutive_429s
    params = {
        "from": f"{from_coords[0]},{from_coords[1]}",  # lat,lon
        "to": to_station,
        "date": date,
        "time": time_str,
        "isArrivalTime": 1,
        "limit": 1,
    }
    max_retries = 5
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(
                f"{SBB_API_BASE}/connections",
                params=params,
                timeout=timeout,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                _consecutive_429s += 1

                # Check if this looks like a daily limit
                if _consecutive_429s >= MAX_CONSECUTIVE_429:
                    _handle_daily_limit()
                    # After pause, retry this request from scratch
                    continue

                if attempt < max_retries:
                    wait = 30 * (2 ** attempt)  # 30s, 60s, 120s, 240s, 480s
                    print(f"    [429] {to_station}, retry {attempt+1}/{max_retries} in {wait}s", flush=True)
                    time_mod.sleep(wait)
                    continue
                else:
                    print(f"    [FAIL] {to_station} after {max_retries} retries", flush=True)
                    return None

            resp.raise_for_status()
            _consecutive_429s = 0  # Reset on success
            data = resp.json()

            connections = data.get("connections", [])
            if not connections:
                return None

            # Take the first (best) connection
            duration_str = connections[0].get("duration")
            return parse_duration(duration_str)

        except requests.exceptions.Timeout:
            if attempt < max_retries:
                wait = 30 * (2 ** attempt)
                print(f"    [TIMEOUT] {to_station}, retry {attempt+1}/{max_retries} in {wait}s", flush=True)
                time_mod.sleep(wait)
                continue
            return None
        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                wait = 30 * (2 ** attempt)
                print(f"    [ERROR] {to_station}: {e}, retry {attempt+1}/{max_retries} in {wait}s", flush=True)
                time_mod.sleep(wait)
                continue
            return None
    return None


def fetch_settlement_times(settlement, city_ids, arrival_times, rate_limiter):
    """
    Fetch PT times from one settlement to all cities, averaged across arrival times.
    Returns dict: { city_id: avg_seconds_or_None }
    """
    lat, lon = settlement["lat"], settlement["lon"]
    result = {}

    for city_id in city_ids:
        station = CITY_STATION_NAMES[city_id]
        durations = []

        for date, time_str in arrival_times:
            # Rate limiting
            rate_limiter()

            seconds = fetch_connection((lat, lon), station, date, time_str)
            if seconds is not None:
                durations.append(seconds)

        if durations:
            result[city_id] = round(sum(durations) / len(durations))
        else:
            result[city_id] = None

    return result


def create_rate_limiter():
    """Create a thread-safe rate limiter."""
    import threading
    lock = threading.Lock()
    last_request = [0.0]

    def wait():
        with lock:
            now = time_mod.time()
            elapsed = now - last_request[0]
            if elapsed < REQUEST_INTERVAL:
                time_mod.sleep(REQUEST_INTERVAL - elapsed)
            last_request[0] = time_mod.time()

    return wait


def load_checkpoint():
    """Load checkpoint (already-fetched settlements)."""
    if CHECKPOINT_PATH.exists():
        with open(CHECKPOINT_PATH) as f:
            return json.load(f)
    return {}


def save_checkpoint(results):
    """Save checkpoint to disk."""
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(results, f)


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
            muni_result[city_id] = min(times) if times else None
        muni_times[muni_id] = muni_result

    return muni_times


def main():
    parser = argparse.ArgumentParser(
        description="Fetch PT travel times via SBB/transport.opendata.ch API"
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from checkpoint (skip already-fetched settlements)"
    )
    parser.add_argument(
        "--workers", type=int, default=2,
        help="Number of parallel workers (default: 2, max recommended: 4)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Test with first 5 settlements only"
    )
    parser.add_argument(
        "--arrival-times", type=int, default=None,
        help="Number of arrival times to use (1-3, default: all 3)"
    )
    parser.add_argument(
        "--vpn", choices=["protonvpn", "none"], default="none",
        help="VPN provider for auto IP rotation on daily limit (default: none)"
    )
    args = parser.parse_args()

    # Configure VPN rotation
    global _vpn_provider
    _vpn_provider = args.vpn
    if _vpn_provider == "protonvpn":
        ip = _get_current_ip()
        if ip:
            _used_ips.add(ip)
            print(f"VPN mode: protonvpn (current IP: {ip})")
        else:
            print("VPN mode: protonvpn (couldn't detect current IP)")


    # Load settlement data
    with open(PROCESSED_DIR / "settlement_points.json") as f:
        settlements = json.load(f)

    with open(PROCESSED_DIR / "settlement_municipality_map.json") as f:
        mapping = json.load(f)

    muni_to_settlements = mapping["municipality_to_settlements"]
    city_ids = list(CITIES.keys())

    arrival_times = ARRIVAL_TIMES
    if args.arrival_times:
        arrival_times = ARRIVAL_TIMES[:args.arrival_times]

    print(f"Loaded {len(settlements)} settlements, {len(muni_to_settlements)} municipalities")
    print(f"Target cities: {len(city_ids)}")
    print(f"Arrival times: {len(arrival_times)}")
    print(f"Total API calls: ~{len(settlements) * len(city_ids) * len(arrival_times):,}")
    print(f"Workers: {args.workers}")
    est_hours = len(settlements) * len(city_ids) * len(arrival_times) * REQUEST_INTERVAL / 3600
    print(f"Estimated time: ~{est_hours:.1f} hours")
    print()

    if args.dry_run:
        settlements = settlements[:5]
        print("DRY RUN: using first 5 settlements only")

    # Load or initialize results
    results = load_checkpoint() if args.resume else {}
    already_done = len(results)
    if already_done > 0:
        print(f"Resuming: {already_done} settlements already fetched, {len(settlements) - already_done} remaining")

    # Filter to settlements not yet done
    todo = [s for s in settlements if s["uuid"] not in results]

    if not todo:
        print("All settlements already fetched!")
    else:
        rate_limiter = create_rate_limiter()
        batch_size = 50  # checkpoint every 50 settlements

        start_time = time_mod.time()
        completed = 0
        total_todo = len(todo)

        # Process in batches for checkpointing
        for batch_start in range(0, total_todo, batch_size):
            batch = todo[batch_start:batch_start + batch_size]

            if args.workers <= 1:
                # Sequential processing
                for settlement in batch:
                    times = fetch_settlement_times(
                        settlement, city_ids, arrival_times, rate_limiter
                    )
                    results[settlement["uuid"]] = times
                    completed += 1

                    # Log every settlement for monitoring
                    elapsed = time_mod.time() - start_time
                    rate = completed / elapsed * 60 if elapsed > 0 else 0
                    eta_s = (total_todo - completed) / (rate / 60) if rate > 0 else 0
                    eta_h = eta_s / 3600
                    non_null = sum(
                        1 for v in times.values() if v is not None
                    )
                    print(
                        f"  [{already_done + completed}/{len(settlements)}] "
                        f"{settlement['name']} ({settlement['canton']}): "
                        f"{non_null}/{len(city_ids)} cities reachable | "
                        f"{rate:.1f} settlements/min | ETA {eta_h:.1f}h"
                    )
                    sys.stdout.flush()
            else:
                # Parallel processing
                with ThreadPoolExecutor(max_workers=args.workers) as executor:
                    future_to_settlement = {}
                    for settlement in batch:
                        future = executor.submit(
                            fetch_settlement_times,
                            settlement, city_ids, arrival_times, rate_limiter
                        )
                        future_to_settlement[future] = settlement

                    for future in as_completed(future_to_settlement):
                        settlement = future_to_settlement[future]
                        try:
                            times = future.result()
                            results[settlement["uuid"]] = times
                        except Exception as e:
                            print(f"  ERROR {settlement['name']}: {e}")
                            results[settlement["uuid"]] = {c: None for c in city_ids}

                        completed += 1

                        if completed % 5 == 0:
                            elapsed = time_mod.time() - start_time
                            rate = completed / elapsed * 60 if elapsed > 0 else 0
                            eta_s = (total_todo - completed) / (rate / 60) if rate > 0 else 0
                            eta_h = eta_s / 3600
                            print(
                                f"  [{already_done + completed}/{len(settlements)}] "
                                f"{rate:.1f} settlements/min | ETA {eta_h:.1f}h"
                            )
                            sys.stdout.flush()

            # Checkpoint after each batch
            save_checkpoint(results)
            print(f"  Checkpoint saved ({already_done + completed}/{len(settlements)})")
            sys.stdout.flush()

    # --- Save final results ---
    print(f"\nSaving results for {len(results)} settlements...")

    # Save SBB-specific output
    with open(OUTPUT_PATH, "w") as f:
        json.dump(results, f)
    print(f"  Saved to {OUTPUT_PATH}")

    # Also save as the standard PT times file (replaces TravelTime data)
    compat_path = PROCESSED_DIR / "settlement_travel_times_pt.json"
    with open(compat_path, "w") as f:
        json.dump(results, f)
    print(f"  Saved to {compat_path}")

    # --- Aggregate to municipality level ---
    print("Aggregating to municipality level...")
    muni_pt = aggregate_to_municipalities(results, muni_to_settlements)

    # Update travel_times.json (preserve driving times)
    tt_path = PROCESSED_DIR / "travel_times.json"
    if tt_path.exists():
        with open(tt_path) as f:
            travel_times = json.load(f)
    else:
        travel_times = {"driving": {}, "public_transport": {}}

    travel_times["public_transport"] = muni_pt

    with open(tt_path, "w") as f:
        json.dump(travel_times, f)
    print(f"  Updated {tt_path}")

    # --- Print stats ---
    total_pairs = len(results) * len(city_ids)
    reachable_pairs = sum(
        1 for times in results.values()
        for v in times.values()
        if v is not None
    )
    print(f"\nResults:")
    print(f"  Settlements: {len(results)}")
    print(f"  Reachable pairs: {reachable_pairs}/{total_pairs} ({reachable_pairs/total_pairs*100:.1f}%)")

    # Per-city stats
    for city_id in city_ids:
        city_times = [
            results[uuid][city_id]
            for uuid in results
            if results[uuid].get(city_id) is not None
        ]
        if city_times:
            avg_min = sum(city_times) / len(city_times) / 60
            print(f"  {CITY_STATION_NAMES[city_id]}: {len(city_times)}/{len(results)} reachable, avg {avg_min:.0f} min")
        else:
            print(f"  {CITY_STATION_NAMES[city_id]}: 0 reachable")

    # Clean up checkpoint on success
    if CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        print("\nCheckpoint cleaned up (run complete)")


if __name__ == "__main__":
    main()
