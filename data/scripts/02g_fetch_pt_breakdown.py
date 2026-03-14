#!/usr/bin/env python3
"""
Step 2g: Fetch PT travel time breakdown via Swiss Open Transport API (SBB).

Extends 02e by extracting the full section-level breakdown:
  - walk_s:     total walking time (access + egress + transfer walks)
  - wait_s:     total waiting/transfer time (gaps between sections)
  - ivt_s:      total in-vehicle time (journey sections)
  - transfers:  number of transfers (from API)
  - total_s:    overall door-to-door travel time

Uses a single departure window (depart at 07:00 on a weekday) for the
breakdown. Uses departure-based queries (isArrivalTime=0) to avoid
the overnight connection bug that affects arrival-based queries for
distant origins. Total travel times from 02e (averaged over 3 windows)
remain the source of truth for the main model; this script adds
the component breakdown as supplementary data.

Supports resume: saves a checkpoint after every batch.

Usage:
  python 02g_fetch_pt_breakdown.py                      # full run
  python 02g_fetch_pt_breakdown.py --resume              # resume from checkpoint
  python 02g_fetch_pt_breakdown.py --vpn tor --resume    # resume with Tor rotation
  python 02g_fetch_pt_breakdown.py --dry-run             # test with 5 settlements

Outputs:
  - data/processed/settlement_pt_breakdown.json
"""
import argparse
import json
import re
import socket
import subprocess
import sys
import time as time_mod
from pathlib import Path

import requests

from config import CITIES, PROCESSED_DIR

# --- Configuration ---

SBB_API_BASE = "https://transport.opendata.ch/v1"

# Single departure time: Monday morning, depart at 07:00
# Using departure (isArrivalTime=0) avoids overnight connections that
# plague arrival-based queries for distant origins.
DEPARTURE_DATE = "2026-03-16"
DEPARTURE_TIME = "07:00"

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

CHECKPOINT_PATH = PROCESSED_DIR / "pt_breakdown_checkpoint.json"
OUTPUT_PATH = PROCESSED_DIR / "settlement_pt_breakdown.json"

# Rate limiting
REQUEST_INTERVAL = 3.0
MAX_CONSECUTIVE_429 = 5
DAILY_LIMIT_PAUSE = 3600

# Global state for rate limit handling
_consecutive_429s = 0
_used_ips = set()
_ratelimited_ips = set()
_vpn_provider = "none"

# Tor config
TOR_SOCKS_PROXY = "socks5h://127.0.0.1:9050"
TOR_CONTROL_PORT = 9051
TOR_CONTROL_PASSWORD = "tor_rotate_123"


def parse_duration(duration_str):
    """Parse SBB duration string like '00d03:24:00' to seconds."""
    if not duration_str:
        return None
    m = re.match(r"(\d+)d(\d+):(\d+):(\d+)", duration_str)
    if m:
        days, hours, mins, secs = (
            int(m.group(1)), int(m.group(2)),
            int(m.group(3)), int(m.group(4))
        )
        return days * 86400 + hours * 3600 + mins * 60 + secs
    return None


def extract_breakdown(connection):
    """
    Extract walk/wait/ivt/transfers from a connection's sections.

    Returns dict: {total_s, walk_s, ivt_s, wait_s, transfers}
    """
    total_s = parse_duration(connection.get("duration"))
    transfers = connection.get("transfers", 0)
    sections = connection.get("sections", [])

    walk_s = 0
    ivt_s = 0

    for sec in sections:
        dep_ts = sec.get("departure", {}).get("departureTimestamp")
        arr_ts = sec.get("arrival", {}).get("arrivalTimestamp")
        if dep_ts is None or arr_ts is None:
            continue
        sec_dur = arr_ts - dep_ts
        if sec_dur < 0:
            continue

        has_journey = sec.get("journey") is not None
        if has_journey:
            ivt_s += sec_dur
        else:
            # Walk section (access, egress, or transfer walk)
            walk_s += sec_dur

    # Wait time = total minus walk minus ivt (time spent on platforms, etc.)
    if total_s is not None:
        wait_s = max(0, total_s - walk_s - ivt_s)
    else:
        wait_s = 0

    return {
        "total_s": total_s,
        "walk_s": walk_s,
        "ivt_s": ivt_s,
        "wait_s": wait_s,
        "transfers": transfers,
    }


# --- VPN/Tor rotation (same as 02e) ---

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
            return _get_tor_session().get(
                "https://api.ipify.org", timeout=15
            ).text.strip()
        return requests.get("https://api.ipify.org", timeout=10).text.strip()
    except Exception:
        return None


def _rotate_vpn():
    global _used_ips, _ratelimited_ips
    if _vpn_provider == "tor":
        current_ip = _get_current_ip()
        if current_ip:
            _ratelimited_ips.add(current_ip)
            print(
                f"    Rate-limited IP: {current_ip} "
                f"({len(_ratelimited_ips)} blocked)", flush=True
            )
        for attempt in range(50):
            print(f"    Tor rotation {attempt + 1}...", flush=True)
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
            print(f"    VPN rotation {attempt + 1}...", flush=True)
            subprocess.run(
                ["protonvpn-cli", "disconnect"],
                capture_output=True, timeout=30
            )
            time_mod.sleep(2)
            result = subprocess.run(
                ["protonvpn-cli", "connect", "--random"],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                time_mod.sleep(5)
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
    import datetime
    now = datetime.datetime.now()
    print(f"\n    *** DAILY LIMIT at {now.strftime('%H:%M:%S')} ***", flush=True)
    if _vpn_provider != "none" and _rotate_vpn():
        print("    Rotated IP, continuing...", flush=True)
        _consecutive_429s = 0
        return
    print(f"    Pausing {DAILY_LIMIT_PAUSE // 60} min...", flush=True)
    time_mod.sleep(DAILY_LIMIT_PAUSE)
    _consecutive_429s = 0
    _used_ips.clear()
    _ratelimited_ips.clear()


# --- API fetch ---

def fetch_connection_breakdown(from_coords, to_station, timeout=30):
    """
    Fetch a single connection and return the full breakdown.
    Returns dict with {total_s, walk_s, ivt_s, wait_s, transfers} or None.
    """
    global _consecutive_429s
    params = {
        "from": f"{from_coords[0]},{from_coords[1]}",
        "to": to_station,
        "date": DEPARTURE_DATE,
        "time": DEPARTURE_TIME,
        "isArrivalTime": 0,  # DEPARTURE-based — avoids overnight connections
        "limit": 4,  # get a few options to pick the best non-overnight one
    }
    max_retries = 5
    http_client = _get_tor_session() if _vpn_provider == "tor" else requests

    for attempt in range(max_retries + 1):
        try:
            resp = http_client.get(
                f"{SBB_API_BASE}/connections",
                params=params, timeout=timeout,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                _consecutive_429s += 1
                if _consecutive_429s >= MAX_CONSECUTIVE_429:
                    _handle_daily_limit()
                    continue
                if attempt < max_retries:
                    wait = 30 * (2 ** attempt)
                    print(
                        f"    [429] {to_station}, retry "
                        f"{attempt+1}/{max_retries} in {wait}s", flush=True
                    )
                    time_mod.sleep(wait)
                    continue
                return None

            resp.raise_for_status()
            _consecutive_429s = 0
            data = resp.json()

            connections = data.get("connections", [])
            if not connections:
                return None

            # Pick the shortest non-overnight connection
            best = None
            best_total = None
            for conn in connections:
                bd = extract_breakdown(conn)
                if bd is None or bd["total_s"] is None:
                    continue
                # Skip overnight: duration includes a full day
                dur_str = conn.get("duration", "")
                dur_m = re.match(r"(\d+)d", dur_str)
                if dur_m and int(dur_m.group(1)) > 0:
                    continue
                # Skip connections departing before 05:00
                dep_str = conn.get("from", {}).get("departure", "")
                if dep_str and "T" in dep_str:
                    hour_part = dep_str.split("T")[1][:2]
                    if hour_part.isdigit() and int(hour_part) < 5:
                        continue
                if best_total is None or bd["total_s"] < best_total:
                    best = bd
                    best_total = bd["total_s"]

            return best

        except requests.exceptions.Timeout:
            if attempt < max_retries:
                time_mod.sleep(30 * (2 ** attempt))
                continue
            return None
        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                wait = 30 * (2 ** attempt)
                print(f"    [ERROR] {to_station}: {e}, retry in {wait}s", flush=True)
                time_mod.sleep(wait)
                continue
            return None
    return None


def fetch_settlement_breakdown(settlement, city_ids, rate_limiter):
    """Fetch PT breakdown from one settlement to all cities."""
    lat, lon = settlement["lat"], settlement["lon"]
    result = {}
    for city_id in city_ids:
        rate_limiter()
        station = CITY_STATION_NAMES[city_id]
        breakdown = fetch_connection_breakdown((lat, lon), station)
        result[city_id] = breakdown  # None if no connection
    return result


def create_rate_limiter():
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
    if CHECKPOINT_PATH.exists():
        with open(CHECKPOINT_PATH) as f:
            return json.load(f)
    return {}


def save_checkpoint(results):
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(results, f)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch PT travel time breakdown (walk/wait/IVT/transfers)"
    )
    parser.add_argument("--resume", action="store_true",
                        help="Resume from checkpoint")
    parser.add_argument("--dry-run", action="store_true",
                        help="Test with first 5 settlements")
    parser.add_argument("--vpn", choices=["protonvpn", "tor", "none"],
                        default="none",
                        help="VPN/proxy for IP rotation (default: none)")
    args = parser.parse_args()

    global _vpn_provider
    _vpn_provider = args.vpn
    if _vpn_provider != "none":
        ip = _get_current_ip()
        print(f"IP rotation: {_vpn_provider} (current IP: {ip})")

    # Load settlement data
    with open(PROCESSED_DIR / "settlement_points.json") as f:
        settlements = json.load(f)

    city_ids = list(CITIES.keys())
    total_calls = len(settlements) * len(city_ids)

    print(f"Settlements: {len(settlements)}")
    print(f"Cities: {len(city_ids)}")
    print(f"Departure: {DEPARTURE_DATE} {DEPARTURE_TIME}")
    print(f"API calls: ~{total_calls:,}")
    est_hours = total_calls * REQUEST_INTERVAL / 3600
    print(f"Estimated time: ~{est_hours:.1f}h")
    print()

    if args.dry_run:
        settlements = settlements[:5]
        print("DRY RUN: 5 settlements only\n")

    # Load or init results
    results = load_checkpoint() if args.resume else {}
    already_done = len(results)
    if already_done:
        print(f"Resuming: {already_done} done, "
              f"{len(settlements) - already_done} remaining")

    todo = [s for s in settlements if s["uuid"] not in results]

    if not todo:
        print("All settlements already fetched!")
    else:
        rate_limiter = create_rate_limiter()
        batch_size = 50
        start_time = time_mod.time()
        completed = 0
        total_todo = len(todo)

        for batch_start in range(0, total_todo, batch_size):
            batch = todo[batch_start:batch_start + batch_size]

            for settlement in batch:
                breakdown = fetch_settlement_breakdown(
                    settlement, city_ids, rate_limiter
                )
                results[settlement["uuid"]] = breakdown
                completed += 1

                elapsed = time_mod.time() - start_time
                rate = completed / elapsed * 60 if elapsed > 0 else 0
                eta_h = (total_todo - completed) / (rate / 60) / 3600 if rate > 0 else 0
                reachable = sum(
                    1 for v in breakdown.values() if v is not None
                )
                print(
                    f"  [{already_done + completed}/{len(settlements)}] "
                    f"{settlement['name']} ({settlement['canton']}): "
                    f"{reachable}/{len(city_ids)} cities | "
                    f"{rate:.1f}/min | ETA {eta_h:.1f}h",
                    flush=True,
                )

            save_checkpoint(results)
            print(f"  Checkpoint saved "
                  f"({already_done + completed}/{len(settlements)})")

    # Save final output
    print(f"\nSaving breakdown for {len(results)} settlements...")
    with open(OUTPUT_PATH, "w") as f:
        json.dump(results, f)
    print(f"  Saved to {OUTPUT_PATH}")

    # Stats
    total_pairs = len(results) * len(city_ids)
    reachable = sum(
        1 for bd in results.values()
        for v in bd.values() if v is not None
    )
    print(f"\nResults:")
    print(f"  Settlements: {len(results)}")
    print(f"  Reachable: {reachable}/{total_pairs} "
          f"({reachable / total_pairs * 100:.1f}%)")

    # Avg breakdown stats
    all_walk, all_wait, all_ivt, all_transfers = [], [], [], []
    for bd in results.values():
        for v in bd.values():
            if v is not None:
                all_walk.append(v["walk_s"])
                all_wait.append(v["wait_s"])
                all_ivt.append(v["ivt_s"])
                all_transfers.append(v["transfers"])

    if all_walk:
        print(f"\n  Average breakdown across all reachable pairs:")
        print(f"    Walk:      {sum(all_walk)/len(all_walk)/60:.1f} min")
        print(f"    Wait:      {sum(all_wait)/len(all_wait)/60:.1f} min")
        print(f"    In-vehicle: {sum(all_ivt)/len(all_ivt)/60:.1f} min")
        print(f"    Transfers:  {sum(all_transfers)/len(all_transfers):.1f}")

    # Clean up checkpoint
    if CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        print("\nCheckpoint cleaned up (run complete)")


if __name__ == "__main__":
    main()
