# Sleeper Towns 🚗💤

**Where self-driving cars wake up Swiss property values.**

An interactive map of Switzerland's *sleeper towns* — cheap municipalities that flip
into viable commute range of a major city once autonomous vehicles make travel time
useful instead of wasted.

**Live map:** https://kaiman22.github.io/sleeper-towns/

## Why "Sleeper Towns"?

The name works three ways:

1. **Investment slang** — a "sleeper" is an undervalued asset nobody noticed yet.
2. **Swiss German** — commuter villages are literally called *Schlafgemeinden* (sleeper communities).
3. **Literally** — in an autonomous car, you can sleep, work, or read through your commute. That's the whole thesis.

## The thesis

Autonomous vehicles lower the *effective* cost of commuting: an hour in an AV where
you can work or nap is worth less lost time than an hour behind the wheel. Places
just outside today's commuter belts will move *inside* them. Property prices there
should eventually reflect that — but they don't yet.

The map quantifies this per settlement with real data:

- **Sleeper Score (m²)** — the flagship metric: capitalized AV commute gain divided
  by property price. "How many m² of property does the AV unlock pay for here?"
  High = cheap town about to enter the AV commuter belt.
- **Wake-Up Value (CHF)** — the latent property value per commuter household:
  best single *viable* AV commute gain (within a commute-tolerance, default 45 min),
  capitalized via value-of-travel-time and a cap rate.
- **AV Upside (min)** — raw minutes AV beats today's best mode (car or PT), per
  settlement, averaged over selected reference cities.

Time savings only count where the destination stays within a viable daily commute —
a 4-hour valley with huge "savings" scores zero, because nobody will commute from
there and the savings never capitalize. See [RESEARCH.md](RESEARCH.md) for the
methodology audit.

## Data

- **3,966 settlement points** across 2,069 Swiss municipalities
- **Car travel times**: Google/Geoapify routing to 10 reference cities
- **PT travel times**: SBB (transport.opendata.ch), incl. walk/wait/in-vehicle breakdown
- **Prices**: Neho hedonic estimates + Homegate listing medians; gaps filled by
  spatial interpolation (tagged "est." in the UI)
- **Taxes**: ESTV municipal multipliers

## Stack

React + Vite + MapLibre GL, static GeoJSON — no backend. Python data pipeline in
`data/scripts/`. Deployed via GitHub Pages on push to `main`.

```bash
cd frontend && npm install && npm run dev
```
