#!/bin/bash
# Run the full data pipeline for Autonomy Explorer
# Usage: ./run_pipeline.sh [--demo] [--real]
#
# --demo: Generate synthetic travel times for frontend development
# --real: Use TravelTime API for real travel times (requires env vars)
#         TRAVELTIME_APP_ID and TRAVELTIME_API_KEY must be set

set -e
cd "$(dirname "$0")"

echo "=== Autonomy Explorer Data Pipeline ==="
echo ""

# Step 1: Fetch municipalities
echo "--- Step 1: Fetch municipality centroids ---"
python3 01_fetch_municipalities.py
echo ""

# Step 2: Travel times (optional - needs API keys or OSRM)
if [[ "$1" == "--real" ]]; then
    echo "--- Step 2: Fetch real travel times ---"
    if [[ -z "$TRAVELTIME_APP_ID" || -z "$TRAVELTIME_API_KEY" ]]; then
        echo "ERROR: Set TRAVELTIME_APP_ID and TRAVELTIME_API_KEY env vars"
        echo "  export TRAVELTIME_APP_ID=your_app_id"
        echo "  export TRAVELTIME_API_KEY=your_api_key"
        exit 1
    fi
    python3 02_fetch_travel_times.py --mode both
    echo ""
fi

# Step 3: Prices (slow - ~20 min for all municipalities)
# Uncomment when ready to scrape:
# echo "--- Step 3: Fetch real estate prices ---"
# python3 03_fetch_prices.py
# echo ""

# Step 4: Tax rates
echo "--- Step 4: Fetch tax rates ---"
python3 04_fetch_taxes.py
echo ""

# Step 5/6: Compute scores
if [[ "$1" == "--real" ]]; then
    echo "--- Step 5: Compute real scores ---"
    python3 05_compute_scores.py
else
    echo "--- Step 6: Generate demo scores ---"
    python3 06_generate_demo.py
fi
echo ""

echo "=== Pipeline complete ==="
echo "Frontend data at: frontend/public/data/municipalities_scored.geojson"
echo "Start dev server: cd frontend && npm run dev"
