# Architecture — Sleeper Towns

> Self-contained project documentation. Everything needed to continue development
> without prior context. Companion docs: [README.md](README.md) (thesis & pitch),
> [RESEARCH.md](RESEARCH.md) (data-source research + metric audit).

## What this is

A static web map ranking Swiss settlements by how much property value autonomous
vehicles (AVs) would unlock there. Python pipeline produces one GeoJSON; a React
frontend renders it and recomputes all metrics client-side. No backend, no runtime
API dependencies (except two optional frontend calls for user-added locations).

- **Live:** https://kaiman22.github.io/sleeper-towns/
- **Repo:** https://github.com/Kaiman22/sleeper-towns (renamed from
  `autonomy-explorer` 2026-07; old Pages URL is dead, git remotes redirect)

```
data/scripts/*.py  →  data/processed/*.json  →  05_compute_scores.py
                                                      ↓
frontend/public/data/municipalities_scored.geojson  (~3,966 point features)
                                                      ↓
React app (recomputes metrics client-side from raw times in the GeoJSON)
```

## Repo layout

```
data/scripts/          Python pipeline (numbered = execution order)
data/processed/        Pipeline outputs & checkpoints (committed)
frontend/              React + Vite + MapLibre GL app
  src/App.jsx          State, metric computation (recomputeScores), custom locations
  src/layers/MapView.jsx    Map rendering, color scales, legend, highlight layers
  src/panels/SidePanel.jsx  All UI controls, metric metadata (METRICS), rankings, detail view
  public/data/municipalities_scored.geojson   THE data file
  .env                 VITE_GEOAPIFY_API_KEY (gitignored — needed for custom locations)
.github/workflows/deploy.yml   Build + deploy to GitHub Pages
```

## Data pipeline

Run scripts from `data/scripts/` (shared constants in `config.py`: the 10 reference
cities, paths, comfort factors). Only **05** needs re-running when upstream data
changes; it's fast and idempotent.

| Step | Script | Output | Notes |
|---|---|---|---|
| 1 | `01_fetch_municipalities.py`, `01c_fetch_settlement_points.py` | `municipalities.json`, `settlement_points.json` | 2,128 municipalities, 3,966 settlement points (swissNAMES3D). `01b` (PLZ points) is legacy. |
| 2 | `02h_fetch_driving_times_google.py` | `settlement_travel_times_driving.json` | Car times to 10 cities. Ran on a **dedicated machine**, results synced via git. `02`/`02b`/`02c` (TravelTime API) and `02d` (Geoapify) are legacy. |
| 2 | `02e_fetch_pt_times_sbb.py` | `settlement_travel_times_pt.json` | PT times via transport.opendata.ch. **Rate limit ~1,000 req/day per IP** — full runs take days on the dedicated machine. |
| 2 | `02f_fix_overnight_pt.py` | (in place) | Fixed 7,591 pairs contaminated by overnight connections (arrival-based queries were the bug). API refetch hit rate limits → heuristic correction (p40 ratio + IDW), mean abs error ~13%. |
| 2 | `02g_fetch_pt_breakdown.py` | `settlement_pt_breakdown.json` | Walk/wait/in-vehicle split per settlement×city. Departure-based queries (07:00 Monday), `isArrivalTime=0` — **never use arrival-based queries** (overnight bug). Coverage 3,859/3,966. |
| 3 | `03b_fetch_prices_neho.py`, `03c_fetch_prices_homegate.py`, `04_merge_prices.py` | `prices.json` | Neho hedonic (primary, 1,368) + Homegate listing medians (fallback, 237) + **IDW interpolation** for 467 gaps (K=5 neighbors, marked `type: "interpolated"`). Playwright + stealth needed (Cloudflare). |
| 4 | `04_fetch_taxes.py` | `taxes.json` | ESTV income-tax multipliers, 2,122 municipalities. |
| 5 | `05_compute_scores.py` | `frontend/public/data/municipalities_scored.geojson` | Merges everything; embeds raw times + PT breakdown + price/tax + precomputed default metrics per feature. |

**The frontend never trusts precomputed metrics for display** — it recomputes
everything in `recomputeScores()` (App.jsx) from the raw `drive_times` /
`pt_times` / `pt_breakdown` properties, so user settings (enabled cities, comfort
sliders, custom locations) work without a server. The backend's precomputed values
in the GeoJSON are only a convenience/sanity baseline and should be kept
semantically in sync with App.jsx.

## Metrics (semantics matter — read this before touching them)

Internal property keys are stable (URL compat); display labels live in
`SidePanel.jsx` `METRICS` and `MapView.jsx` `METRIC_LABELS`.

| Key | Label | Definition |
|---|---|---|
| `avg_car_access` / `avg_pt_access` | Average Car/PT Access | Mean minutes to enabled refs. |
| `optimum_access` | Optimum Accessibility | Mean of per-ref `min(car, pt)`. |
| `car_pt_delta_min` | Car − PT Delta | `avg(car) − avg(pt)`; mixed-sign refs cancel (known, accepted). |
| `av_upside` | AV Upside | Per-ref `max(0, min(pt_comfort, car) − car×avFactor)`, averaged over refs. Raw and unconditioned: "minutes AV beats today's best mode." **Correlates r≈0.89 with car distance — do not use as an investment signal by itself.** |
| `av_value_unlock` | Wake-Up Value (CHF) | `max over refs (upside_ref × viability(car×avFactor))`, capitalized: `min × 2 trips × 220 days × (VTT/60) / capRate`. One commuter, one destination. |
| `av_deal_score` | Sleeper Score (m²) | `av_value_unlock / chf_per_m2` — m² of property the AV unlock pays for. The flagship ranking. |

Key modeling rules (rationale in RESEARCH.md "Metric Review"):

- **Viability weighting**: time savings only capitalize while the destination stays
  a plausible daily commute. `viability(t_av)` = 1 up to `avTolerance` (slider,
  default 45 min), linear fade to 0 over the next 45 min. Without this, the deal
  score ranks 4-hour-remote valleys first (Poschiavo problem).
- **PT comfort**: `ptFactor` applies **only to in-vehicle time** when breakdown
  data exists (`ivt×ptFactor + walk + wait`), else to the whole PT time.
- **Plausibility filter**: per ref, drop a mode when PT/car ratio > 3.5 or < 1/3.5
  (residual bad routings).
- Model params (`DEFAULT_MODEL_PARAMS` in App.jsx): `avFactor` 0.65, `ptFactor`
  0.90, `vtt` 30 CHF/h, `capRate` 0.04, `avTolerance` 45 min — all user-adjustable
  in Advanced Settings. **When adding a param: also initialize it in the
  `modelParams` useState** (past bug: vtt/capRate sliders rendered NaN because
  state only spread two of the defaults).

## Custom reference locations (frontend-only feature)

Users can add any address as a reference. On add (`addCustomLocation` in App.jsx):

- **Car times**: Geoapify Route Matrix (batches of 100, needs
  `VITE_GEOAPIFY_API_KEY` in `frontend/.env`); haversine @50 km/h fallback.
- **PT times**: **hub routing** — 10 parallel SBB API calls custom→hub, then per
  settlement `min over hubs (settlement→hub + custom→hub + Taktfahrplan transfer)`;
  transfer 7.5/10/12.5 min by hub size; `min(hubRouted, carRatio)` as safety bound;
  car-ratio fallback for <15 km or when <3 hubs respond (`HUB_CITIES`,
  `fetchSbbHubTimes`, `computeHubRoutedPtTime` in App.jsx).

## Frontend rendering notes

- Color scales are **quantile-based** (p10–p90 stops from `colorBounds` in App.jsx),
  resolved in `getResolvedMetricConfig()` (MapView.jsx). Each metric has a distinct
  palette (deal score: teal→magenta; value: amber; upside: green).
- Map layers: `municipalities-heat` (blurred surface) / `municipalities-circles`
  (default) / `municipalities-selected` (white ring) / `municipalities-highlight`
  (yellow ring — driven by hovering the Top/Bottom-10 lists).
- URL state: `colorBy`, `cities`, `avFactor`, `ptFactor` (see `getUrlState`).
- Interpolated prices show an "est." tag in the detail panel via `price_source`.

## Build & deploy

```bash
cd frontend && npm run build        # after any data or code change
git add ... && git commit && git push
gh workflow run deploy.yml --ref main    # REQUIRED — see below
```

⚠️ **The `on: push` trigger of `.github/workflows/deploy.yml` is dead** (GitHub
auto-disabled it during repo inactivity; observed since 2026-03). Every deploy since
has been manual `workflow_dispatch`. Either trigger manually after each push or
re-enable by editing the workflow file. Occasionally the Pages deploy step fails
transiently ("Deployment failed, try again later") — just re-run.

The GeoJSON ships inside the Pages artifact (it lives in `frontend/public/`), so
**data changes also require build + deploy**, not just a push.

## Known data caveats

- 7,591 PT pairs carry heuristic overnight corrections (~13% mean error).
- 467 municipality prices are IDW-interpolated; Homegate medians accept ≥2 listings
  (noisy in small towns — top Sleeper Score entries are leads, not answers).
- 107 settlements lack travel times; 56 municipalities have no settlement points
  (absent from the map by design).
- PT breakdown covers 3,859/3,966; the rest fall back to whole-time ptFactor.
- Model prices only commute time. Zoning/building-land supply (the real binding
  constraint on Swiss price response) is not modeled — candidate future data:
  ARE building-zone statistics.

## External services & constraints

| Service | Used for | Constraint |
|---|---|---|
| transport.opendata.ch (SBB) | PT times, breakdown, hub routing | ~1,000 req/day/IP. Long scrapes run on a dedicated machine, synced via git. |
| Geoapify | Custom-location car matrix | Free-tier key in `frontend/.env` (gitignored). 429 → haversine fallback. |
| Neho / Homegate | Prices | Cloudflare; Playwright + stealth; slug matching to BFS ids is fragile (125 district-level + 114 town-level Neho entries remain unmatched as `_slug_*` in `prices_neho.json`). |
| ESTV | Taxes | Stable JSON endpoint, no auth. |
