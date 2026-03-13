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

# OSRM local instance (legacy — replaced by Geoapify for production)
OSRM_BASE_URL = os.environ.get("OSRM_URL", "http://localhost:5000")

# Geoapify Routing API (with traffic approximation)
# Sign up free at https://myprojects.geoapify.com/ — no credit card required
# Free tier: 3,000 credits/day. Route Matrix: Max(S,T)*Min(S,T,10) credits per call.
GEOAPIFY_API_KEY = os.environ.get("GEOAPIFY_API_KEY", "")
GEOAPIFY_MATRIX_URL = "https://api.geoapify.com/v1/routematrix"
GEOAPIFY_ROUTING_URL = "https://api.geoapify.com/v1/routing"
GEOAPIFY_BATCH_SOURCES = 100  # max sources per matrix call (Geoapify limit)
GEOAPIFY_DAILY_CREDITS = 3000  # free tier daily limit

# Google Maps Routes API (with real-time / historical traffic)
# Sign up at https://console.cloud.google.com/ → Enable "Routes API"
# Create an API key → export GOOGLE_MAPS_API_KEY=your_key
# Pricing (post-March 2025): Pro tier (TRAFFIC_AWARE) = $10/1,000 requests,
#   5,000 free/month.  Essentials (no traffic) = $5/1,000, 10,000 free/month.
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

# ESTV Tax API
ESTV_TAX_URL = "https://swisstaxcalculator.estv.admin.ch/delegate/ost-integration/v1/lg-proxy/operation/c3b67379_ESTV/API_exportManySimpleRates"
ESTV_TAX_YEAR = 2025

# Comfort factors — must match DEFAULT_MODEL_PARAMS in frontend/src/App.jsx
COMFORT = {
    "av_factor": 0.65,          # AV makes drive time 65% as burdensome
    "oev_sitting_factor": 0.90, # PT comfort factor (0.9 = PT slightly more comfortable than driving)
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
