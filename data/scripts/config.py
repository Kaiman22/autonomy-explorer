"""
Shared configuration for the Autonomy Explorer data pipeline.
"""
import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
FRONTEND_DATA_DIR = BASE_DIR / "frontend" / "public" / "data"

# Ensure dirs exist
RAW_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
FRONTEND_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Target cities (arrival points)
CITIES = {
    "zurich": {"name": "Zürich HB", "lat": 47.3769, "lon": 8.5417},
    "bern": {"name": "Bern HB", "lat": 46.9490, "lon": 7.4395},
    "basel": {"name": "Basel SBB", "lat": 47.5476, "lon": 7.5891},
    "luzern": {"name": "Luzern Bf", "lat": 47.0502, "lon": 8.3093},
    "geneve": {"name": "Genève Cornavin", "lat": 46.2100, "lon": 6.1426},
    "lausanne": {"name": "Lausanne Gare", "lat": 46.5168, "lon": 6.6294},
    "stgallen": {"name": "St. Gallen HB", "lat": 47.4233, "lon": 9.3696},
    "lugano": {"name": "Lugano Bf", "lat": 46.0054, "lon": 8.9468},
    "winterthur": {"name": "Winterthur HB", "lat": 47.5001, "lon": 8.7237},
    "biel": {"name": "Biel/Bienne", "lat": 47.1326, "lon": 7.2474},
}

# TravelTime API
TRAVELTIME_APP_ID = os.environ.get("TRAVELTIME_APP_ID", "")
TRAVELTIME_API_KEY = os.environ.get("TRAVELTIME_API_KEY", "")
TRAVELTIME_BASE_URL = "https://api.traveltimeapp.com/v4"

# OSRM local instance
OSRM_BASE_URL = os.environ.get("OSRM_URL", "http://localhost:5000")

# ESTV Tax API
ESTV_TAX_URL = "https://swisstaxcalculator.estv.admin.ch/delegate/ost-integration/v1/lg-proxy/operation/c3b67379_ESTV/API_exportManySimpleRates"
ESTV_TAX_YEAR = 2025

# Scoring weights (defaults, adjustable in frontend)
# Two-component model:
#   accessibility_gain: how much AV improves this place's connectivity
#   inherent_attractiveness: how desirable this place is independent of transport
#     (= price normalized by current status-quo accessibility)
SCORING_WEIGHTS = {
    "accessibility_gain": 0.50,
    "inherent_attractiveness": 0.50,
}

# Comfort factors — must match DEFAULT_MODEL_PARAMS in frontend/src/App.jsx
COMFORT = {
    "av_factor": 0.70,          # AV makes drive time 70% as burdensome
    "oev_sitting_factor": 0.70, # Sitting on train is 70% as burdensome
}

# Walking deduction from PT times (seconds), by population category.
# TravelTime API includes walking to/from PT stops, but the origin walking
# segment is noise (depends on centroid placement) and disproportionately
# inflates PT times because walking is slow. Must match frontend/src/App.jsx.
# Urban areas have nearby PT stops (short walk), rural areas don't.
PT_WALK_DEDUCTION_S = {
    "> 100'000": 180,           # 3 min — dense urban, PT stop everywhere
    "50'000 bis 100'000": 240,  # 4 min
    "10'000 bis 49'999": 360,   # 6 min — small city/town
    "2'000 bis 9'999": 480,     # 8 min — large village
    "1'000 bis 1'999": 600,     # 10 min — village
    "100 bis 999": 720,         # 12 min — small village, bus stop may be far
    "default": 600,             # 10 min fallback
}

# Arrival times for commuter scenario — multiple departures for robustness.
# TravelTime PT routes vary significantly by time of day (peak vs off-peak).
# We average across these to get a more representative commute time.
# All are weekday arrivals to capture traffic/PT schedule variation.
ARRIVAL_TIMES = [
    "2026-03-02T07:30:00+01:00",  # Monday 7:30am
    "2026-03-02T08:30:00+01:00",  # Monday 8:30am
    "2026-03-03T08:00:00+01:00",  # Tuesday 8:00am
    "2026-03-04T09:00:00+01:00",  # Wednesday 9:00am (flex start)
    "2026-03-05T08:00:00+01:00",  # Thursday 8:00am
]
ARRIVAL_TIME = ARRIVAL_TIMES[0]  # backward compat for scripts using single time
MAX_TRAVEL_TIME = 14400  # 4 hours in seconds
