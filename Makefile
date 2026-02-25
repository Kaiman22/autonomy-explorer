# Autonomy Explorer — Pipeline Orchestration
# Usage:
#   make pipeline        — Full data pipeline (municipalities → scores → frontend build)
#   make scores          — Recompute scores from existing data
#   make travel-times    — Fetch travel times only (requires API keys)
#   make prices          — Fetch prices only (requires browser)
#   make frontend        — Build and deploy frontend
#   make validate        — Run data quality checks
#
# Environment variables:
#   TRAVELTIME_APP_ID    — TravelTime API application ID
#   TRAVELTIME_API_KEY   — TravelTime API key
#   OSRM_URL             — OSRM server URL (default: http://localhost:5000)

SCRIPTS_DIR := data/scripts
PROCESSED_DIR := data/processed
FRONTEND_DIR := frontend

.PHONY: pipeline scores travel-times travel-pt travel-driving prices taxes frontend validate clean help

help:
	@echo "Autonomy Explorer Pipeline"
	@echo ""
	@echo "Targets:"
	@echo "  pipeline        Full data pipeline (all steps)"
	@echo "  municipalities  Step 1: Fetch municipality + settlement data"
	@echo "  travel-times    Step 2: Fetch travel times (driving + PT)"
	@echo "  travel-driving  Step 2a: Fetch driving times only (OSRM)"
	@echo "  travel-pt       Step 2b: Fetch PT times only (TravelTime API)"
	@echo "  prices          Step 3: Scrape property prices (Homegate)"
	@echo "  merge-prices    Step 3b: Merge price sources"
	@echo "  taxes           Step 4: Fetch tax data"
	@echo "  scores          Step 5: Compute scores + export GeoJSON"
	@echo "  frontend        Build frontend for deployment"
	@echo "  deploy          Deploy to GitHub Pages"
	@echo "  validate        Run data quality checks"
	@echo ""
	@echo "Required env vars for travel times:"
	@echo "  TRAVELTIME_APP_ID, TRAVELTIME_API_KEY"

# ── Full Pipeline ──

pipeline: municipalities travel-times prices merge-prices taxes scores
	@echo "Pipeline complete."

# ── Step 1: Municipalities + Settlements ──

municipalities:
	@echo "=== Step 1: Fetching municipalities ==="
	cd $(SCRIPTS_DIR) && python 01_fetch_municipalities.py
	@echo "=== Step 1c: Fetching settlement points ==="
	cd $(SCRIPTS_DIR) && python 01c_fetch_settlement_points.py

# ── Step 2: Travel Times ──

travel-times: travel-driving travel-pt

travel-driving:
	@echo "=== Step 2: Fetching driving times (OSRM) ==="
	cd $(SCRIPTS_DIR) && python 02c_fetch_travel_times_settlements.py --mode driving --osrm-public

travel-pt:
	@echo "=== Step 2: Fetching PT times (TravelTime API) ==="
	@test -n "$(TRAVELTIME_APP_ID)" || (echo "ERROR: TRAVELTIME_APP_ID not set" && exit 1)
	@test -n "$(TRAVELTIME_API_KEY)" || (echo "ERROR: TRAVELTIME_API_KEY not set" && exit 1)
	cd $(SCRIPTS_DIR) && python 02c_fetch_travel_times_settlements.py --mode pt

# ── Step 3: Prices ──

prices:
	@echo "=== Step 3: Scraping property prices (Homegate) ==="
	cd $(SCRIPTS_DIR) && python 03c_fetch_prices_homegate.py

merge-prices:
	@echo "=== Step 3b: Merging price sources ==="
	cd $(SCRIPTS_DIR) && python 04_merge_prices.py

# ── Step 4: Taxes ──

taxes:
	@echo "=== Step 4: Fetching tax data ==="
	cd $(SCRIPTS_DIR) && python 04_fetch_taxes.py

# ── Step 5: Scores ──

scores:
	@echo "=== Step 5: Computing scores + exporting GeoJSON ==="
	cd $(SCRIPTS_DIR) && python 05_compute_scores.py

# ── Frontend ──

frontend:
	@echo "=== Building frontend ==="
	cd $(FRONTEND_DIR) && npm run build

deploy: scores frontend
	@echo "=== Deploying to GitHub Pages ==="
	git add -A && git commit -m "Pipeline rebuild" || true
	git push origin main
	gh workflow run deploy.yml --repo Kaiman22/autonomy-explorer --ref main
	@echo "Deployment triggered. Check GitHub Actions for status."

# ── Validation ──

validate:
	@echo "=== Validating data quality ==="
	cd $(SCRIPTS_DIR) && python -c "\
	import json, sys; \
	g = json.load(open('../../$(FRONTEND_DIR)/public/data/municipalities_scored.geojson')); \
	feats = g['features']; \
	n = len(feats); \
	print(f'Features: {n}'); \
	assert n > 3000, f'Too few features: {n}'; \
	with_score = sum(1 for f in feats if f['properties'].get('autonomy_score_rel') is not None); \
	pct = with_score / n * 100; \
	print(f'With score: {with_score} ({pct:.1f}%)'); \
	assert pct > 40, f'Score coverage too low: {pct:.1f}%'; \
	with_price = sum(1 for f in feats if f['properties'].get('chf_per_m2') is not None); \
	ppct = with_price / n * 100; \
	print(f'With price: {with_price} ({ppct:.1f}%)'); \
	assert ppct > 30, f'Price coverage too low: {ppct:.1f}%'; \
	with_drive = sum(1 for f in feats if f['properties'].get('min_drive_s') is not None); \
	dpct = with_drive / n * 100; \
	print(f'With drive times: {with_drive} ({dpct:.1f}%)'); \
	assert dpct > 90, f'Drive coverage too low: {dpct:.1f}%'; \
	munis = set(f['properties'].get('municipality_id') for f in feats if f['properties'].get('municipality_id')); \
	print(f'Municipalities: {len(munis)}'); \
	assert len(munis) > 2000, f'Too few municipalities: {len(munis)}'; \
	print('All checks passed.'); \
	"

# ── Clean ──

clean:
	rm -f $(PROCESSED_DIR)/settlement_travel_times_pt_run*.json
	@echo "Cleaned intermediate files."
