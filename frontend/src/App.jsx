import React, { useState, useEffect, useCallback, useMemo } from 'react'
import MapView from './layers/MapView'
import SidePanel from './panels/SidePanel'

const DATA_URL = './data/municipalities_scored.geojson'

// Default model parameters (can be adjusted via UI sliders)
const DEFAULT_MODEL_PARAMS = {
  avFactor: 0.70,     // AV comfort factor (0.5 = very comfortable, 1.0 = same as driving)
  ptFactor: 0.70,     // PT comfort factor (0.5 = very comfortable, 1.0 = same as driving)
}

// Walking deduction from PT times (seconds), varies by population density.
// Urban areas have nearby PT stops (short walk), rural areas don't.
// Must match config.py PT_WALK_DEDUCTION_S.
const PT_WALK_DEDUCTION = {
  "> 100'000": 180,           // 3 min — dense urban
  "50'000 bis 100'000": 240,  // 4 min
  "10'000 bis 49'999": 360,   // 6 min — small city
  "2'000 bis 9'999": 480,     // 8 min — large village
  "1'000 bis 1'999": 600,     // 10 min — village
  "100 bis 999": 720,         // 12 min — small village
}
const PT_WALK_DEFAULT = 600    // 10 min fallback

function ptWalkDeduction(popCategory) {
  return PT_WALK_DEDUCTION[popCategory] || PT_WALK_DEFAULT
}

/**
 * Recompute all metrics from raw travel times, prices —
 * accounting for which cities/custom locations are enabled, max travel time
 * constraints per ref, and the scoring weights.
 *
 * Each ref has an optional max travel time. Municipalities that exceed ANY
 * ref's max-time constraint are excluded from scoring and normalization.
 * Aggregation: bottleneck (worst ref) — accessibility = ability to reach ALL
 * targets. Adding a ref shrinks the high-accessibility region (intersection).
 */
function recomputeScores(geojson, weights, enabledCities, customLocations, refMaxTimes, modelParams) {
  if (!geojson) return null
  const { avFactor, ptFactor } = modelParams

  function parseTimes(raw) {
    return typeof raw === 'string' ? JSON.parse(raw) : raw || {}
  }

  // Combine all reference IDs (city IDs + custom location IDs) with their max time constraint
  const allRefs = [] // { id, maxMinutes }
  enabledCities.forEach((id) => {
    allRefs.push({ id, maxMinutes: refMaxTimes[id] ?? null }) // null = no limit
  })
  if (customLocations) {
    customLocations.forEach((loc) => {
      if (loc.id && loc.enabled) {
        allRefs.push({ id: loc.id, maxMinutes: refMaxTimes[loc.id] ?? null })
      }
    })
  }
  const allRefIds = allRefs.map((r) => r.id)

  // First pass: collect raw values for normalization
  const rawStatusQuo = []       // status-quo accessibility (lower = better today)
  const rawPostAV = []          // post-AV accessibility (lower = better with AV)
  const rawDelta = []           // delta = status_quo - post_AV (higher = more gain, minutes)
  const rawRelGain = []         // relative gain = (sq - av) / sq * 100 (% improvement)
  const excluded = []           // true if municipality violates any max-time constraint

  // Temporary arrays for peer-group benchmarking
  const sqPricePairs = []       // { index, sq, price } for municipalities with both values

  geojson.features.forEach((f, i) => {
    const p = f.properties
    const driveTimes = parseTimes(p.drive_times)
    const ptTimes = parseTimes(p.pt_times)

    // --- Check max-time constraints ---
    // Uses RAW travel times (no comfort weighting) — the user's question is
    // "can I physically reach this city within X hours?" not "does it feel like X hours?"
    // PT walk deduction is applied consistently (same as in scoring) so
    // constraints and scores don't contradict each other.
    let isExcluded = false
    for (const ref of allRefs) {
      if (ref.maxMinutes == null) continue // no limit set
      const driveS = driveTimes[ref.id]
      const rawPtS = ptTimes[ref.id]
      const walkDed = ptWalkDeduction(p.pop_category)
      const ptS = rawPtS != null ? Math.max(0, rawPtS - walkDed) : null

      // Raw time to this ref (best of driving or PT, no comfort factor)
      const candidates = []
      if (driveS != null) candidates.push(driveS / 60)
      if (ptS != null) candidates.push(ptS / 60)
      const bestTime = candidates.length > 0 ? Math.min(...candidates) : Infinity

      if (bestTime > ref.maxMinutes) {
        isExcluded = true
        break
      }
    }
    excluded.push(isExcluded)

    // If excluded, still compute values (for detail panel) but they won't
    // participate in normalization
    // --- Bottleneck aggregation across refs ---
    // Use MAX (worst ref) so accessibility = ability to reach ALL targets.
    // Adding a ref can only shrink the high-accessibility region (intersection),
    // not grow it (union). This matches the intuition: "I need to commute to
    // both Zürich AND Basel, so I need to be close to both."
    const sqTimes = []
    const avTimes = []
    const walkDed = ptWalkDeduction(p.pop_category)

    for (const ref of allRefs) {
      const driveS = driveTimes[ref.id]
      const rawPtS = ptTimes[ref.id]
      // Deduct walking to first PT stop (noise from centroid placement)
      const ptS = rawPtS != null ? Math.max(0, rawPtS - walkDed) : null

      // Status quo: best of manual drive or PT for this ref
      const candidates = []
      if (driveS != null) candidates.push(driveS / 60)
      if (ptS != null) candidates.push((ptS / 60) * ptFactor)
      if (candidates.length > 0) {
        sqTimes.push(Math.min(...candidates))
      }

      // Post-AV: best of AV driving or PT
      const avCandidates = []
      if (driveS != null) avCandidates.push((driveS / 60) * avFactor)
      if (ptS != null) avCandidates.push((ptS / 60) * ptFactor)
      if (avCandidates.length > 0) {
        avTimes.push(Math.min(...avCandidates))
      }
    }

    // Bottleneck: worst (max) time across all refs
    const sq = sqTimes.length > 0 ? Math.max(...sqTimes) : null
    rawStatusQuo.push(sq)

    const av = avTimes.length > 0 ? Math.max(...avTimes) : null
    rawPostAV.push(av)

    // Delta: how much AV improves the bottleneck (absolute minutes)
    if (sq != null && av != null) {
      rawDelta.push(sq - av)
    } else {
      rawDelta.push(null)
    }

    // Relative gain: what % of current commute pain does AV eliminate?
    // This doesn't bias towards remote areas like absolute delta does.
    if (sq != null && av != null && sq > 0) {
      rawRelGain.push(((sq - av) / sq) * 100)
    } else {
      rawRelGain.push(null)
    }

    // Collect pairs for peer-group benchmarking
    const price = p.chf_per_m2
    if (price != null && sq != null && price > 0) {
      sqPricePairs.push({ index: i, sq, price })
    }
  })

  // --- Inherent attractiveness via peer-group price percentile ---
  // For each municipality: find all places with similar status-quo accessibility
  // (within ±15% of commute time), then ask "what % of those peers are cheaper?"
  //
  // High percentile = most peers with similar commute are cheaper = this place is
  // expensive for its accessibility level → inherently desirable (St. Moritz, Gstaad).
  // People pay a premium NOT for the commute but for nature, prestige, culture.
  //
  // Attractiveness = percentile (expensive among peers = inherently desirable).
  const rawAttractiveness = new Array(geojson.features.length).fill(null)
  const pricePercentile = new Array(geojson.features.length).fill(null)

  if (sqPricePairs.length > 10) {
    // Sort by sq for efficient windowing
    const sorted = [...sqPricePairs].sort((a, b) => a.sq - b.sq)

    for (const entry of sqPricePairs) {
      // Find peers: places with similar status-quo accessibility.
      // Adaptive bandwidth: start at ±15% (min ±5 min), widen if too few peers.
      // This ensures remote areas (few peers) still get a meaningful percentile.
      let peerPrices = []
      let pct = 0.15
      while (pct <= 0.50) {
        const margin = Math.max(5, entry.sq * pct)
        const lo = entry.sq - margin
        const hi = entry.sq + margin
        peerPrices = []
        for (const peer of sorted) {
          if (peer.sq < lo) continue
          if (peer.sq > hi) break
          peerPrices.push(peer.price)
        }
        if (peerPrices.length >= 10) break  // enough peers
        pct += 0.10  // widen
      }

      if (peerPrices.length >= 5) {
        const cheaper = peerPrices.filter((p) => p < entry.price).length
        const pctile = (cheaper / peerPrices.length) * 100
        pricePercentile[entry.index] = Math.round(pctile)
        rawAttractiveness[entry.index] = pctile
      }
    }
  }

  // Normalize helper — only uses non-excluded municipalities for min/max range
  // When all values are equal (range=0), returns 50 for all (matches backend).
  function normalize(values) {
    const valid = values.filter((v, i) => v !== null && !excluded[i])
    if (valid.length === 0) return values.map(() => null)
    const lo = Math.min(...valid)
    const hi = Math.max(...valid)
    if (hi === lo) {
      return values.map((v, i) => v !== null && !excluded[i] ? 50 : null)
    }
    const range = hi - lo
    return values.map((v, i) =>
      v !== null && !excluded[i] ? Math.round(((v - lo) / range) * 1000) / 10 : null
    )
  }

  // Normalize for inverted metrics (lower raw = better = higher score)
  function normalizeInverted(values) {
    const valid = values.filter((v, i) => v !== null && !excluded[i])
    if (valid.length === 0) return values.map(() => null)
    const lo = Math.min(...valid)
    const hi = Math.max(...valid)
    if (hi === lo) {
      return values.map((v, i) => v !== null && !excluded[i] ? 50 : null)
    }
    const range = hi - lo
    return values.map((v, i) =>
      v !== null && !excluded[i] ? Math.round(((hi - v) / range) * 1000) / 10 : null
    )
  }

  const normDelta = normalize(rawDelta)             // higher abs delta = higher score
  const normRelGain = normalize(rawRelGain)          // higher % gain = higher score
  const normAttract = normalize(rawAttractiveness)   // higher attract = higher score

  // Normalize SQ and post-AV on the SAME scale so they're visually comparable.
  // Both are raw minutes (lower = better). Using a shared min/max ensures that
  // if AV improves accessibility everywhere, post-AV scores are uniformly higher.
  const allAccessValues = rawStatusQuo.concat(rawPostAV)
  const validAccess = allAccessValues.filter((v, i) => {
    const origIdx = i >= rawStatusQuo.length ? i - rawStatusQuo.length : i
    return v !== null && !excluded[origIdx]
  })
  const accessLo = validAccess.length > 0 ? Math.min(...validAccess) : 0
  const accessHi = validAccess.length > 0 ? Math.max(...validAccess) : 1
  const accessRange = accessHi - accessLo || 1

  function normalizeInvertedShared(values) {
    return values.map((v, i) =>
      v !== null && !excluded[i] ? Math.round(((accessHi - v) / accessRange) * 1000) / 10 : null
    )
  }

  const normSQ = normalizeInvertedShared(rawStatusQuo)       // shared scale
  const normPostAV = normalizeInvertedShared(rawPostAV)      // shared scale

  // Second pass: build enriched features
  const features = geojson.features.map((f, i) => {
    const p = f.properties
    const driveTimes = parseTimes(p.drive_times)
    const ptTimes = parseTimes(p.pt_times)

    const scoreRelGain = normRelGain[i]  // relative gain — used in compound
    const scoreAbsDelta = normDelta[i]   // absolute delta — separate visualization
    const scoreAttract = normAttract[i]

    // Two compound scores: one with relative gain (%), one with absolute (min)
    // Require price data (compound depends on inherent attractiveness which needs price).
    const hasPrice = p.chf_per_m2 != null

    let scoreRel = null
    let scoreAbs = null
    if (hasPrice) {
      const relComps = []
      if (scoreRelGain !== null) relComps.push({ v: scoreRelGain, w: weights.accessibility_gain })
      if (scoreAttract !== null) relComps.push({ v: scoreAttract, w: weights.inherent_attractiveness })
      if (relComps.length > 0) {
        const tw = relComps.reduce((s, c) => s + c.w, 0)
        if (tw > 0) scoreRel = Math.round((relComps.reduce((s, c) => s + c.v * c.w, 0) / tw) * 10) / 10
      }

      const absComps = []
      if (scoreAbsDelta !== null) absComps.push({ v: scoreAbsDelta, w: weights.accessibility_gain })
      if (scoreAttract !== null) absComps.push({ v: scoreAttract, w: weights.inherent_attractiveness })
      if (absComps.length > 0) {
        const tw = absComps.reduce((s, c) => s + c.w, 0)
        if (tw > 0) scoreAbs = Math.round((absComps.reduce((s, c) => s + c.v * c.w, 0) / tw) * 10) / 10
      }
    }

    // Per-city gains for detail panel (all cities, not just enabled)
    const gainPerCity = {}
    const walkDedDetail = ptWalkDeduction(p.pop_category)
    for (const [refId, driveS] of Object.entries(driveTimes)) {
      const rawPtS = ptTimes[refId]
      const ptS = rawPtS != null ? Math.max(0, rawPtS - walkDedDetail) : null
      if (driveS != null && ptS != null) {
        const humanDrive = driveS / 60
        const ptComfort = (ptS / 60) * ptFactor
        const bestToday = Math.min(humanDrive, ptComfort)
        const bestPostAV = Math.min((driveS / 60) * avFactor, ptComfort)
        gainPerCity[refId] = Math.round((bestToday - bestPostAV) * 10) / 10
      }
    }

    // Min drive/pt for enabled refs only
    const enabledDrive = allRefIds
      .map((c) => driveTimes[c])
      .filter((v) => v != null)
    const enabledPt = allRefIds
      .map((c) => ptTimes[c])
      .filter((v) => v != null)

    const isExcl = excluded[i]

    return {
      ...f,
      properties: {
        ...p,
        // Exclusion flag (municipality violates a max-time constraint)
        excluded: isExcl,
        // Raw values (in minutes)
        status_quo_access: rawStatusQuo[i] != null ? Math.round(rawStatusQuo[i] * 10) / 10 : null,
        post_av_access: rawPostAV[i] != null ? Math.round(rawPostAV[i] * 10) / 10 : null,
        delta_accessibility: rawDelta[i] != null ? Math.round(rawDelta[i] * 10) / 10 : null,
        relative_gain_pct: rawRelGain[i] != null ? Math.round(rawRelGain[i] * 10) / 10 : null,
        inherent_attractiveness_raw: rawAttractiveness[i] != null ? Math.round(rawAttractiveness[i] * 10) / 10 : null,
        price_percentile: pricePercentile[i],  // "X% of similar-commute places are cheaper"
        // Normalized scores (0-100, higher = better) — null if excluded
        score_accessibility: isExcl ? null : scoreRelGain,
        score_rel_gain: isExcl ? null : scoreRelGain,
        score_abs_delta: isExcl ? null : scoreAbsDelta,
        score_attractiveness: isExcl ? null : scoreAttract,
        score_status_quo: isExcl ? null : normSQ[i],
        score_post_av: isExcl ? null : normPostAV[i],
        score_delta: isExcl ? null : normDelta[i],
        // Per-city detail
        gain_per_city: gainPerCity,
        min_drive_s: enabledDrive.length ? Math.min(...enabledDrive) : p.min_drive_s,
        min_pt_s: enabledPt.length ? Math.min(...enabledPt) : p.min_pt_s,
        // Final combined scores — null if excluded
        autonomy_score_rel: isExcl ? null : scoreRel,
        autonomy_score_abs: isExcl ? null : scoreAbs,
      },
    }
  })

  return { ...geojson, features }
}

// --- URL state helpers ---
function getUrlState() {
  try {
    const params = new URLSearchParams(window.location.search)
    const state = {}
    if (params.get('colorBy')) state.colorBy = params.get('colorBy')
    if (params.get('cities')) state.enabledCities = params.get('cities').split(',')
    if (params.get('avFactor')) state.avFactor = parseFloat(params.get('avFactor'))
    if (params.get('ptFactor')) state.ptFactor = parseFloat(params.get('ptFactor'))
    if (params.get('wGain')) state.wGain = parseFloat(params.get('wGain'))
    if (params.get('wAttract')) state.wAttract = parseFloat(params.get('wAttract'))
    return state
  } catch { return {} }
}

function setUrlState(state) {
  try {
    const params = new URLSearchParams()
    if (state.colorBy && state.colorBy !== 'autonomy_score_rel') params.set('colorBy', state.colorBy)
    if (state.enabledCities) params.set('cities', state.enabledCities.join(','))
    if (state.avFactor != null && state.avFactor !== DEFAULT_MODEL_PARAMS.avFactor) params.set('avFactor', state.avFactor.toFixed(2))
    if (state.ptFactor != null && state.ptFactor !== DEFAULT_MODEL_PARAMS.ptFactor) params.set('ptFactor', state.ptFactor.toFixed(2))
    if (state.wGain != null && state.wGain !== 0.5) params.set('wGain', state.wGain.toFixed(2))
    if (state.wAttract != null && state.wAttract !== 0.5) params.set('wAttract', state.wAttract.toFixed(2))
    const str = params.toString()
    const newUrl = str ? `${window.location.pathname}?${str}` : window.location.pathname
    window.history.replaceState(null, '', newUrl)
  } catch { /* ignore */ }
}

export default function App() {
  const urlState = useMemo(() => getUrlState(), [])

  const [rawData, setRawData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [hovered, setHovered] = useState(null)
  const [colorBy, setColorBy] = useState(urlState.colorBy || 'autonomy_score_rel')
  const [filterCity, setFilterCity] = useState('best')
  const [weights, setWeights] = useState({
    accessibility_gain: urlState.wGain ?? 0.5,
    inherent_attractiveness: urlState.wAttract ?? 0.5,
  })

  // All available cities from metadata, and which are enabled
  const [allCities, setAllCities] = useState({})
  const [enabledCities, setEnabledCities] = useState([])

  // Custom reference locations (user-added addresses)
  const [customLocations, setCustomLocations] = useState([])

  // Per-reference max travel time in minutes (refId → minutes, null = no limit)
  const [refMaxTimes, setRefMaxTimes] = useState({})

  // Model parameters (comfort factors etc.)
  const [modelParams, setModelParams] = useState({
    avFactor: urlState.avFactor ?? DEFAULT_MODEL_PARAMS.avFactor,
    ptFactor: urlState.ptFactor ?? DEFAULT_MODEL_PARAMS.ptFactor,
  })

  // Sync state → URL (debounced)
  useEffect(() => {
    const t = setTimeout(() => {
      setUrlState({
        colorBy,
        enabledCities,
        avFactor: modelParams.avFactor,
        ptFactor: modelParams.ptFactor,
        wGain: weights.accessibility_gain,
        wAttract: weights.inherent_attractiveness,
      })
    }, 300)
    return () => clearTimeout(t)
  }, [colorBy, enabledCities, modelParams, weights])

  useEffect(() => {
    fetch(DATA_URL)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}: Failed to load data`)
        return r.json()
      })
      .then((geojson) => {
        setRawData(geojson)
        const cities = geojson.metadata?.cities || {}
        setAllCities(cities)
        // Use URL state cities if valid, otherwise enable all
        if (urlState.enabledCities && urlState.enabledCities.every(c => c in cities)) {
          setEnabledCities(urlState.enabledCities)
        } else {
          setEnabledCities(Object.keys(cities))
        }
        setLoading(false)
      })
      .catch((err) => {
        console.error('Failed to load data:', err)
        setLoadError(err.message)
        setLoading(false)
      })
  }, [])

  // Add a custom reference location
  const addCustomLocation = useCallback((location) => {
    // location: { id, name, lat, lon, enabled }
    setCustomLocations((prev) => [...prev, { ...location, enabled: true }])

    // Compute travel times for this custom location to all municipalities
    // For now use haversine estimate; later can use OSRM API
    setRawData((prevData) => {
      if (!prevData) return prevData
      const newFeatures = prevData.features.map((f) => {
        const p = f.properties
        const driveTimes = typeof p.drive_times === 'string' ? JSON.parse(p.drive_times) : { ...p.drive_times }
        const ptTimes = typeof p.pt_times === 'string' ? JSON.parse(p.pt_times) : { ...p.pt_times }

        // Haversine distance estimate → drive time
        const coords = f.geometry.coordinates // [lon, lat]
        const R = 6371
        const dLat = ((location.lat - coords[1]) * Math.PI) / 180
        const dLon = ((location.lon - coords[0]) * Math.PI) / 180
        const a =
          Math.sin(dLat / 2) ** 2 +
          Math.cos((coords[1] * Math.PI) / 180) *
            Math.cos((location.lat * Math.PI) / 180) *
            Math.sin(dLon / 2) ** 2
        const dist = R * 2 * Math.asin(Math.sqrt(a))

        // Estimate driving time: ~70 km/h avg for Swiss roads
        const driveSec = Math.round((dist / 70) * 3600)
        // Estimate PT: 1.3-1.8x driving based on distance
        const ptRatio = dist < 30 ? 1.2 : dist < 80 ? 1.4 : 1.7
        const ptSec = Math.round(driveSec * ptRatio)

        driveTimes[location.id] = driveSec
        ptTimes[location.id] = ptSec

        return {
          ...f,
          properties: {
            ...p,
            drive_times: driveTimes,
            pt_times: ptTimes,
          },
        }
      })
      return { ...prevData, features: newFeatures }
    })
  }, [])

  const removeCustomLocation = useCallback((locId) => {
    setCustomLocations((prev) => prev.filter((l) => l.id !== locId))
  }, [])

  const toggleCustomLocation = useCallback((locId) => {
    setCustomLocations((prev) =>
      prev.map((l) => (l.id === locId ? { ...l, enabled: !l.enabled } : l))
    )
  }, [])

  const setRefMaxTime = useCallback((refId, minutes) => {
    setRefMaxTimes((prev) => ({ ...prev, [refId]: minutes }))
  }, [])

  // Reset everything to defaults
  const resetToDefaults = useCallback(() => {
    setColorBy('autonomy_score_rel')
    setWeights({ accessibility_gain: 0.5, inherent_attractiveness: 0.5 })
    setModelParams(DEFAULT_MODEL_PARAMS)
    setEnabledCities(Object.keys(allCities))
    setRefMaxTimes({})
    setCustomLocations([])
    setSelected(null)
    // Clear URL params
    window.history.replaceState(null, '', window.location.pathname)
  }, [allCities])

  const data = useMemo(
    () => recomputeScores(rawData, weights, enabledCities, customLocations, refMaxTimes, modelParams),
    [rawData, weights, enabledCities, customLocations, refMaxTimes, modelParams]
  )

  const handleSelect = useCallback((feature) => {
    setSelected((prev) => {
      if (!feature) return null
      return feature
    })
  }, [])

  // Keep selected feature in sync with recomputed scores
  const resolvedSelected = useMemo(() => {
    if (!selected || !data) return null
    const id = selected.properties?.id
    const fresh = data.features.find((f) => f.properties.id === id)
    return fresh || selected
  }, [selected, data])

  const handleHover = useCallback((feature, point) => {
    setHovered(feature ? { feature, point } : null)
  }, [])

  const handleSelectFromSearch = useCallback((feature) => {
    setSelected(feature)
  }, [])

  const toggleCity = useCallback((cityId) => {
    setEnabledCities((prev) => {
      if (prev.includes(cityId)) {
        return prev.filter((c) => c !== cityId)
      }
      return [...prev, cityId]
    })
  }, [])

  // Tooltip value based on current colorBy
  const tooltipValue = useMemo(() => {
    if (!hovered) return null
    const p = hovered.feature.properties
    const val = p[colorBy]
    if (val == null) return 'No data'
    // For raw minute values, show as minutes
    if (['status_quo_access', 'post_av_access', 'delta_accessibility'].includes(colorBy)) {
      return `${val.toFixed(1)} min`
    }
    if (colorBy === 'chf_per_m2') {
      return `${val.toLocaleString()} CHF/m²`
    }
    return `${val.toFixed(1)}`
  }, [hovered, colorBy])

  return (
    <div className="app">
      <div className="map-container">
        {loading && (
          <div className="loading-overlay">
            <div className="loading-spinner" />
          </div>
        )}
        {loadError && !loading && (
          <div className="loading-overlay" style={{ background: 'rgba(20,20,30,0.95)' }}>
            <div style={{ textAlign: 'center', color: '#fff', maxWidth: 400, padding: 24 }}>
              <div style={{ fontSize: 48, marginBottom: 16 }}>⚠️</div>
              <h2 style={{ marginBottom: 8 }}>Failed to load data</h2>
              <p style={{ color: 'rgba(255,255,255,0.6)', marginBottom: 16 }}>{loadError}</p>
              <button
                onClick={() => window.location.reload()}
                style={{
                  padding: '8px 24px', borderRadius: 6, border: 'none',
                  background: 'var(--accent-blue, #1976d2)', color: '#fff', cursor: 'pointer',
                  fontSize: 14,
                }}
              >
                Retry
              </button>
            </div>
          </div>
        )}
        <MapView
          data={data}
          colorBy={colorBy}
          filterCity={filterCity}
          weights={weights}
          onSelect={handleSelect}
          onHover={handleHover}
          selected={resolvedSelected}
        />
        {hovered && (
          <div
            className="map-tooltip"
            style={{
              left: hovered.point.x + 12,
              top: hovered.point.y - 12,
            }}
          >
            <div className="map-tooltip-name">
              {hovered.feature.properties.name}
              {hovered.feature.properties.settlement_name && hovered.feature.properties.settlement_name !== hovered.feature.properties.name && (
                <span style={{ opacity: 0.6, marginLeft: 4, fontSize: '0.85em' }}>
                  {hovered.feature.properties.settlement_name}
                </span>
              )}
            </div>
            <div className="map-tooltip-score">
              {tooltipValue}
            </div>
          </div>
        )}
      </div>
      <SidePanel
        data={data}
        selected={resolvedSelected}
        colorBy={colorBy}
        setColorBy={setColorBy}
        filterCity={filterCity}
        setFilterCity={setFilterCity}
        weights={weights}
        setWeights={setWeights}
        allCities={allCities}
        enabledCities={enabledCities}
        toggleCity={toggleCity}
        customLocations={customLocations}
        addCustomLocation={addCustomLocation}
        removeCustomLocation={removeCustomLocation}
        toggleCustomLocation={toggleCustomLocation}
        refMaxTimes={refMaxTimes}
        setRefMaxTime={setRefMaxTime}
        modelParams={modelParams}
        setModelParams={setModelParams}
        onClose={() => setSelected(null)}
        onSelectFeature={handleSelectFromSearch}
        resetToDefaults={resetToDefaults}
      />
    </div>
  )
}
