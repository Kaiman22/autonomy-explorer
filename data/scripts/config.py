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

# Comfort factors
COMFORT = {
    "av_factor": 0.70,          # AV makes drive time 70% as burdensome
    "oev_sitting_factor": 0.70, # Sitting on train is 70% as burdensome
    "wait_penalty_factor": 2.0, # Waiting feels 2x as long
    "transfer_penalty_min": 10, # Each transfer adds 10 min perceived
    "walk_factor": 1.75,        # Walking feels 1.75x as long
}

# Arrival time for commuter scenario
ARRIVAL_TIME = "2026-03-02T08:00:00+01:00"  # Monday 8am CET
MAX_TRAVEL_TIME = 14400  # 4 hours in seconds
