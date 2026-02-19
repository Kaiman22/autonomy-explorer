# Autonomy Real Estate Explorer – Research Findings

> Phase 1 research completed 2026-02-18. All data sources validated.

---

## R1: Swiss Public Transport Data

### Recommended: TravelTime API (primary) + OJP API (fallback)

| Source | Type | Rate Limits (Free) | Auth | Door-to-Door | Batch 2000 OD |
|---|---|---|---|---|---|
| **TravelTime API** | REST JSON | 60/min (trial), 5/min (post) | API key | Yes | Best – 2 requests |
| **OJP API** | XML | 50/min, 20k/day | API key (free reg) | Yes | High – ~40 min |
| **OTP (local)** | REST JSON | None (local) | No | Yes | Best – no limits |
| **search.ch** | REST JSON | 1,000 routes/day | No | Yes | Medium – 2-3 days |
| **transport.opendata.ch** | REST JSON | Same as search.ch | No | Partial | Medium |
| **SBB Journey-Service** | REST | Plan-dependent, approval needed | OAuth 2.0 | Likely | Low – heavy onboarding |

**Decision**: Use TravelTime API `public_transport` mode for PT travel times. Switzerland has full coverage (95%+ stops). Free trial: 60 hits/min. Post-trial: 5 hits/min – still sufficient for our 16-hit workload.

**GTFS Feed**: Unified country-wide feed at `data.opentransportdata.swiss/dataset/timetable-2026-gtfs2020`. Can be used with local OTP instance as backup.

---

## R2: Car Routing

### Recommended: OSRM local Docker (primary) + TravelTime `driving` (complement)

| Source | Type | Rate Limits | Traffic Model | Matrix Support | Setup |
|---|---|---|---|---|---|
| **OSRM (local)** | Docker REST | None | No (free-flow) | 2000x4 in <1s | ~30 min |
| **TravelTime** | REST JSON | 60/min (trial) | Yes (balanced/optimistic/pessimistic) | 2000/search | API key |
| **OpenRouteService** | REST | 500 req/day | Yes | 3,500 elements/req | API key |
| **GraphHopper** | REST | 5 locations/req (free) | Limited | Impractical free | API key |

### OSRM Setup (CH pipeline recommended for matrix)

```bash
# Download Switzerland extract (~496 MB)
wget https://download.geofabrik.de/europe/switzerland-latest.osm.pbf

# Extract with car profile
docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend \
  osrm-extract -p /opt/car.lua /data/switzerland-latest.osm.pbf

# Contract (CH pipeline – best for matrix queries)
docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend \
  osrm-contract /data/switzerland-latest.osrm

# Start server
docker run -t -i -p 5000:5000 -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend \
  osrm-routed --algorithm ch --max-table-size 10000 /data/switzerland-latest.osrm
```

**Performance**: 10k x 10k matrix (~100M routes) in ~8 seconds. Our 2000x4 matrix: sub-second.

**Decision**: Use OSRM local for free-flow driving times (unlimited, instant). Use TravelTime `driving` mode for traffic-aware times as complement (4 requests total).

---

## R3: Real Estate Prices

### Recommended: Homegate.ch scraping (primary) + Raiffeisen Gemeindeinfo (fallback)

| Source | Granularity | Data Type | Format | Free | Coverage |
|---|---|---|---|---|---|
| **Homegate.ch** | Municipality | CHF/m² median | `window.__INITIAL_STATE__` JSON | Yes (scrape) | 2,100+ |
| **ImmoScout24.ch** | Municipality | CHF/m² median | `window.__INITIAL_STATE__` JSON | Yes (scrape) | 2,800+ |
| **Raiffeisen Gemeindeinfo** | Municipality | Hedonic transaction CHF | HTML (scrape) | Yes (scrape) | All municipalities |
| **FPRE API** | Municipality | Absolute prices, forecasts | REST/SOAP API | No (paid) | All |
| **BFS IMPI** | 5 municipality types | Price index (base 100) | PDF/API | Yes | National |
| **SNB data portal** | 8 market regions | Price indices | CSV | Yes | National |
| **Wüest Partner** | 8 regions (free) | Asking price indices | CSV | Partially | National |

**Scraping approach**: Both Homegate and ImmoScout24 embed structured JSON in `window.__INITIAL_STATE__` on pages like `homegate.ch/en/property-prices-m2/city-{name}`. Parse this for median CHF/m² for Eigentumswohnungen.

**URL patterns**:
- Homegate: `https://www.homegate.ch/en/property-prices-m2/city-{municipality-slug}`
- ImmoScout24: `https://www.immoscout24.ch/en/property-prices-m2/city-{municipality-slug}`
- Raiffeisen: `https://www.raiffeisen.ch/rch/de/privatkunden/hypotheken/gemeindeinfo.{municipality-slug}.html`

**Decision**: Scrape Homegate.ch or ImmoScout24.ch for CHF/m² per municipality. Use Raiffeisen as backup (hedonic model, total CHF for standard property – divide by 100m² for STWE).

---

## R4: Swiss Tax Rates (Steuerfüsse)

### Recommended: ESTV swisstaxcalculator export

**Source**: `https://swisstaxcalculator.estv.admin.ch/#/taxdata/tax-rates`

**API Endpoints discovered**:
1. **JSON data**: `POST https://swisstaxcalculator.estv.admin.ch/delegate/ost-integration/v1/lg-proxy/operation/c3b67379_ESTV/API_exportManySimpleRates`
   - Body: `{"SimKey":null,"TaxYear":2025,"TaxGroupID":99}`
   - Returns JSON with all municipality tax multipliers
2. **Excel export**: `POST .../export/income-tax-rates/EN`
   - Downloads `estv_income_rates.xlsx` (~170 KB) with all municipalities

**Additional sources**:
- Canton Zurich: CSV on opendata.swiss
- Canton Bern: Geospatial format on opendata.swiss
- opendata.swiss search: Various cantonal datasets

**Decision**: Use ESTV API endpoint for comprehensive all-Switzerland Steuerfüsse data. The JSON endpoint returns structured data for all municipalities in a single request. Alternatively, download the Excel export and parse with Python (openpyxl/pandas).

---

## R5: Swiss Geodata

### Municipality Boundaries

| Source | Format | CRS | License | Best For |
|---|---|---|---|---|
| **BFS Generalisierte Gemeindegrenzen** | Shapefile, FileGDB | LV95 (EPSG:2056) | Free + attribution | Thematic maps, web (smaller) |
| **swisstopo swissBOUNDARIES3D** | Shapefile, GeoPackage, GPKG | LV95 (EPSG:2056) | Free OGD (since 2021) | Precise boundaries |
| **cividi/swissboundaries-municipalities-data** | GeoJSON, GeoPackage | WGS84 | Free | Ready-to-use for web |

**Download**: `https://www.bfs.admin.ch/asset/de/ag-b-00.03-875-gg25` (BFS 2025 edition)

**Conversion**: `ogr2ogr -f GeoJSON -t_srs EPSG:4326 output.geojson input.shp`

### Municipality Centroids

**Best option**: Opendatasoft georef-switzerland-gemeinde
- URL: `https://public.opendatasoft.com/explore/dataset/georef-switzerland-gemeinde/`
- Includes pre-computed `geo_point_2d` (centroid) for every Gemeinde
- Export as CSV, JSON, or GeoJSON

**Alternative**: Derive from polygons with GeoPandas: `gdf['centroid'] = gdf.geometry.centroid`

### 1km Raster Grid (H3)

- **H3 Resolution 7**: ~1.4 km edge, ~5.16 km² per hexagon, ~8,000 cells for Switzerland
- **H3 Resolution 8**: ~530m edge, ~0.74 km² per hexagon, ~56,000 cells
- Library: `pip install h3`

### Basemap Tiles

| Style | URL | API Key | Notes |
|---|---|---|---|
| **swisstopo Light** | `https://vectortiles.geo.admin.ch/styles/ch.swisstopo.lightbasemap.vt/style.json` | No | Swiss-specific, MapLibre-ready |
| **CARTO Dark Matter** | `https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json` | No | Global OSM, dark theme |
| **CARTO Positron** | `https://basemaps.cartocdn.com/gl/positron-gl-style/style.json` | No | Global OSM, light theme |
| **MapTiler LBM Dark** | `https://api.maptiler.com/maps/ch-swisstopo-lbm-dark/style.json?key={KEY}` | Yes (free tier) | Swiss-specific dark |

**Decision**: Use CARTO Dark Matter as default basemap (free, no key, dark theme). Offer swisstopo Light as alternative. Both are MapLibre GL JS compatible.

### Elevation

- **DHM25/200** (200m grid): Small file, sufficient for municipal average elevation via zonal statistics
- Download: `https://opendata.swiss/en/dataset/das-digitale-hohenmodell-der-schweiz-mit-einer-maschenweite-von-200-m`

### Nature / Protected Areas

- Pro Natura reserves (~770): Shapefile on opendata.swiss
- Swiss National Park + Parks of National Importance: Shapefile via BAFU
- Lakes: Included in swissBOUNDARIES3D or via `github.com/ZHB/switzerland-geojson`

---

## R6: TravelTime API

### Key Findings

| Parameter | Value |
|---|---|
| **Best endpoint** | `/v4/time-filter` (standard) |
| **Max destinations/search** | 2,000 |
| **Max searches/request** | 10 |
| **Max travel time** | 14,400s (4 hours) |
| **Trial rate limit** | 60 hits/min (14 days) |
| **Post-trial rate limit** | 5 hits/min |
| **Swiss PT coverage** | Full (95%+ stops) |
| **Driving support** | Yes (standard endpoint; NOT on Fast endpoint) |
| **PT support** | Yes (both standard and Fast) |
| **Traffic model** | balanced / optimistic / pessimistic |
| **Route details** | line_name, departure_station, arrival_station, num_stops, travel_time per leg |

### Batch Strategy

**For our 2,150 municipalities × 4 cities × 2 modes = 17,200 pairs:**

1. Per mode: 4 cities × 2 batches (2000 + 150) = 8 searches → fits in 1 API request
2. Total: 2 API requests (1 driving, 1 PT) = 16 hits
3. At 60 hits/min (trial): **under 1 minute**
4. At 5 hits/min (post-trial): **~4 minutes**

### Request Structure

```python
# Each request: 1 per transport mode
{
  "locations": [
    # 4 city locations + 2,150 municipality centroids
  ],
  "arrival_searches": [
    # 8 searches: 4 cities × 2 batches
    {
      "arrival_location_id": "zurich_hb",
      "departure_location_ids": ["muni_0001", ..., "muni_2000"],
      "transportation": {"type": "driving"},  # or "public_transport"
      "arrival_time": "2026-03-02T08:00:00+01:00",
      "travel_time": 14400,
      "properties": ["travel_time"]
    },
    # ... 7 more searches
  ]
}
```

### Fast Endpoint Limitations

- Supports `public_transport` but NOT plain `driving` (only `driving+ferry`)
- No custom departure time (only `weekday_morning`)
- Up to 100,000 locations per search but fewer configurable parameters
- **Not recommended for our use case** – use standard endpoint instead

---

## Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Car travel times** | OSRM local (primary) + TravelTime driving (traffic-aware) | Free, instant, unlimited |
| **PT travel times** | TravelTime `public_transport` | Full Swiss coverage, 2 requests total |
| **Real estate prices** | Scrape Homegate.ch `window.__INITIAL_STATE__` | Structured JSON, 2100+ municipalities |
| **Tax rates** | ESTV swisstaxcalculator API/export | All municipalities, official data |
| **Municipality data** | BFS Generalisierte Gemeindegrenzen + Opendatasoft centroids | Simplified for web, pre-computed centroids |
| **Grid** | H3 Resolution 7 (~8k cells) | Modern, standardized, WGS84-native |
| **Basemap** | CARTO Dark Matter | Free, no key, dark theme |
| **Frontend** | React + Vite + MapLibre GL JS | Modern, performant, vector tiles |
| **Data format** | Pre-computed static GeoJSON/JSON | No runtime API dependency |

---

## Data Pipeline Steps

1. **Generate grid**: Download BFS boundaries → derive centroids (or download from Opendatasoft) → optionally generate H3 grid
2. **Fetch car travel times**: OSRM Table API (local Docker) → 2000×4 matrix in <1s
3. **Fetch PT travel times**: TravelTime `/v4/time-filter` → 2 API requests
4. **Fetch real estate prices**: Scrape Homegate.ch → CHF/m² per municipality
5. **Fetch tax rates**: ESTV API → Steuerfüsse per municipality
6. **Compute scores**: Apply comfort-weighted formula → autonomy_upside_score
7. **Export**: Static GeoJSON for frontend consumption
