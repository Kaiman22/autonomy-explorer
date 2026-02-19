# OpenClaw Scraping Task: Swiss Municipality Property Prices

## Goal

Scrape real estate prices (CHF per square meter) for 2,128 Swiss municipalities from **Homegate.ch** and/or **ImmoScout24.ch**. Output a single JSON file that I can feed into my data pipeline.

## Input

The file `municipality_list.json` (in this same folder) contains all 2,128 municipalities with this structure:

```json
[
  {"id": "0001", "name": "Aeugst am Albis", "slug": "aeugst-am-albis"},
  {"id": "0261", "name": "Zürich", "slug": "zuerich"},
  ...
]
```

## URL Patterns

For each municipality, visit **one or both** of these URLs:

- **Homegate**: `https://www.homegate.ch/en/property-prices-m2/city-{slug}`
- **ImmoScout24**: `https://www.immoscout24.ch/en/property-prices-m2/city-{slug}`

Example for Zürich:
- `https://www.homegate.ch/en/property-prices-m2/city-zuerich`
- `https://www.immoscout24.ch/en/property-prices-m2/city-zuerich`

## What to Extract

On each page, look for **property price data** displayed in CHF/m². The page typically shows:

1. **Median price CHF/m²** for apartments (buying) - THIS IS THE MOST IMPORTANT VALUE
2. Median price CHF/m² for houses (buying) - secondary
3. Price range (min-max) if shown
4. Median rent CHF/m² for apartments (renting) - nice to have
5. Median rent CHF/m² for houses (renting) - nice to have

The data is typically displayed as large numbers on the page, often in a card/panel layout, e.g. "CHF 12,500 /m²".

There may also be a `window.__INITIAL_STATE__` JSON object embedded in the page source (inside a `<script>` tag) that contains this data in structured form. If you can extract that, even better - look for paths like:
- `pages.propertyPricesPerM2.data.buy.apartment.median`
- `pages.propertyPricesPerM2.data.buy.house.median`
- `pages.propertyPricesPerM2.data.rent.apartment.median`

## Output Format

Save results to `prices_scraped.json` in this folder. Use this exact structure:

```json
{
  "0001": {
    "source": "homegate",
    "buy_apartment_chf_m2": 12500,
    "buy_house_chf_m2": 10200,
    "rent_apartment_chf_m2": 280,
    "rent_house_chf_m2": 220,
    "buy_apartment_min": 9000,
    "buy_apartment_max": 16000
  },
  "0261": {
    "source": "immoscout24",
    "buy_apartment_chf_m2": 15800,
    "buy_house_chf_m2": null,
    "rent_apartment_chf_m2": 380,
    "rent_house_chf_m2": null,
    "buy_apartment_min": 10000,
    "buy_apartment_max": 22000
  }
}
```

Rules for the output:
- Use the municipality **id** (e.g. "0261") as the key, NOT the name or slug
- Use `null` for any value that's not available on the page
- The `buy_apartment_chf_m2` field is the most critical one - prioritize getting this
- If a page returns 404 or has no data, skip that municipality (don't include it in the JSON)
- Include `"source": "homegate"` or `"source": "immoscout24"` to track where each price came from
- If you get data from both sources for the same municipality, prefer Homegate (or include both with keys like `"source": "both"` and `"homegate_buy_apartment_chf_m2"` / `"immoscout24_buy_apartment_chf_m2"`)

## Scraping Strategy

1. **Start with Homegate** for all 2,128 municipalities
2. For any municipalities where Homegate returned 404/no data, **retry with ImmoScout24**
3. Be polite: wait **3-5 seconds between page loads** to avoid rate limiting
4. If you hit a captcha or block, pause for 2 minutes, then continue
5. Process in batches of ~100, saving progress after each batch (append to the JSON)
6. **Save progress frequently** - if the process crashes, you should be able to resume from the last saved batch

## Handling Edge Cases

- Some slugs might not match exactly. If you get a 404, try variations:
  - `st-gallen` vs `saint-gallen`
  - `biel-bienne` vs `biel` vs `bienne`
  - Remove parenthetical suffixes: `benken-zh` vs `benken`
- If a municipality page loads but shows no price data (just an empty template), record it as attempted but skip it in the output
- Cookie/consent banners: dismiss them (click "Accept" or "Reject all") so they don't block content

## Progress Tracking

Create a `scraping_progress.json` file that tracks:

```json
{
  "total": 2128,
  "attempted": 450,
  "found": 380,
  "not_found": 70,
  "last_id": "0450",
  "last_updated": "2026-02-19T15:30:00"
}
```

Update this after each batch so you (or I) can see progress and resume if needed.

## Resume Support

If `prices_scraped.json` already exists with some entries, skip those municipalities and only scrape the ones not yet in the file. This lets you resume after interruptions.

## Final Notes

- The total should take roughly 3-6 hours at 3-5 seconds per page
- I need at least the `buy_apartment_chf_m2` for the majority of municipalities to be useful
- Rent data is a bonus - nice to have but not critical
- Don't worry about perfect coverage - even 1,500/2,128 with real prices would be a huge improvement over what we have now
