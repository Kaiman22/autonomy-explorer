#!/usr/bin/env python3
"""
Step 2g: Fix car-free settlement car times.

Identifies settlements that are genuinely car-free (accessible only by
cable car, funicular, or cog railway — no road access) and nulls out
their driving times. These inflated OSRM car times distort the
car–PT delta views.

Also identifies settlements whose centroids happen to fall in
pedestrian zones of otherwise car-accessible municipalities. For these,
the OSRM-computed times are reasonable (OSRM snaps to nearest road),
so we keep them.

Known car-free Swiss settlements:
  - Zermatt, Saas-Fee (VS): access via Visp, car-free by law
  - Wengen, Mürren (BE): access via Lauterbrunnen, Jungfrau region
  - Braunwald (GL): access via funicular from Linthal
  - Stoos (SZ): access via funicular from Schwyz valley
  - Rigi Kaltbad (LU): access via cog railway from Vitznau/Goldau
  - Bettmeralp, Riederalp (VS): access via cable car from Betten/Mörel
  - Praborgne (VS): hamlet near Zermatt, same access restrictions

Usage:
  python 02g_fix_carfree.py          # apply fixes
  python 02g_fix_carfree.py --dry-run # show what would change
"""
import argparse
import json
from pathlib import Path

from config import PROCESSED_DIR

# Car-free settlements: name → reason
# These are settlements where OSRM returned driving times via absurd
# mountain detours because there is genuinely no road access.
CAR_FREE_SETTLEMENTS = {
    "Zermatt": "car-free by law, access via Visp railway",
    "Praborgne": "hamlet in Zermatt commune, same restrictions",
    "Saas-Fee": "car-free by law, access via PostBus from Saas-Grund",
    "Wengen": "car-free, access via WAB train from Lauterbrunnen",
    "Mürren": "car-free, access via cable car/funicular from Lauterbrunnen",
    "Braunwald": "car-free, access via funicular from Linthal",
    "Stoos SZ": "car-free, access via funicular from Schwyz",
    "Rigi Kaltbad": "car-free, access via cog railway from Vitznau/Goldau",
    "Bettmeralp": "car-free, access via cable car from Betten",
    "Riederalp": "car-free, access via cable car from Mörel",
}


def main():
    parser = argparse.ArgumentParser(description="Fix car-free settlement driving times")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    args = parser.parse_args()

    # Load data
    with open(PROCESSED_DIR / "settlement_points.json") as f:
        settlements = json.load(f)
    with open(PROCESSED_DIR / "settlement_travel_times_driving.json") as f:
        driving = json.load(f)

    name_to_uuids = {}
    for s in settlements:
        name_to_uuids.setdefault(s["name"], []).append(s["uuid"])

    cities = ["zurich", "bern", "basel", "luzern", "geneve", "lausanne",
              "stgallen", "lugano", "winterthur", "biel"]

    changes = []
    already_null = []

    for name, reason in CAR_FREE_SETTLEMENTS.items():
        uuids = name_to_uuids.get(name, [])
        if not uuids:
            print(f"  WARNING: Settlement '{name}' not found in settlement_points.json")
            continue

        for uuid in uuids:
            if uuid not in driving:
                print(f"  WARNING: UUID {uuid} ({name}) not in driving data")
                continue

            current = driving[uuid]
            non_null = {c: v for c, v in current.items() if v is not None}

            if non_null:
                changes.append((uuid, name, reason, non_null))
                print(f"  WILL NULL: {name} — {len(non_null)} non-null car times")
                for c, v in sorted(non_null.items()):
                    print(f"    {c}: {v/60:.0f} min → null")
            else:
                already_null.append((uuid, name))
                print(f"  ALREADY NULL: {name}")

    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  Settlements to fix: {len(changes)}")
    print(f"  Already null: {len(already_null)}")
    print(f"  Total car-free: {len(changes) + len(already_null)}")

    if args.dry_run:
        print("\n  DRY RUN — no changes applied")
        return

    if not changes:
        print("\n  No changes needed")
        return

    # Apply changes
    for uuid, name, reason, _ in changes:
        for city in cities:
            driving[uuid][city] = None

    # Save
    output_path = PROCESSED_DIR / "settlement_travel_times_driving.json"
    with open(output_path, "w") as f:
        json.dump(driving, f)
    print(f"\n  Saved updated driving times to {output_path}")

    # Also update travel_times.json (municipality level)
    with open(PROCESSED_DIR / "settlement_municipality_map.json") as f:
        mapping = json.load(f)

    tt_path = PROCESSED_DIR / "travel_times.json"
    if tt_path.exists():
        with open(tt_path) as f:
            travel_times = json.load(f)

        # Re-aggregate driving times to municipality level
        muni_to_settlements = mapping["municipality_to_settlements"]
        for muni_id, settlement_uuids in muni_to_settlements.items():
            for city in cities:
                times = []
                for suuid in settlement_uuids:
                    t = driving.get(suuid, {}).get(city)
                    if t is not None:
                        times.append(t)
                if muni_id not in travel_times.get("driving", {}):
                    continue
                travel_times["driving"][muni_id][city] = min(times) if times else None

        with open(tt_path, "w") as f:
            json.dump(travel_times, f)
        print(f"  Updated municipality-level driving times in {tt_path}")


if __name__ == "__main__":
    main()
