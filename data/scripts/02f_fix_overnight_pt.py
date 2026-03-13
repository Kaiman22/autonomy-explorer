#!/usr/bin/env python3
"""
Step 2f: Fix overnight PT connections in SBB data.

The original 02e script queried "arrive BY 08:00" (isArrivalTime=1),
which for remote settlements returns overnight connections (depart 11 PM,
arrive 7:50 AM → 9 hours) instead of the real morning commute (~2-3h).

This affects ~50% of Geneva pairs, ~46% of Lugano pairs, ~32% of St. Gallen,
and ~24% of Lausanne pairs — a massive data quality issue.

This script:
  1. Identifies pairs with suspected overnight connections
  2. Re-queries the SBB API using DEPARTURE-based queries (isArrivalTime=0)
     at 06:30 and 07:30 (morning commute times)
  3. Filters out any connection that departs before 05:00 (overnight)
  4. Keeps the shorter of the old and new values

Detection criteria for overnight connections:
  - PT excess over car > 3 hours (10800s) AND PT/car ratio > 2.0
    → catches overnight waits where PT includes hours of idle time
  - OR car is null AND PT > 8 hours (car-free settlement, likely overnight)

Supports checkpoint/resume, VPN/Tor rotation (same as 02e).

Usage:
  python 02f_fix_overnight_pt.py --dry-run        # show what would be re-fetched
  python 02f_fix_overnight_pt.py                   # run (direct IP)
  python 02f_fix_overnight_pt.py --vpn tor         # run with Tor rotation
  python 02f_fix_overnight_pt.py --resume          # resume from checkpoint
  python 02f_fix_overnight_pt.py --resume --vpn tor
"""
import argparse
import json
import re
import socket
import subprocess
import sys
import threading
import time as time_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

from config import CITIES, PROCESSED_DIR

# --- Configuration ---

SBB_API_BASE = "https://transport.opendata.ch/v1"

# Departure times for re-fetch (morning commute)
DEPARTURE_TIMES = [
    ("2026-03-02", "06:30"),  # Monday morning
    ("2026-03-03", "07:30"),  # Tuesday morning
]

# SBB API station names for target cities
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

CHECKPOINT_PATH = PROCESSED_DIR / "sbb_fix_checkpoint.json"
INITIAL_INTERVAL = 2.0  # start at 2s between requests
MIN_INTERVAL = 1.5      # fastest we'll go after sustained success
MAX_INTERVAL = 10.0     # slowest after rate limit
MAX_CONCURRENT = 1      # single-threaded (SBB API rate limits aggressively)
MAX_CONSECUTIVE_429 = 3
DAILY_LIMIT_PAUSE = 120 # 2 min pause on sustained rate limit

# Overnight detection thresholds
EXCESS_THRESHOLD = 10800    # 3 hours excess over car time (in seconds)
RATIO_THRESHOLD = 2.0       # PT/car > this AND excess > threshold = suspect
CARFREE_ABS_THRESHOLD = 28800  # 8 hours absolute for car-free settlements


def parse_duration(duration_str):
    """Parse SBB duration string like '00d03:24:00' to seconds."""
    if not duration_str:
        return None
    m = re.match(r"(\d+)d(\d+):(\d+):(\d+)", duration_str)
    if m:
        days, hours, mins, secs = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        return days * 86400 + hours * 3600 + mins * 60 + secs
    return None


def parse_datetime(dt_str):
    """Parse ISO datetime string like '2026-03-02T06:54:00+0100'."""
    if not dt_str:
        return None
    try:
        # Handle timezone offset format variations
        dt_str = dt_str.replace("+0100", "+01:00").replace("+0200", "+02:00")
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None


# --- VPN/Tor rotation (same as 02e) ---

_consecutive_429s = 0
_rate_lock = threading.Lock()
_used_ips = set()
_ratelimited_ips = set()
_vpn_provider = "none"

TOR_SOCKS_PROXY = "socks5h://127.0.0.1:9050"
TOR_CONTROL_PORT = 9051
TOR_CONTROL_PASSWORD = "tor_rotate_123"


def _get_tor_session():
    session = requests.Session()
    session.proxies = {"http": TOR_SOCKS_PROXY, "https": TOR_SOCKS_PROXY}
    return session


def _rotate_tor_circuit():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("127.0.0.1", TOR_CONTROL_PORT))
        s.send(f'AUTHENTICATE "{TOR_CONTROL_PASSWORD}"\r\n'.encode())
        resp = s.recv(256).decode().strip()
        if "250 OK" not in resp:
            s.close()
            return False
        s.send(b"SIGNAL NEWNYM\r\n")
        resp = s.recv(256).decode().strip()
        s.close()
        if "250 OK" in resp:
            time_mod.sleep(8)
            return True
        return False
    except Exception:
        return False


def _get_current_ip():
    try:
        if _vpn_provider == "tor":
            return _get_tor_session().get("https://api.ipify.org", timeout=15).text.strip()
        return requests.get("https://api.ipify.org", timeout=10).text.strip()
    except Exception:
        return None


def _rotate_vpn():
    global _used_ips, _ratelimited_ips
    if _vpn_provider == "tor":
        current_ip = _get_current_ip()
        if current_ip:
            _ratelimited_ips.add(current_ip)
        for attempt in range(50):
            if not _rotate_tor_circuit():
                continue
            new_ip = _get_current_ip()
            if new_ip and new_ip not in _ratelimited_ips:
                print(f"    New Tor IP: {new_ip}", flush=True)
                return True
        return False
    if _vpn_provider == "protonvpn":
        current_ip = _get_current_ip()
        if current_ip:
            _used_ips.add(current_ip)
        for attempt in range(20):
            subprocess.run(["protonvpn-cli", "disconnect"], capture_output=True, timeout=30)
            time_mod.sleep(2)
            result = subprocess.run(["protonvpn-cli", "connect", "--random"],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                continue
            time_mod.sleep(5)
            new_ip = _get_current_ip()
            if new_ip and new_ip not in _used_ips:
                _used_ips.add(new_ip)
                print(f"    New IP: {new_ip}", flush=True)
                return True
        return False
    return False


def _handle_daily_limit():
    global _consecutive_429s
    print(f"\n    *** DAILY RATE LIMIT DETECTED ***", flush=True)
    if _vpn_provider != "none" and _rotate_vpn():
        _consecutive_429s = 0
        return
    print(f"    Pausing for {DAILY_LIMIT_PAUSE // 60} min...", flush=True)
    time_mod.sleep(DAILY_LIMIT_PAUSE)
    _consecutive_429s = 0
    _used_ips.clear()
    _ratelimited_ips.clear()


def fetch_departure_connection(from_coords, to_station, date, time_str, timeout=30):
    """
    Fetch connection using DEPARTURE time (isArrivalTime=0).
    Returns (duration_seconds, departure_hour) or (None, None).
    Filters out overnight connections (departure before 05:00).
    """
    global _consecutive_429s
    params = {
        "from": f"{from_coords[0]},{from_coords[1]}",
        "to": to_station,
        "date": date,
        "time": time_str,
        "isArrivalTime": 0,  # DEPARTURE time
        "limit": 4,  # Get a few options
    }
    max_retries = 5
    http_client = _get_tor_session() if _vpn_provider == "tor" else requests

    for attempt in range(max_retries + 1):
        try:
            adaptive_rate_wait()
            resp = http_client.get(f"{SBB_API_BASE}/connections", params=params, timeout=timeout)

            if resp.status_code == 429 or resp.status_code >= 500:
                rate_limit_hit()
                with _rate_lock:
                    _consecutive_429s += 1
                    consec = _consecutive_429s
                if consec >= MAX_CONSECUTIVE_429:
                    _handle_daily_limit()
                    continue
                if attempt < max_retries:
                    wait = 15 * (2 ** attempt)
                    time_mod.sleep(wait)
                    continue
                return None, None

            resp.raise_for_status()
            rate_limit_ok()
            with _rate_lock:
                _consecutive_429s = 0
            data = resp.json()
            connections = data.get("connections", [])
            if not connections:
                return None, None

            # Find the best NON-overnight connection
            best_duration = None
            best_dep_hour = None
            for conn in connections:
                duration = parse_duration(conn.get("duration"))
                if duration is None:
                    continue

                # Check departure time
                dep_dt = parse_datetime(conn.get("from", {}).get("departure"))
                if dep_dt:
                    dep_hour = dep_dt.hour + dep_dt.minute / 60.0
                    # Reject overnight: departure before 05:00
                    if dep_hour < 5.0:
                        continue
                    # Reject if duration includes days (overnight train)
                    dur_str = conn.get("duration", "")
                    dur_m = re.match(r"(\d+)d", dur_str)
                    if dur_m and int(dur_m.group(1)) > 0:
                        continue

                if best_duration is None or duration < best_duration:
                    best_duration = duration
                    best_dep_hour = dep_dt.hour if dep_dt else None

            return best_duration, best_dep_hour

        except requests.exceptions.Timeout:
            if attempt < max_retries:
                time_mod.sleep(30 * (2 ** attempt))
                continue
            return None, None
        except requests.exceptions.RequestException:
            if attempt < max_retries:
                time_mod.sleep(30 * (2 ** attempt))
                continue
            return None, None

    return None, None


def identify_suspect_pairs(pt_data, drive_data):
    """
    Identify settlement-city pairs with suspected overnight connections.

    Criteria:
      - PT excess over car > 3h AND PT/car ratio > 2.0
        (arrival-based queries returned overnight connections where PT includes
        hours of idle waiting at a station or overnight train)
      - Car is null AND PT > 8h (car-free settlement, likely overnight)

    Re-querying with departure-based approach will confirm correct time.
    False positives are safe — if the current time is already correct,
    the departure query won't produce a shorter time and we keep the old value.
    """
    cities = list(CITY_STATION_NAMES.keys())
    suspects = []

    for uuid in pt_data:
        for city in cities:
            pt_s = pt_data[uuid].get(city)
            if pt_s is None:
                continue

            dr_s = drive_data.get(uuid, {}).get(city)

            is_suspect = False
            reason = ""

            if dr_s and dr_s > 0:
                ratio = pt_s / dr_s
                excess = pt_s - dr_s
                if excess > EXCESS_THRESHOLD and ratio > RATIO_THRESHOLD:
                    is_suspect = True
                    reason = f"ratio={ratio:.1f},excess={excess/60:.0f}m,pt={pt_s/60:.0f}m"
            elif dr_s is None and pt_s > CARFREE_ABS_THRESHOLD:
                # Car-free settlement with PT > 8h — likely overnight
                is_suspect = True
                reason = f"no_car,pt={pt_s/60:.0f}m"

            if is_suspect:
                suspects.append((uuid, city, pt_s, dr_s, reason))

    return suspects


_rate_state = {
    'lock': threading.Lock(),
    'last_request': 0.0,
    'interval': INITIAL_INTERVAL,
    'success_streak': 0,
}

def adaptive_rate_wait():
    """Adaptive rate limiter: slows down on 429, speeds up after sustained success."""
    with _rate_state['lock']:
        now = time_mod.time()
        elapsed = now - _rate_state['last_request']
        if elapsed < _rate_state['interval']:
            time_mod.sleep(_rate_state['interval'] - elapsed)
        _rate_state['last_request'] = time_mod.time()

def rate_limit_hit():
    """Called when we get a 429 — doubles the interval."""
    with _rate_state['lock']:
        _rate_state['interval'] = min(_rate_state['interval'] * 2, MAX_INTERVAL)
        _rate_state['success_streak'] = 0
        print(f"    Rate limit hit, interval → {_rate_state['interval']:.1f}s", flush=True)

def rate_limit_ok():
    """Called on successful request — gradually reduces interval."""
    with _rate_state['lock']:
        _rate_state['success_streak'] += 1
        if _rate_state['success_streak'] >= 20 and _rate_state['interval'] > MIN_INTERVAL:
            _rate_state['interval'] = max(_rate_state['interval'] * 0.9, MIN_INTERVAL)
            _rate_state['success_streak'] = 0


def main():
    parser = argparse.ArgumentParser(description="Fix overnight PT connections")
    parser.add_argument("--dry-run", action="store_true", help="Show suspects, don't re-fetch")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--vpn", choices=["protonvpn", "tor", "none"], default="none")
    parser.add_argument("--limit", type=int, default=None, help="Max pairs to re-fetch")
    args = parser.parse_args()

    global _vpn_provider
    _vpn_provider = args.vpn
    if _vpn_provider != "none":
        ip = _get_current_ip()
        print(f"IP rotation: {_vpn_provider} (current IP: {ip})")

    # Load data
    with open(PROCESSED_DIR / "settlement_travel_times_pt_sbb.json") as f:
        pt_data = json.load(f)
    with open(PROCESSED_DIR / "settlement_travel_times_driving.json") as f:
        drive_data = json.load(f)
    with open(PROCESSED_DIR / "settlement_points.json") as f:
        settlements = json.load(f)

    name_map = {s["uuid"]: f"{s['name']} ({s['canton']})" for s in settlements}
    coord_map = {s["uuid"]: (s["lat"], s["lon"]) for s in settlements}

    # Identify suspects
    suspects = identify_suspect_pairs(pt_data, drive_data)
    print(f"Suspect pairs: {len(suspects)}")

    # Group by settlement for reporting
    by_settlement = {}
    for uuid, city, pt_s, dr_s, reason in suspects:
        by_settlement.setdefault(uuid, []).append((city, pt_s, dr_s, reason))

    print(f"Suspect settlements: {len(by_settlement)}")

    if args.dry_run:
        print(f"\nTop 30 most affected settlements:")
        for uuid, pairs in sorted(by_settlement.items(), key=lambda x: -len(x[1]))[:30]:
            print(f"  {name_map.get(uuid, uuid)[:45]:45s}: {len(pairs)} bad pairs")
            for city, pt_s, dr_s, reason in pairs[:3]:
                dr_str = f"{dr_s/60:.0f}m" if dr_s else "N/A"
                print(f"    → {city:12s}: PT={pt_s/60:.0f}m, Car={dr_str} [{reason}]")

        # Estimate
        est_calls = len(suspects) * len(DEPARTURE_TIMES)
        # Global rate limit: one request per GLOBAL_INTERVAL
        est_sec = est_calls * GLOBAL_INTERVAL + est_calls * 1.5  # interval + avg API response time
        est_min = est_sec / 60
        print(f"\nEstimate: {est_calls} API calls, ~{GLOBAL_INTERVAL}s interval, ~{est_min:.0f}min")
        return

    # Load checkpoint
    checkpoint = {}
    if args.resume and CHECKPOINT_PATH.exists():
        with open(CHECKPOINT_PATH) as f:
            checkpoint = json.load(f)
        print(f"Resuming: {len(checkpoint)} pairs already re-fetched")

    # Filter out already-done pairs
    todo = []
    for uuid, city, pt_s, dr_s, reason in suspects:
        key = f"{uuid}|{city}"
        if key not in checkpoint:
            todo.append((uuid, city, pt_s, dr_s, reason))

    if args.limit:
        todo = todo[:args.limit]

    print(f"Pairs to re-fetch: {len(todo)}")

    if not todo:
        print("Nothing to re-fetch!")
    else:
        start_time = time_mod.time()
        fixed = 0
        kept = 0
        failed = 0
        counter_lock = threading.Lock()
        checkpoint_lock = threading.Lock()
        processed = [0]  # mutable counter

        def process_pair(item):
            """Process one (uuid, city) pair — called from thread pool."""
            idx, (uuid, city, old_pt, dr_s, reason) = item
            coords = coord_map.get(uuid)
            if not coords:
                return None

            station = CITY_STATION_NAMES[city]
            best_new = None

            for date, time_str in DEPARTURE_TIMES:
                duration, dep_hour = fetch_departure_connection(coords, station, date, time_str)
                if duration is not None:
                    if best_new is None or duration < best_new:
                        best_new = duration

            return (uuid, city, old_pt, best_new, idx)

        concurrency = MAX_CONCURRENT if _vpn_provider == "none" else 1
        print(f"Using {concurrency} concurrent workers")

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(process_pair, (i, t)): i
                       for i, t in enumerate(todo)}

            for future in as_completed(futures):
                result = future.result()
                if result is None:
                    continue

                uuid, city, old_pt, best_new, idx = result
                key = f"{uuid}|{city}"

                with counter_lock:
                    processed[0] += 1
                    p = processed[0]

                    if best_new is not None and best_new < old_pt:
                        checkpoint[key] = best_new
                        fixed += 1
                        improvement = (old_pt - best_new) / 60
                        print(f"  [{p}/{len(todo)}] FIXED {name_map.get(uuid, '?')[:30]:30s} \u2192 {city:12s}: "
                              f"{old_pt/60:.0f}m \u2192 {best_new/60:.0f}m (saved {improvement:.0f}m)")
                    elif best_new is not None:
                        checkpoint[key] = old_pt
                        kept += 1
                    else:
                        checkpoint[key] = old_pt
                        failed += 1

                    # Checkpoint every 50 pairs
                    if p % 50 == 0:
                        with open(CHECKPOINT_PATH, "w") as f:
                            json.dump(checkpoint, f)
                        elapsed = time_mod.time() - start_time
                        rate = p / elapsed * 60
                        eta_min = (len(todo) - p) / rate if rate > 0 else 0
                        print(f"  Checkpoint: {p}/{len(todo)}, {fixed} fixed, "
                              f"{rate:.1f}/min, ETA {eta_min:.0f}min")
                        sys.stdout.flush()

        # Final checkpoint
        with open(CHECKPOINT_PATH, "w") as f:
            json.dump(checkpoint, f)

        print(f"\nResults: {fixed} fixed, {kept} kept (new wasn't better), {failed} failed")

    # Apply all fixes to PT data
    applied = 0
    for key, new_val in checkpoint.items():
        uuid, city = key.split("|")
        if uuid in pt_data:
            old_val = pt_data[uuid].get(city)
            if old_val is not None and new_val < old_val:
                pt_data[uuid][city] = new_val
                applied += 1

    print(f"Applied {applied} improvements to PT data")

    # Save
    output_path = PROCESSED_DIR / "settlement_travel_times_pt_sbb.json"
    with open(output_path, "w") as f:
        json.dump(pt_data, f)
    print(f"Saved to {output_path}")

    # Also save as compatibility file
    compat_path = PROCESSED_DIR / "settlement_travel_times_pt.json"
    with open(compat_path, "w") as f:
        json.dump(pt_data, f)
    print(f"Saved to {compat_path}")

    # Re-aggregate to municipality level
    with open(PROCESSED_DIR / "settlement_municipality_map.json") as f:
        mapping = json.load(f)

    tt_path = PROCESSED_DIR / "travel_times.json"
    if tt_path.exists():
        with open(tt_path) as f:
            travel_times = json.load(f)

        muni_to_settlements = mapping["municipality_to_settlements"]
        city_list = list(CITIES.keys())
        for muni_id, settlement_uuids in muni_to_settlements.items():
            if muni_id not in travel_times.get("public_transport", {}):
                continue
            for city_id in city_list:
                times = []
                for suuid in settlement_uuids:
                    t = pt_data.get(suuid, {}).get(city_id)
                    if t is not None:
                        times.append(t)
                travel_times["public_transport"][muni_id][city_id] = min(times) if times else None

        with open(tt_path, "w") as f:
            json.dump(travel_times, f)
        print(f"Updated municipality-level PT times in {tt_path}")

    # Cleanup checkpoint
    if CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        print("Checkpoint cleaned up")


if __name__ == "__main__":
    main()
